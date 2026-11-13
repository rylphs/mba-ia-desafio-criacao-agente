import inspect

from google.adk.tools.tool_confirmation import ToolConfirmation

from aurora import db
from aurora.tools.reservas import (
    TOOLS_RESERVAS, cancelar_reserva, listar_minhas_reservas, reservar_area, verificar_disponibilidade,
)
from aurora.tools.visitantes import TOOLS_VISITANTES, autorizar_visitante, listar_meus_visitantes
from tests.conftest import FakeToolContext


def _tem_reserva(apto, area, data):
    with db.conexao() as conn:
        return any(r["area"] == area and r["data"] == data for r in db.reservas_ativas(conn, apto))


def test_quadra_reserva_sem_confirmacao():
    ctx = FakeToolContext("101")
    r = reservar_area("quadra", "2030-04-06", ctx)
    assert r["status"] == "reservado" and ctx.pedidos == []


def test_salao_pede_confirmacao_e_nao_grava():
    ctx = FakeToolContext("101")
    r = reservar_area("salao-de-festas", "2030-04-20", ctx)
    assert r["status"] == "pendente_confirmacao"
    assert ctx.pedidos[0]["payload"] == {"area": "salao-de-festas", "data": "2030-04-20", "taxa": 150.0}
    assert not _tem_reserva("101", "salao-de-festas", "2030-04-20")


def test_salao_negado_nao_grava():
    ctx = FakeToolContext("101", tool_confirmation=ToolConfirmation(confirmed=False))
    assert reservar_area("salao-de-festas", "2030-04-20", ctx)["status"] == "negado"
    assert not _tem_reserva("101", "salao-de-festas", "2030-04-20")


def test_salao_confirmado_grava_uma_vez_mesmo_reexecutado():
    ctx = FakeToolContext("101", function_call_id="call-x", tool_confirmation=ToolConfirmation(confirmed=True))
    a = reservar_area("salao-de-festas", "2030-04-20", ctx)
    b = reservar_area("salao-de-festas", "2030-04-20", ctx)
    assert a["status"] == "reservado" and a["codigo"] == b["codigo"]


def test_data_ocupada_recusa_sem_pedir_confirmacao_nem_vazar():
    ctx = FakeToolContext("101")
    r = reservar_area("salao-de-festas", "2030-03-16", ctx)
    assert r["status"] == "indisponivel" and ctx.pedidos == []
    assert "RSV-4821" not in str(r) and "302" not in str(r)


def test_area_por_nome_resolve():
    assert verificar_disponibilidade("Salão de Festas", "2030-03-16") == {
        "area": "salao-de-festas", "data": "2030-03-16", "disponivel": False}


def test_data_formato_invalido():
    ctx = FakeToolContext("101")
    assert reservar_area("salao-de-festas", "20/04/2030", ctx)["status"] == "erro" and ctx.pedidos == []


def test_cancelar_reserva_alheia_nao_vaza():
    r = cancelar_reserva(FakeToolContext("101"), area="salao-de-festas", data="2030-03-16")
    assert r["status"] == "nao_encontrada" and "RSV-4821" not in str(r) and "302" not in str(r)


def test_cancelar_propria_por_area_data_sem_confirmacao():
    ctx = FakeToolContext("101")
    assert cancelar_reserva(ctx, area="quadra", data="2030-03-09")["status"] == "cancelado" and ctx.pedidos == []


def test_listar_so_do_proprio_apartamento():
    assert [r["codigo"] for r in listar_minhas_reservas(FakeToolContext("101"))["reservas"]] == ["RSV-1377"]
    assert "Marina Duarte" not in str(listar_meus_visitantes(FakeToolContext("101")))


def test_visitante_sempre_pede_confirmacao():
    ctx = FakeToolContext("101")
    r = autorizar_visitante("Joana Ribeiro", "2030-04-21", ctx)
    assert r["status"] == "pendente_confirmacao" and ctx.pedidos[0]["payload"] == {
        "nome": "Joana Ribeiro", "data": "2030-04-21"}


def test_visitante_nome_vazio():
    ctx = FakeToolContext("101")
    assert autorizar_visitante("   ", "2030-04-21", ctx)["status"] == "erro" and ctx.pedidos == []


def test_nenhuma_tool_aceita_apartamento():
    for f in TOOLS_RESERVAS + TOOLS_VISITANTES:
        assert not any("apart" in p for p in inspect.signature(f).parameters)
