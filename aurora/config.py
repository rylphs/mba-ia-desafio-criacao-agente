"""Configuração lida do ambiente no momento da chamada."""
import os
from pathlib import Path

from dotenv import load_dotenv

MODELO_PADRAO = "gemini-3.5-flash"


def raiz() -> Path:
    return Path(__file__).resolve().parent.parent


load_dotenv(raiz() / ".env")


def dados_dir() -> Path:
    return raiz() / "dados"


def var_dir() -> Path:
    d = Path(os.environ.get("AURORA_VAR_DIR") or raiz() / "var")
    d.mkdir(parents=True, exist_ok=True)
    return d


def condominio_db() -> Path:
    return var_dir() / "condominio.db"


def sessoes_db_url() -> str:
    return f"sqlite+aiosqlite:///{var_dir() / 'sessoes.db'}"


def modelo() -> str:
    return os.environ.get("GEMINI_MODEL") or MODELO_PADRAO
