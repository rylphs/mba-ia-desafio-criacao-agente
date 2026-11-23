"""Agentes do assistente Aurora e o App ADK.

Cada agente é descrito por um YAML em `configs/`; ver `fabrica.py`.
"""
from pathlib import Path

from google.adk.apps import App, ResumabilityConfig

from aurora.agentes.fabrica import ConfigAgente, criar_agente, load_config

__all__ = ["APP_NAME", "CONFIGS_DIR", "ConfigAgente", "criar_agente", "criar_app", "load_config"]

APP_NAME = "aurora"
CONFIGS_DIR = Path(__file__).parent / "configs"


def criar_app() -> App:
    return App(
        name=APP_NAME,
        root_agent=criar_agente(load_config(CONFIGS_DIR / "aurora.yaml")),
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
