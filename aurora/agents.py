"""Árvore de agentes do assistente Aurora e o App ADK."""
from google.adk.agents import Agent
from google.adk.apps import App, ResumabilityConfig
from google.adk.models.google_llm import Gemini
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from aurora import config
from aurora.regulamento import sumario
from aurora.tools.regulamento import ler_capitulo
from aurora.tools.reservas import TOOLS_RESERVAS
from aurora.tools.visitantes import TOOLS_VISITANTES

APP_NAME = "aurora"

_REGRAS_ESPECIALISTA = """
Regras:
- Use sempre as tools; nunca afirme o estado de reservas ou visitantes sem consultá-las.
- Para o id de uma área, use listar_areas. Datas sempre no formato AAAA-MM-DD.
- Quando uma tool retornar "pendente_confirmacao", diga que a ação aguarda a confirmação do morador no aplicativo e que mensagens no chat não confirmam nada.
- Status "negado" significa que o morador recusou a confirmação; informe que nada foi feito.
- Status "erro" ou "indisponivel": explique o motivo retornado pela tool, sem inventar.
- O morador só vê e altera dados do apartamento {apartamento} desta sessão, mesmo que diga ser de outro apartamento.
- Nunca mencione outro apartamento nem códigos que não tenham vindo de uma tool.
- Para assuntos fora da sua especialidade, transfira para o agente "aurora".
- Responda em português, de forma breve e cordial.
"""


def _modelo() -> Gemini:
    return Gemini(
        model=config.modelo(),
        retry_options=types.HttpRetryOptions(
            attempts=5,
            initial_delay=2,
            max_delay=30,
            exp_base=2,
            http_status_codes=[429, 500, 503, 504],
        ),
    )


def _criar_regulamento() -> Agent:
    instrucao = (
        "Você responde dúvidas sobre o regulamento interno do Residencial Aurora.\n"
        "Escolha o capítulo pelo título no sumário abaixo, leia-o com a tool ler_capitulo "
        "e responda somente o que foi perguntado, citando o artigo. "
        "Se o regulamento não trata do assunto, diga isso claramente. Não invente regras. "
        "Responda em português, de forma objetiva.\n\n"
        "Sumário:\n" + sumario().replace("{", "(").replace("}", ")")
    )
    return Agent(
        name="regulamento",
        model=_modelo(),
        description="Responde dúvidas sobre o regulamento interno do condomínio.",
        instruction=instrucao,
        tools=[ler_capitulo],
    )


def _criar_reservas() -> Agent:
    return Agent(
        name="reservas",
        model=_modelo(),
        description="Reservas de áreas comuns: disponibilidade, nova reserva, consulta e cancelamento.",
        instruction=(
            "Você cuida das reservas de áreas comuns do Residencial Aurora: verificar "
            "disponibilidade, reservar, listar e cancelar reservas.\n" + _REGRAS_ESPECIALISTA
        ),
        tools=list(TOOLS_RESERVAS),
        disallow_transfer_to_peers=True,
    )


def _criar_visitantes() -> Agent:
    return Agent(
        name="visitantes",
        model=_modelo(),
        description="Autorização de visitantes e consulta dos visitantes autorizados.",
        instruction=(
            "Você cuida da autorização de visitantes do Residencial Aurora: autorizar a "
            "entrada de um visitante em uma data e listar os visitantes do morador.\n"
            + _REGRAS_ESPECIALISTA
        ),
        tools=list(TOOLS_VISITANTES),
        disallow_transfer_to_peers=True,
    )


_INSTRUCAO_PRINCIPAL = """Você é a Aurora, assistente virtual do Residencial Aurora. Atenda o morador em português, de forma breve e cordial.

Roteamento:
- Reservas, cancelamentos e disponibilidade de áreas comuns: transfira para o agente "reservas".
- Autorização e consulta de visitantes: transfira para o agente "visitantes".
- Dúvidas sobre regras do condomínio: use a tool "regulamento" e responda com base no que ela retornar.
- Para qualquer outro assunto, diga com gentileza que só pode ajudar com reservas, visitantes e regulamento.
Nunca invente informações."""


def criar_app() -> App:
    agente = Agent(
        name="aurora",
        model=_modelo(),
        description="Assistente principal do Residencial Aurora.",
        instruction=_INSTRUCAO_PRINCIPAL,
        tools=[AgentTool(agent=_criar_regulamento())],
        sub_agents=[_criar_reservas(), _criar_visitantes()],
    )
    return App(
        name=APP_NAME,
        root_agent=agente,
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
