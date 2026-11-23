from google.adk.tools.agent_tool import AgentTool

from aurora.agentes import APP_NAME, criar_app
from aurora.regulamento import capitulos, sumario


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


# --- load_config / criar_agente ---------------------------------------------

import pytest
from pydantic import ValidationError

from aurora.agentes import CONFIGS_DIR, ConfigAgente, criar_agente, load_config


def _yaml(tmp_path, nome, texto):
    p = tmp_path / nome
    p.write_text(texto, encoding="utf-8")
    return p


def test_load_config_resolve_referencias_relativas_ao_yaml(tmp_path):
    _yaml(tmp_path, "filho.yaml", "name: filho\ndescription: d\ninstruction: i\n")
    raiz = _yaml(
        tmp_path, "raiz.yaml",
        "name: raiz\ndescription: d\ninstruction: i\nsub_agents: [filho.yaml]\nagent_tools: [filho.yaml]\n",
    )
    cfg = load_config(raiz)
    assert isinstance(cfg, ConfigAgente)
    assert cfg.sub_agents[0].name == cfg.agent_tools[0].name == "filho"


def test_load_config_rejeita_tool_desconhecida(tmp_path):
    p = _yaml(tmp_path, "a.yaml", "name: a\ndescription: d\ninstruction: i\ntools: [nao_existe]\n")
    with pytest.raises(ValidationError, match="nao_existe"):
        load_config(p)


def test_load_config_rejeita_campo_desconhecido(tmp_path):
    p = _yaml(tmp_path, "a.yaml", "name: a\ndescription: d\ninstruction: i\ntool: [listar_areas]\n")
    with pytest.raises(ValidationError, match="tool"):
        load_config(p)


def test_load_config_rejeita_variavel_desconhecida(tmp_path):
    p = _yaml(tmp_path, "a.yaml", "name: a\ndescription: d\ninstruction: 'x ${nada}'\n")
    with pytest.raises(ValidationError, match="nada"):
        load_config(p)


def test_criar_agente_substitui_variaveis_e_preserva_template_de_estado():
    cfg = ConfigAgente(
        name="a", description="d",
        instruction="${sumario_regulamento} | {apartamento} | $$",
        tools=["listar_areas"],
    )
    agente = criar_agente(cfg)
    assert agente.instruction.startswith(sumario())
    assert agente.instruction.endswith("| {apartamento} | $")
    assert [t.__name__ for t in agente.tools] == ["listar_areas"]


def test_cada_agente_tem_seu_yaml():
    for nome in ("aurora", "regulamento", "reservas", "visitantes"):
        assert load_config(CONFIGS_DIR / f"{nome}.yaml").name == nome
