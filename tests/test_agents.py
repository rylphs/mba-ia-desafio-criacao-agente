from google.adk.tools.agent_tool import AgentTool

from aurora.agents import APP_NAME, criar_app
from aurora.regulamento import capitulos


def test_topologia():
    app = criar_app()
    root = app.root_agent
    assert app.name == APP_NAME == "aurora"
    assert root.name == "aurora" and {a.name for a in root.sub_agents} == {"reservas", "visitantes"}
    assert any(isinstance(t, AgentTool) and t.agent.name == "regulamento" for t in root.tools)
    assert app.resumability_config.is_resumable


def test_principal_sem_regulamento_nas_instrucoes():
    root = criar_app().root_agent
    for cap in capitulos():
        assert cap.texto.strip()[:80] not in root.instruction
    assert "Art." not in root.instruction and "9h às 20h" not in root.instruction


def test_factories_criam_agentes_novos():
    assert criar_app().root_agent is not criar_app().root_agent


def test_instrucoes_sem_chaves_indevidas():
    root = criar_app().root_agent
    assert "{" not in root.instruction
    reg = next(t for t in root.tools if isinstance(t, AgentTool)).agent
    assert "{" not in reg.instruction
    for a in root.sub_agents:
        assert a.instruction.count("{") == 1 and "{apartamento}" in a.instruction
