"""Esquema SQLite do condomínio, carga inicial e restauração."""
import json
import secrets
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


# --- Repositório -----------------------------------------------------------

def listar_areas(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT id, nome, taxa FROM areas ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def obter_area(conn: sqlite3.Connection, area_id: str) -> dict | None:
    r = conn.execute("SELECT id, nome, taxa FROM areas WHERE id = ?", (area_id,)).fetchone()
    return dict(r) if r else None


def data_ocupada(conn: sqlite3.Connection, area_id: str, data: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND status = 'ativa'", (area_id, data)
    ).fetchone() is not None


def reservas_ativas(conn: sqlite3.Connection, apartamento: str) -> list[dict]:
    rows = conn.execute(
        "SELECT codigo, area, data FROM reservas WHERE apartamento = ? AND status = 'ativa' "
        "ORDER BY data, codigo",
        (apartamento,),
    ).fetchall()
    return [dict(r) for r in rows]


def inserir_reserva(
    conn: sqlite3.Connection, apartamento: str, area_id: str, data: str, origem: str | None
) -> dict:
    """Reserva com exclusividade garantida pelo índice único no instante da gravação."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if origem is not None:
            ja = conn.execute(
                "SELECT codigo, area, data FROM reservas WHERE origem = ?", (origem,)
            ).fetchone()
            if ja:
                conn.execute("COMMIT")
                return {"status": "reservado", "codigo": ja["codigo"], "area": ja["area"], "data": ja["data"]}
        for _ in range(5):
            codigo = "RSV-" + secrets.token_hex(3).upper()
            try:
                conn.execute(
                    "INSERT INTO reservas(codigo, apartamento, area, data, status, origem) "
                    "VALUES (?, ?, ?, ?, 'ativa', ?)",
                    (codigo, apartamento, area_id, data, origem),
                )
            except sqlite3.IntegrityError as e:
                msg = str(e)
                if "reservas.area" in msg:
                    conn.execute("ROLLBACK")
                    return {"status": "indisponivel", "area": area_id, "data": data}
                if "reservas.codigo" in msg:
                    continue
                raise
            conn.execute("COMMIT")
            return {"status": "reservado", "codigo": codigo, "area": area_id, "data": data}
        raise RuntimeError("não foi possível gerar um código de reserva único")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def cancelar_reserva(
    conn: sqlite3.Connection,
    apartamento: str,
    *,
    codigo: str | None = None,
    area_id: str | None = None,
    data: str | None = None,
) -> dict | None:
    if codigo is not None:
        filtro, args = "codigo = ?", (codigo,)
    elif area_id is not None and data is not None:
        filtro, args = "area = ? AND data = ?", (area_id, data)
    else:
        return None
    conn.execute("BEGIN IMMEDIATE")
    try:
        r = conn.execute(
            f"SELECT codigo, area, data FROM reservas WHERE apartamento = ? AND status = 'ativa' AND {filtro}",
            (apartamento, *args),
        ).fetchone()
        if r:
            conn.execute("UPDATE reservas SET status = 'cancelada' WHERE codigo = ?", (r["codigo"],))
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return dict(r) if r else None


def visitantes_do_apartamento(conn: sqlite3.Connection, apartamento: str) -> list[dict]:
    rows = conn.execute(
        "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data, nome", (apartamento,)
    ).fetchall()
    return [dict(r) for r in rows]


def inserir_visitante(
    conn: sqlite3.Connection, apartamento: str, nome: str, data: str, origem: str | None
) -> dict:
    conn.execute("BEGIN IMMEDIATE")
    try:
        ja = None
        if origem is not None:
            ja = conn.execute("SELECT nome, data FROM visitantes WHERE origem = ?", (origem,)).fetchone()
        if ja:
            nome, data = ja["nome"], ja["data"]
        else:
            conn.execute(
                "INSERT INTO visitantes(apartamento, nome, data, origem) VALUES (?, ?, ?, ?)",
                (apartamento, nome, data, origem),
            )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return {"status": "autorizado", "nome": nome, "data": data}


def registrar_sessao(conn: sqlite3.Connection, session_id: str, apartamento: str) -> None:
    conn.execute(
        "INSERT INTO sessoes(session_id, apartamento) VALUES (?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET apartamento = excluded.apartamento",
        (session_id, apartamento),
    )


def apartamento_da_sessao(conn: sqlite3.Connection, session_id: str) -> str | None:
    r = conn.execute("SELECT apartamento FROM sessoes WHERE session_id = ?", (session_id,)).fetchone()
    return r["apartamento"] if r else None
