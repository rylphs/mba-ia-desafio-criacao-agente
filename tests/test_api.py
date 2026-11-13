import pytest
from fastapi.testclient import TestClient
from google.adk.events import Event
from google.genai import types

from aurora.agents import APP_NAME
from aurora.api import app

ORIG = {"id": "call-1", "name": "reservar_area", "args": {"area": "quadra", "data": "2030-04-06"}}


@pytest.fixture
def cliente():
    with TestClient(app) as c:
        yield c


async def _explode(**kw):
    raise AssertionError("runner não deveria ser chamado")
    yield


def _evento_pedido():
    return Event(
        invocation_id="inv-1", author="reservas",
        content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
            id="adk-1", name="adk_request_confirmation",
            args={"originalFunctionCall": ORIG, "toolConfirmation": {}}))]),
    )


def _evento_resposta():
    return Event(
        invocation_id="inv-2", author="user",
        content=types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
            id="adk-1", name="adk_request_confirmation", response={"confirmed": False}))]),
    )


def _injetar(cliente, sid, *eventos):
    svc = cliente.app.state.session_service

    async def tarefa():
        s = await svc.get_session(app_name=APP_NAME, user_id="101", session_id=sid)
        for e in eventos:
            await svc.append_event(s, e)

    cliente.portal.call(tarefa)


def _sessao(cliente):
    return cliente.post("/sessoes", json={"apartamento": "101"}).json()["session_id"]


def test_criar_sessao_201(cliente):
    r = cliente.post("/sessoes", json={"apartamento": "101"})
    assert r.status_code == 201 and r.json()["session_id"]


def test_rotas_de_verificacao(cliente):
    assert cliente.get("/apartamentos/101/reservas").json() == [{"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}]
    assert cliente.get("/apartamentos/302/visitantes").json() == [{"nome": "Marina Duarte", "data": "2030-03-16"}]


def test_404_sessao_inexistente(cliente):
    assert cliente.get("/sessoes/sessao-inexistente/eventos").status_code == 404
    assert cliente.post("/sessoes/x/mensagens", json={"texto": "oi"}).status_code == 404
    assert cliente.post("/sessoes/x/confirmacoes", json={"id": "a", "confirmado": True}).status_code == 404


def test_409_id_inexistente_nao_chama_runner(cliente, monkeypatch):
    sid = _sessao(cliente)
    monkeypatch.setattr(cliente.app.state.runner, "run_async", _explode)
    r = cliente.post(f"/sessoes/{sid}/confirmacoes", json={"id": "id-inexistente", "confirmado": True})
    assert r.status_code == 409
    assert r.json() == {"detail": "Não existe confirmação pendente com esse id nesta sessão."}


def test_409_id_ja_respondido(cliente, monkeypatch):
    sid = _sessao(cliente)
    _injetar(cliente, sid, _evento_pedido(), _evento_resposta())
    monkeypatch.setattr(cliente.app.state.runner, "run_async", _explode)
    r = cliente.post(f"/sessoes/{sid}/confirmacoes", json={"id": "adk-1", "confirmado": True})
    assert r.status_code == 409


def test_pos_checagem_runner_silencioso_da_500(cliente, monkeypatch):
    sid = _sessao(cliente)
    _injetar(cliente, sid, _evento_pedido())
    chamadas = []

    async def runner_mudo(**kw):
        chamadas.append(kw)
        return
        yield

    monkeypatch.setattr(cliente.app.state.runner, "run_async", runner_mudo)
    assert cliente.post(f"/sessoes/{sid}/confirmacoes", json={"id": "adk-9", "confirmado": True}).status_code == 409
    assert chamadas == []
    r = cliente.post(f"/sessoes/{sid}/confirmacoes", json={"id": "adk-1", "confirmado": True})
    assert r.status_code == 500 and len(chamadas) == 1
    assert r.json() == {"detail": "A confirmação foi recebida, mas a ação não foi executada."}


def test_negar_nao_exige_execucao_da_tool(cliente, monkeypatch):
    sid = _sessao(cliente)
    _injetar(cliente, sid, _evento_pedido())

    async def runner_mudo(**kw):
        return
        yield

    monkeypatch.setattr(cliente.app.state.runner, "run_async", runner_mudo)
    r = cliente.post(f"/sessoes/{sid}/confirmacoes", json={"id": "adk-1", "confirmado": False})
    assert r.status_code == 200
    assert r.json()["confirmacoes_pendentes"][0]["id"] == "adk-1"


def test_mensagem_devolve_resposta(cliente, monkeypatch):
    sid = _sessao(cliente)

    async def runner_falso(**kw):
        assert kw["user_id"] == "101" and kw["session_id"] == sid
        yield Event(invocation_id="i", author="aurora",
                    content=types.Content(role="model", parts=[types.Part(text="Olá!")]))

    monkeypatch.setattr(cliente.app.state.runner, "run_async", runner_falso)
    r = cliente.post(f"/sessoes/{sid}/mensagens", json={"texto": "oi"})
    assert r.status_code == 200
    assert r.json() == {"resposta": "Olá!", "confirmacoes_pendentes": []}


def test_erro_do_modelo_da_502(cliente, monkeypatch):
    from google.genai.errors import APIError
    sid = _sessao(cliente)

    async def runner_falha(**kw):
        raise APIError(503, {"error": {"message": "indisponível"}})
        yield

    monkeypatch.setattr(cliente.app.state.runner, "run_async", runner_falha)
    r = cliente.post(f"/sessoes/{sid}/mensagens", json={"texto": "oi"})
    assert r.status_code == 502


def test_eventos_lista_vazia_em_sessao_nova(cliente):
    sid = _sessao(cliente)
    assert cliente.get(f"/sessoes/{sid}/eventos").json() == []


def test_eventos_serializados(cliente):
    sid = _sessao(cliente)
    _injetar(cliente, sid, _evento_pedido())
    ev = cliente.get(f"/sessoes/{sid}/eventos").json()
    assert len(ev) == 1 and ev[0]["author"] == "reservas"
