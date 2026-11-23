"""Criação de agentes ADK a partir de arquivos YAML.

Cada YAML descreve um agente. Campos:

- name, description, instruction: obrigatórios.
- model: opcional; padrão é `config.modelo()` (variável GEMINI_MODEL).
- tools: nomes de funções registradas em `TOOLS`.
- agent_tools: YAMLs de agentes usados como tool (`AgentTool`).
- sub_agents: YAMLs de agentes para os quais este pode transferir.
- disallow_transfer_to_peers: opcional, padrão false.

Caminhos em agent_tools e sub_agents são relativos ao YAML que os cita.
Na instrução, `${nome}` é trocado pelo valor de `VARIAVEIS[nome]` na criação
do agente (use `$$` para um `$` literal). `{chave}` sem `$` é template de
estado do ADK e passa intacto.
"""
from pathlib import Path
from string import Template
from typing import Callable

import yaml
from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.tools.agent_tool import AgentTool
from google.genai import types
from pydantic import BaseModel, ConfigDict, field_validator

from aurora import config
from aurora.regulamento import sumario
from aurora.tools.regulamento import ler_capitulo
from aurora.tools.reservas import TOOLS_RESERVAS
from aurora.tools.visitantes import TOOLS_VISITANTES

TOOLS: dict[str, Callable] = {
    t.__name__: t for t in (ler_capitulo, *TOOLS_RESERVAS, *TOOLS_VISITANTES)
}


def _sumario_regulamento() -> str:
    # Chaves no sumário seriam lidas pelo ADK como template de estado.
    return sumario().replace("{", "(").replace("}", ")")


VARIAVEIS: dict[str, Callable[[], str]] = {
    "sumario_regulamento": _sumario_regulamento,
}


class ConfigAgente(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    instruction: str
    model: str | None = None
    tools: list[str] = []
    agent_tools: list["ConfigAgente"] = []
    sub_agents: list["ConfigAgente"] = []
    disallow_transfer_to_peers: bool = False

    @field_validator("tools")
    @classmethod
    def _tools_registradas(cls, nomes: list[str]) -> list[str]:
        desconhecidas = [n for n in nomes if n not in TOOLS]
        if desconhecidas:
            raise ValueError(f"tools não registradas: {desconhecidas}; disponíveis: {sorted(TOOLS)}")
        return nomes

    @field_validator("instruction")
    @classmethod
    def _variaveis_conhecidas(cls, texto: str) -> str:
        tpl = Template(texto)
        if not tpl.is_valid():
            raise ValueError("instrução com '$' inválido; use '$$' para um '$' literal")
        desconhecidas = [v for v in tpl.get_identifiers() if v not in VARIAVEIS]
        if desconhecidas:
            raise ValueError(f"variáveis desconhecidas: {desconhecidas}; disponíveis: {sorted(VARIAVEIS)}")
        return texto


def load_config(path: str | Path) -> ConfigAgente:
    """Lê o YAML de um agente e, recursivamente, os YAMLs que ele cita."""
    path = Path(path)
    with path.open(encoding="utf-8") as f:
        dados = yaml.safe_load(f)
    if not isinstance(dados, dict):
        raise ValueError(f"{path}: esperado um mapeamento YAML")
    for campo in ("agent_tools", "sub_agents"):
        dados[campo] = [load_config(path.parent / ref) for ref in dados.get(campo) or []]
    return ConfigAgente.model_validate(dados)


def _modelo(nome: str | None) -> Gemini:
    return Gemini(
        model=nome or config.modelo(),
        retry_options=types.HttpRetryOptions(
            attempts=5,
            initial_delay=2,
            max_delay=30,
            exp_base=2,
            http_status_codes=[429, 500, 503, 504],
        ),
    )


def _instrucao(texto: str) -> str:
    tpl = Template(texto)
    return tpl.substitute({v: VARIAVEIS[v]() for v in tpl.get_identifiers()})


def criar_agente(cfg: ConfigAgente) -> Agent:
    """Cria um agente novo (e seus sub-agentes e agent tools) a partir da configuração."""
    return Agent(
        name=cfg.name,
        model=_modelo(cfg.model),
        description=cfg.description,
        instruction=_instrucao(cfg.instruction),
        tools=[
            *(TOOLS[n] for n in cfg.tools),
            *(AgentTool(agent=criar_agente(c)) for c in cfg.agent_tools),
        ],
        sub_agents=[criar_agente(c) for c in cfg.sub_agents],
        disallow_transfer_to_peers=cfg.disallow_transfer_to_peers,
    )
