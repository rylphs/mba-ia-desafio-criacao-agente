"""Esquema SQLite do condomínio, carga inicial e restauração."""
import json
import sqlite3
from contextlib import contextmanager

from aurora import config

ESQUEMA = """
CREATE TABLE IF NOT EXISTS apartamentos(numero TEXT PRIMARY KEY, morador TEXT);
CREATE TABLE IF NOT EXISTS areas(id TEXT PRIMARY KEY, nome TEXT, taxa REAL);
CREATE TABLE IF NOT EXISTS reservas(
  codigo TEXT PRIMARY KEY,
  apartamento TEXT NOT NULL,
  area TEXT NOT NULL REFERENCES areas(id),
  data TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ativa',
  origem TEXT UNIQUE
);
CREATE UNIQUE INDEX IF NOT EXISTS reserva_unica_ativa ON reservas(area, data) WHERE status = 'ativa';
CREATE TABLE IF NOT EXISTS visitantes(
  id INTEGER PRIMARY KEY,
  apartamento TEXT NOT NULL, nome TEXT NOT NULL, data TEXT NOT NULL,
  origem TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS sessoes(session_id TEXT PRIMARY KEY, apartamento TEXT NOT NULL);
"""


@contextmanager
def conexao():
    conn = sqlite3.connect(config.condominio_db(), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
    finally:
        conn.close()


def criar_esquema(conn: sqlite3.Connection) -> None:
    for comando in ESQUEMA.split(";"):
        if comando.strip():
            conn.execute(comando)


def _json(nome: str) -> list[dict]:
    return json.loads((config.dados_dir() / nome).read_text(encoding="utf-8"))


def restaurar(conn: sqlite3.Connection) -> None:
    apartamentos = _json("apartamentos.json")
    areas = _json("areas.json")
    reservas = _json("reservas.json")
    visitantes = _json("visitantes.json")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for tabela in ("reservas", "visitantes", "areas", "apartamentos"):
            conn.execute(f"DELETE FROM {tabela}")
        conn.executemany(
            "INSERT INTO apartamentos(numero, morador) VALUES (:numero, :morador)", apartamentos
        )
        conn.executemany("INSERT INTO areas(id, nome, taxa) VALUES (:id, :nome, :taxa)", areas)
        conn.executemany(
            "INSERT INTO reservas(codigo, apartamento, area, data, status, origem) "
            "VALUES (:codigo, :apartamento, :area, :data, 'ativa', NULL)",
            reservas,
        )
        conn.executemany(
            "INSERT INTO visitantes(apartamento, nome, data) VALUES (:apartamento, :nome, :data)",
            visitantes,
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def inicializar_se_preciso() -> None:
    with conexao() as conn:
        criar_esquema(conn)
        if conn.execute("SELECT COUNT(*) FROM areas").fetchone()[0] == 0:
            restaurar(conn)
