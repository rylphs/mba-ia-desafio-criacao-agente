import sqlite3

import pytest

from aurora import db


def test_restaurar_carrega_dados_iniciais():
    with db.conexao() as c:
        r = c.execute("select codigo, apartamento, area, data, status from reservas order by codigo").fetchall()
        assert [tuple(x) for x in r] == [
            ("RSV-1377", "101", "quadra", "2030-03-09", "ativa"),
            ("RSV-2950", "201", "churrasqueira", "2030-03-23", "ativa"),
            ("RSV-4821", "302", "salao-de-festas", "2030-03-16", "ativa")]
        assert c.execute("select count(*) from visitantes").fetchone()[0] == 2
        assert c.execute("select taxa from areas where id='quadra'").fetchone()[0] == 0


def test_restaurar_desfaz_mudancas():
    with db.conexao() as c:
        c.execute("delete from reservas where codigo='RSV-1377'")
        c.execute("insert into visitantes(apartamento,nome,data) values ('101','X','2030-01-01')")
        db.restaurar(c)
        assert c.execute("select count(*) from reservas where codigo='RSV-1377'").fetchone()[0] == 1
        assert c.execute("select count(*) from visitantes").fetchone()[0] == 2


def test_indice_impede_duas_ativas_mesma_area_data():
    with db.conexao() as c, pytest.raises(sqlite3.IntegrityError):
        c.execute("insert into reservas(codigo,apartamento,area,data) values ('RSV-X','101','salao-de-festas','2030-03-16')")


def test_indice_permite_nova_ativa_quando_anterior_cancelada():
    with db.conexao() as c:
        c.execute("update reservas set status='cancelada' where codigo='RSV-4821'")
        c.execute("insert into reservas(codigo,apartamento,area,data) values ('RSV-X','101','salao-de-festas','2030-03-16')")


import threading


def _reservar(apto, area, data, origem, barreira):
    with db.conexao() as c:
        barreira.wait(timeout=10)
        return db.inserir_reserva(c, apto, area, data, origem)


def run_in_threads(fn, n=2):
    barreira = threading.Barrier(n)
    resultados = [None] * n
    erros = []

    def alvo(i):
        try:
            resultados[i] = fn(i, barreira)
        except BaseException as e:  # noqa: BLE001
            erros.append(e)

    ts = [threading.Thread(target=alvo, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not erros, erros
    return resultados


def test_inserir_reserva_gera_codigo_novo_e_unico():
    with db.conexao() as c:
        r = db.inserir_reserva(c, "101", "quadra", "2030-04-06", "call-a")
        assert r["status"] == "reservado" and r["codigo"].startswith("RSV-")
        assert r["codigo"] not in {"RSV-1377", "RSV-4821", "RSV-2950"}


def test_inserir_reserva_idempotente_por_origem():
    with db.conexao() as c:
        a = db.inserir_reserva(c, "101", "salao-de-festas", "2030-04-20", "call-a")
        b = db.inserir_reserva(c, "101", "salao-de-festas", "2030-04-20", "call-a")
        assert a == b
        assert len([x for x in db.reservas_ativas(c, "101") if x["data"] == "2030-04-20"]) == 1


def test_inserir_reserva_data_ocupada_nao_vaza_dono():
    with db.conexao() as c:
        r = db.inserir_reserva(c, "101", "salao-de-festas", "2030-03-16", "call-b")
        assert r == {"status": "indisponivel", "area": "salao-de-festas", "data": "2030-03-16"}


def test_corrida_de_reservas_so_uma_vence():
    resultados = run_in_threads(
        lambda i, b: _reservar("101" if i == 0 else "201", "salao-de-festas", "2030-05-11", f"call-{i}", b), n=2)
    assert sorted(r["status"] for r in resultados) == ["indisponivel", "reservado"]
    with db.conexao() as c:
        assert c.execute("select count(*) from reservas where area='salao-de-festas' and data='2030-05-11' and status='ativa'").fetchone()[0] == 1


def test_cancelar_so_do_proprio_apartamento():
    with db.conexao() as c:
        assert db.cancelar_reserva(c, "101", area_id="salao-de-festas", data="2030-03-16") is None
        assert db.cancelar_reserva(c, "101", codigo="RSV-4821") is None
        assert db.cancelar_reserva(c, "101", area_id="quadra", data="2030-03-09") == {"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}
        assert db.reservas_ativas(c, "101") == []
        assert [r["codigo"] for r in db.reservas_ativas(c, "302")] == ["RSV-4821"]


def test_codigo_cancelado_nunca_reaproveitado():
    with db.conexao() as c:
        db.cancelar_reserva(c, "101", codigo="RSV-1377")
        r = db.inserir_reserva(c, "101", "quadra", "2030-03-09", "call-c")
        assert r["status"] == "reservado" and r["codigo"] != "RSV-1377"


def test_inserir_visitante_idempotente():
    with db.conexao() as c:
        db.inserir_visitante(c, "101", "Joana Ribeiro", "2030-04-21", "call-v")
        db.inserir_visitante(c, "101", "Joana Ribeiro", "2030-04-21", "call-v")
        assert db.visitantes_do_apartamento(c, "101") == [{"nome": "Joana Ribeiro", "data": "2030-04-21"}]


def test_areas_data_ocupada_e_sessao():
    with db.conexao() as c:
        assert {a["id"] for a in db.listar_areas(c)} >= {"quadra", "salao-de-festas"}
        assert db.obter_area(c, "quadra")["taxa"] == 0
        assert db.obter_area(c, "nada") is None
        assert db.data_ocupada(c, "quadra", "2030-03-09") is True
        db.cancelar_reserva(c, "101", codigo="RSV-1377")
        assert db.data_ocupada(c, "quadra", "2030-03-09") is False
        assert db.apartamento_da_sessao(c, "s1") is None
        db.registrar_sessao(c, "s1", "101")
        db.registrar_sessao(c, "s1", "201")
        assert db.apartamento_da_sessao(c, "s1") == "201"
