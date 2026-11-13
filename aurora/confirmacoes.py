"""Helpers puros sobre eventos de sessão do ADK: confirmações pendentes de tools."""

from google.adk.events import Event
from google.genai import types

NOME_CONFIRMACAO = "adk_request_confirmation"


def _respostas(events: list[Event]) -> set[str]:
    return {fr.id for e in events for fr in e.get_function_responses() if fr.id}


def _confirmacoes(events: list[Event]):
    for e in events:
        for fc in e.get_function_calls():
            if fc.name == NOME_CONFIRMACAO:
                yield fc


def _original(fc) -> dict:
    return dict((fc.args or {}).get("originalFunctionCall") or {})


def _payload(fc) -> dict:
    confirmacao = (fc.args or {}).get("toolConfirmation") or {}
    return dict(confirmacao.get("payload") or {})


def listar_pendentes(events: list[Event]) -> list[dict]:
    respondidas = _respostas(events)
    pendentes = []
    for fc in _confirmacoes(events):
        if fc.id in respondidas:
            continue
        orig = _original(fc)
        pendentes.append({
            "id": fc.id,
            "acao": orig.get("name"),
            # O payload é o que a tool validou (id da área, data); os args são o que o modelo escreveu.
            "detalhes": _payload(fc) or dict(orig.get("args") or {}),
        })
    return pendentes


def chamada_original(events: list[Event], confirmacao_id: str) -> str | None:
    for fc in _confirmacoes(events):
        if fc.id == confirmacao_id:
            return _original(fc).get("id")
    return None


def tool_respondeu(events: list[Event], chamada_id: str) -> bool:
    """Diz se a chamada original teve resposta definitiva (não "pendente_confirmacao").

    No ADK a tool que pede confirmação já responde "pendente" para a chamada original
    antes da aprovação; essa resposta não conta como execução.
    """
    return any(
        fr.id == chamada_id
        and chamada_id not in e.actions.requested_tool_confirmations
        and (fr.response or {}).get("status") != "pendente_confirmacao"
        for e in events
        for fr in e.get_function_responses()
    )


def texto_resposta(events: list[Event]) -> str:
    textos = [
        p.text
        for e in events
        if e.author != "user" and not e.partial and e.content and e.content.parts
        for p in e.content.parts
        if p.text and not p.thought
    ]
    return "\n".join(textos).strip()


def mensagem_confirmacao(confirmacao_id: str, confirmado: bool) -> types.Content:
    return types.Content(
        role="user",
        parts=[types.Part(function_response=types.FunctionResponse(
            id=confirmacao_id, name=NOME_CONFIRMACAO, response={"confirmed": confirmado}))],
    )
