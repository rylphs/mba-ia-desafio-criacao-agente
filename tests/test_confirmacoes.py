from google.adk.events import Event
from google.genai import types

from aurora.confirmacoes import (
    NOME_CONFIRMACAO,
    chamada_original,
    listar_pendentes,
    mensagem_confirmacao,
    texto_resposta,
    tool_respondeu,
)

ORIG = {"id": "call-1", "name": "reservar_area", "args": {"area": "salao-de-festas", "data": "2030-04-20"}}


def ev_fc(author, name, id, args):
    part = types.Part(function_call=types.FunctionCall(id=id, name=name, args=args))
    return Event(author=author, content=types.Content(role="model", parts=[part]))


def ev_fr(author, name, id, response):
    part = types.Part(function_response=types.FunctionResponse(id=id, name=name, response=response))
    role = "user" if author == "user" else "model"
    return Event(author=author, content=types.Content(role=role, parts=[part]))


def ev_text(author, text, thought=False, partial=None):
    role = "user" if author == "user" else "model"
    part = types.Part(text=text, thought=thought or None)
    return Event(author=author, partial=partial, content=types.Content(role=role, parts=[part]))


def test_pendente_aparece_com_detalhes():
    evs = [ev_fc("reservas", "adk_request_confirmation", "adk-1", {"originalFunctionCall": ORIG, "toolConfirmation": {"hint": "x"}})]
    assert listar_pendentes(evs) == [{"id": "adk-1", "acao": "reservar_area", "detalhes": {"area": "salao-de-festas", "data": "2030-04-20"}}]
    assert chamada_original(evs, "adk-1") == "call-1"
    assert chamada_original(evs, "outro") is None


def test_respondida_sai_da_lista_aprovada_ou_negada():
    for ok in (True, False):
        evs = [ev_fc("reservas", "adk_request_confirmation", "adk-1", {"originalFunctionCall": ORIG, "toolConfirmation": {}}),
               ev_fr("user", "adk_request_confirmation", "adk-1", {"confirmed": ok})]
        assert listar_pendentes(evs) == []


def test_tool_respondeu():
    evs = [ev_fr("reservas", "reservar_area", "call-1", {"status": "indisponivel"})]
    assert tool_respondeu(evs, "call-1") and not tool_respondeu(evs, "call-2")


def test_texto_resposta_ignora_usuario_e_thought():
    evs = [ev_text("user", "oi"), ev_text("reservas", "pensando", thought=True), ev_text("reservas", "Reservado.")]
    assert texto_resposta(evs) == "Reservado."
    assert texto_resposta([]) == ""


def test_texto_resposta_ignora_parciais_e_une_linhas():
    evs = [ev_text("a", "parcial", partial=True), ev_text("a", " um"), ev_text("b", "dois ")]
    assert texto_resposta(evs) == "um\ndois"


def test_mensagem_confirmacao_formato():
    msg = mensagem_confirmacao("adk-1", False)
    assert msg.role == "user"
    fr = msg.parts[0].function_response
    assert (fr.id, fr.name, fr.response) == ("adk-1", NOME_CONFIRMACAO, {"confirmed": False})
