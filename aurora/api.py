"""API HTTP do assistente Residencial Aurora (contrato do desafio)."""
import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types
from google.genai.errors import APIError
from pydantic import BaseModel

from aurora import config, confirmacoes, db
from aurora.agents import APP_NAME, criar_app


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.inicializar_se_preciso()
    app.state.session_service = DatabaseSessionService(db_url=config.sessoes_db_url())
    app.state.runner = Runner(app=criar_app(), session_service=app.state.session_service)
    app.state.locks = defaultdict(asyncio.Lock)
    yield
    await app.state.session_service.close()


app = FastAPI(title="Residencial Aurora", lifespan=lifespan)


class NovaSessao(BaseModel):
    apartamento: str


class Mensagem(BaseModel):
    texto: str


class Confirmacao(BaseModel):
    id: str
    confirmado: bool


async def _carregar(request: Request, session_id: str):
    with db.conexao() as conn:
        apartamento = db.apartamento_da_sessao(conn, session_id)
    if apartamento is None:
        raise HTTPException(404, "Sessão não encontrada.")
    session = await request.app.state.session_service.get_session(
        app_name=APP_NAME, user_id=apartamento, session_id=session_id)
    if session is None:
        raise HTTPException(404, "Sessão não encontrada.")
    return apartamento, session


async def _executar(request: Request, apartamento: str, session_id: str,
                    content: types.Content, eventos: list[Event] | None = None) -> list[Event]:
    eventos = [] if eventos is None else eventos
    try:
        async for e in request.app.state.runner.run_async(
                user_id=apartamento, session_id=session_id, new_message=content):
            eventos.append(e)
    except APIError as exc:
        raise HTTPException(502, "Modelo indisponível, tente novamente.") from exc
    return eventos


async def _resposta(request: Request, session_id: str, apartamento: str,
                    eventos: list[Event]) -> dict:
    session = await request.app.state.session_service.get_session(
        app_name=APP_NAME, user_id=apartamento, session_id=session_id)
    return {
        "resposta": confirmacoes.texto_resposta(eventos),
        "confirmacoes_pendentes": confirmacoes.listar_pendentes(session.events),
    }


@app.post("/sessoes", status_code=201)
async def criar_sessao(corpo: NovaSessao, request: Request):
    session = await request.app.state.session_service.create_session(
        app_name=APP_NAME, user_id=corpo.apartamento, state={"apartamento": corpo.apartamento})
    with db.conexao() as conn:
        db.registrar_sessao(conn, session.id, corpo.apartamento)
    return {"session_id": session.id}


@app.post("/sessoes/{session_id}/mensagens")
async def enviar_mensagem(session_id: str, corpo: Mensagem, request: Request):
    async with request.app.state.locks[session_id]:
        apartamento, _ = await _carregar(request, session_id)
        eventos = await _executar(
            request, apartamento, session_id,
            types.Content(role="user", parts=[types.Part(text=corpo.texto)]))
        return await _resposta(request, session_id, apartamento, eventos)


@app.post("/sessoes/{session_id}/confirmacoes")
async def responder_confirmacao(session_id: str, corpo: Confirmacao, request: Request):
    async with request.app.state.locks[session_id]:
        apartamento, session = await _carregar(request, session_id)
        if corpo.id not in {p["id"] for p in confirmacoes.listar_pendentes(session.events)}:
            raise HTTPException(409, "Não existe confirmação pendente com esse id nesta sessão.")
        original = confirmacoes.chamada_original(session.events, corpo.id)
        eventos: list[Event] = []
        try:
            await _executar(request, apartamento, session_id,
                            confirmacoes.mensagem_confirmacao(corpo.id, corpo.confirmado), eventos)
        except HTTPException:
            # Falha do modelo depois de a ação aprovada já ter rodado não vira erro.
            if not (corpo.confirmado and confirmacoes.tool_respondeu(eventos, original)):
                raise
        if corpo.confirmado and not confirmacoes.tool_respondeu(eventos, original):
            raise HTTPException(500, "A confirmação foi recebida, mas a ação não foi executada.")
        return await _resposta(request, session_id, apartamento, eventos)


@app.get("/sessoes/{session_id}/eventos")
async def listar_eventos(session_id: str, request: Request):
    _, session = await _carregar(request, session_id)
    return [e.model_dump(mode="json", by_alias=True, exclude_none=True) for e in session.events]


@app.get("/apartamentos/{numero}/reservas")
def reservas(numero: str):
    with db.conexao() as conn:
        return db.reservas_ativas(conn, numero)


@app.get("/apartamentos/{numero}/visitantes")
def visitantes(numero: str):
    with db.conexao() as conn:
        return db.visitantes_do_apartamento(conn, numero)


def main() -> None:
    uvicorn.run("aurora.api:app", host="0.0.0.0", port=8000)
