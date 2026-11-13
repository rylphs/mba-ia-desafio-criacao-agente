"""Utilidades compartilhadas pelas tools."""
import re
import sqlite3
import unicodedata
from datetime import date

from aurora import db

_PADRAO_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def apartamento_do_contexto(tool_context) -> str:
    try:
        return tool_context.state["apartamento"]
    except KeyError as e:
        raise RuntimeError("sessão sem apartamento") from e


def validar_data(data: str) -> str | None:
    if not isinstance(data, str) or not _PADRAO_DATA.match(data):
        return None
    try:
        date.fromisoformat(data)
    except ValueError:
        return None
    return data


def _normalizar(texto: str) -> str:
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )
    return re.sub(r"[\s\-_]+", " ", sem_acento).strip().casefold()


def resolver_area(conn: sqlite3.Connection, valor: str) -> dict | None:
    alvo = _normalizar(valor or "")
    if not alvo:
        return None
    for area in db.listar_areas(conn):
        if alvo in (_normalizar(area["id"]), _normalizar(area["nome"])):
            return area
    return None


def erro(mensagem: str) -> dict:
    return {"status": "erro", "mensagem": mensagem}
