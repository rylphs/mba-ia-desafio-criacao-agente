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
