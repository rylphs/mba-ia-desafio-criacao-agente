# Assistente do Residencial Aurora — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** API FastAPI em `localhost:8000` com um assistente Google ADK (principal + 3 especialistas) que cumpre as cinco garantias do desafio em código.

**Architecture:** `aurora` (root) transfere para os sub-agentes `reservas` e `visitantes` e chama `regulamento` como `AgentTool`. Tools leem o apartamento do `state` da sessão e gravam num SQLite próprio com índice único parcial; sessões ficam no `DatabaseSessionService` (SQLite). Confirmações usam `tool_context.request_confirmation`; a API calcula pendências a partir dos eventos, decide o `409` e devolve o `FunctionResponse` ao Runner.

**Tech Stack:** Python 3.12, uv, `google-adk[db]==2.14.1`, FastAPI, uvicorn, aiosqlite, sqlite3 (stdlib), pytest + httpx (dev).

**Spec:** `docs/superpowers/specs/2026-11-13-residencial-aurora-design.md`

## Global Constraints

- Python `>=3.12`; projeto uv com `pyproject.toml` e `uv.lock` versionados.
- `google-adk[db]==2.14.1` — versão exata.
- Modelo padrão `gemini-3.5-flash`, sobrescrito por `GEMINI_MODEL`; chave em `GOOGLE_API_KEY`.
- `dados/` nunca é modificado. `.env` fora do Git; `.env.example` só com nomes.
- Bancos em `var/` (`var/condominio.db`, `var/sessoes.db`); diretório sobrescrito por `AURORA_VAR_DIR` (usado nos testes).
- Nenhuma tool aceita parâmetro de apartamento; o apartamento vem de `tool_context.state["apartamento"]`.
- Nenhum retorno de tool contém código, apartamento ou nome de outro apartamento.
- Agente principal sem texto do regulamento nas instruções.
- Um único processo uvicorn (locks em memória).
- Textos de agentes e mensagens em português.
- Commits terminam com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Modelo passa a área pelo nome ("salão de festas", "Salão de Festas") em vez do id → a tool resolve para `salao-de-festas` sem erro (teste em Task 3).
2. Modelo passa data fora do formato (`20/04/2030`) → tool devolve `erro` pedindo AAAA-MM-DD e nada é gravado nem fica pendente (teste em Task 3).
3. Reenvio de um id que foi **negado** → `409`, igual a um aprovado (teste em Task 6/7).
4. Cancelamento só por área + data (sem código) do próprio apartamento → cancela; o mesmo pedido para a reserva de outro apartamento → "não encontrada", sem vazar código (teste em Task 3).
5. Nome de visitante vazio ou só espaços → `erro`, sem pedir confirmação (teste em Task 3).

---

## Estrutura de arquivos

```
pyproject.toml, uv.lock, .env.example, README.md
aurora/__init__.py
aurora/config.py          caminhos e modelo (lidos em tempo de chamada)
aurora/db.py              esquema, carga/restauração, repositório, Garantia 5
aurora/restaurar.py       CLI aurora-restaurar
aurora/regulamento.py     parser de capítulos
aurora/tools/__init__.py
aurora/tools/_comum.py    apartamento da sessão, validação de data, resolução de área
aurora/tools/reservas.py
aurora/tools/visitantes.py
aurora/tools/regulamento.py
aurora/agents.py          árvore de agentes + App
aurora/confirmacoes.py    pendências, texto de resposta, pós-checagem
aurora/api.py             FastAPI, Runner, CLI aurora-api
scripts/fluxo_avaliador.py  roteiro E2E do avaliador
tests/conftest.py, tests/test_db.py, tests/test_tools.py, tests/test_regulamento.py,
tests/test_agents.py, tests/test_confirmacoes.py, tests/test_api.py
```

---

### Task 1: Projeto, configuração, esquema e restauração

**Files:**
- Create: `pyproject.toml`, `.env.example`, `aurora/__init__.py`, `aurora/config.py`, `aurora/db.py` (esquema + carga), `aurora/restaurar.py`, `tests/conftest.py`, `tests/test_db.py`

**Interfaces:**
- Produces:
  - `config.raiz() -> Path` (raiz do repo), `config.dados_dir() -> Path`, `config.var_dir() -> Path` (cria se faltar; respeita `AURORA_VAR_DIR`), `config.condominio_db() -> Path`, `config.sessoes_db_url() -> str` (`sqlite+aiosqlite:///<var>/sessoes.db`), `config.modelo() -> str`. `load_dotenv(raiz()/".env")` no import.
  - `db.conexao() -> ContextManager[sqlite3.Connection]` — abre `condominio_db()`, `row_factory=sqlite3.Row`, `isolation_level=None`, `PRAGMA busy_timeout=5000`, `PRAGMA journal_mode=WAL`; fecha ao sair.
  - `db.criar_esquema(conn) -> None` — tabelas e índice exatamente como no spec (seção Armazenamento), `IF NOT EXISTS`.
  - `db.restaurar(conn) -> None` — em uma transação: apaga e recarrega `apartamentos`, `areas`, `reservas` (status `ativa`, `origem` NULL), `visitantes` dos JSON de `dados/`. Não toca em `sessoes`.
  - `db.inicializar_se_preciso() -> None` — cria esquema; se `areas` vazia, chama `restaurar`.
  - `restaurar.main() -> None` — `inicializar` esquema + `restaurar`; flag `--sessoes` apaga `var/sessoes.db` e limpa a tabela `sessoes`. Imprime resumo.
  - `tests/conftest.py`: fixture autouse `var_tmp(tmp_path, monkeypatch)` que define `AURORA_VAR_DIR=tmp_path` e chama `db.inicializar_se_preciso()`; classe `FakeToolContext(apartamento, function_call_id="call-1", tool_confirmation=None)` com `state={"apartamento": ...}`, e `request_confirmation(*, hint=None, payload=None)` que guarda `self.pedidos.append({"hint":hint,"payload":payload})`.

- [ ] **Step 1: Criar `pyproject.toml`**

`name="aurora"`, `requires-python=">=3.12"`, build-system hatchling (`packages=["aurora"]`), deps `google-adk[db]==2.14.1`, `fastapi`, `uvicorn`, `aiosqlite`, `python-dotenv`; dev group `pytest`, `httpx`. Scripts: `aurora-api = "aurora.api:main"`, `aurora-restaurar = "aurora.restaurar:main"`. Rodar `uv sync` → sem erro. `.env.example` com `GOOGLE_API_KEY=` e `GEMINI_MODEL=`.

- [ ] **Step 2: Testes que falham — `tests/test_db.py`**

```python
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
```

- [ ] **Step 3:** `uv run pytest tests/test_db.py -v` → FAIL (módulo `aurora.db` inexistente).
- [ ] **Step 4:** Implementar `config.py`, esquema/carga em `db.py`, `restaurar.py`, `conftest.py`.
- [ ] **Step 5:** `uv run pytest tests/test_db.py -v` → PASS. `uv run aurora-restaurar` → imprime 3 reservas, 2 visitantes; `git status` não mostra `dados/` alterado.
- [ ] **Step 6: Commit** — `feat: projeto uv, esquema SQLite e comando de restauração`.

---

### Task 2: Repositório de reservas e visitantes (Garantia 5)

**Files:**
- Modify: `aurora/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `db.conexao`, esquema da Task 1.
- Produces (todas recebem `conn` aberta por `db.conexao()`):
  - `listar_areas(conn) -> list[dict]` → `[{"id","nome","taxa"}]`
  - `obter_area(conn, area_id: str) -> dict | None`
  - `data_ocupada(conn, area_id: str, data: str) -> bool` (só `status='ativa'`)
  - `reservas_ativas(conn, apartamento: str) -> list[dict]` → `[{"codigo","area","data"}]` ordenado por `data, codigo`
  - `inserir_reserva(conn, apartamento: str, area_id: str, data: str, origem: str | None) -> dict` → `{"status":"reservado","codigo","area","data"}` ou `{"status":"indisponivel","area","data"}`
  - `cancelar_reserva(conn, apartamento: str, *, codigo: str | None = None, area_id: str | None = None, data: str | None = None) -> dict | None` → reserva cancelada `{"codigo","area","data"}` ou `None` se não há reserva **ativa do apartamento** que case
  - `visitantes_do_apartamento(conn, apartamento: str) -> list[dict]` → `[{"nome","data"}]` ordenado por `data, nome`
  - `inserir_visitante(conn, apartamento: str, nome: str, data: str, origem: str | None) -> dict` → `{"status":"autorizado","nome","data"}`
  - `registrar_sessao(conn, session_id: str, apartamento: str) -> None`, `apartamento_da_sessao(conn, session_id: str) -> str | None`

- [ ] **Step 1: Testes que falham**

```python
def test_inserir_reserva_gera_codigo_novo_e_unico():
    with db.conexao() as c:
        r = db.inserir_reserva(c, "101", "quadra", "2030-04-06", "call-a")
        assert r["status"] == "reservado" and r["codigo"].startswith("RSV-")
        assert r["codigo"] not in {"RSV-1377", "RSV-4821", "RSV-2950"}

def test_inserir_reserva_idempotente_por_origem():
    with db.conexao() as c:
        a = db.inserir_reserva(c, "101", "salao-de-festas", "2030-04-20", "call-a")
        b = db.inserir_reserva(c, "101", "salao-de-festas", "2030-04-20", "call-a")
        assert a == b
        assert len([x for x in db.reservas_ativas(c, "101") if x["data"] == "2030-04-20"]) == 1

def test_inserir_reserva_data_ocupada_nao_vaza_dono():
    with db.conexao() as c:
        r = db.inserir_reserva(c, "101", "salao-de-festas", "2030-03-16", "call-b")
        assert r == {"status": "indisponivel", "area": "salao-de-festas", "data": "2030-03-16"}

def test_corrida_de_reservas_so_uma_vence():
    # 2 threads, cada uma com sua conexão, origens distintas, mesma área/data
    resultados = run_in_threads(lambda i: _reservar("101" if i == 0 else "201", "salao-de-festas", "2030-05-11", f"call-{i}"), n=2)
    assert sorted(r["status"] for r in resultados) == ["indisponivel", "reservado"]
    with db.conexao() as c:
        assert c.execute("select count(*) from reservas where area='salao-de-festas' and data='2030-05-11' and status='ativa'").fetchone()[0] == 1

def test_cancelar_so_do_proprio_apartamento():
    with db.conexao() as c:
        assert db.cancelar_reserva(c, "101", area_id="salao-de-festas", data="2030-03-16") is None
        assert db.cancelar_reserva(c, "101", codigo="RSV-4821") is None
        assert db.cancelar_reserva(c, "101", area_id="quadra", data="2030-03-09") == {"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}
        assert db.reservas_ativas(c, "101") == []
        assert [r["codigo"] for r in db.reservas_ativas(c, "302")] == ["RSV-4821"]

def test_codigo_cancelado_nunca_reaproveitado():
    with db.conexao() as c:
        db.cancelar_reserva(c, "101", codigo="RSV-1377")
        r = db.inserir_reserva(c, "101", "quadra", "2030-03-09", "call-c")
        assert r["status"] == "reservado" and r["codigo"] != "RSV-1377"

def test_inserir_visitante_idempotente():
    with db.conexao() as c:
        db.inserir_visitante(c, "101", "Joana Ribeiro", "2030-04-21", "call-v")
        db.inserir_visitante(c, "101", "Joana Ribeiro", "2030-04-21", "call-v")
        assert db.visitantes_do_apartamento(c, "101") == [{"nome": "Joana Ribeiro", "data": "2030-04-21"}]
```

`run_in_threads` / `_reservar` são helpers locais do teste (`threading.Barrier(2)` antes do insert; cada thread abre `db.conexao()`).

- [ ] **Step 2:** `uv run pytest tests/test_db.py -v` → FAIL nos novos.
- [ ] **Step 3: Implementar.** `inserir_reserva`: `BEGIN IMMEDIATE`; se existe linha com `origem` → `COMMIT` e devolve-a como `reservado`; senão tenta `INSERT` com código `"RSV-" + secrets.token_hex(3).upper()`; `IntegrityError` cuja mensagem contém `reservas.area` → `ROLLBACK`, `indisponivel`; contém `reservas.codigo` → novo código (até 5 tentativas). `inserir_visitante` análogo com `origem`. `cancelar_reserva` faz `UPDATE ... SET status='cancelada' WHERE apartamento=? AND status='ativa' AND (codigo=? | area=? AND data=?)` e devolve a linha afetada.
- [ ] **Step 4:** `uv run pytest tests/test_db.py -v` → PASS (rodar o de corrida 5× com `--count` manual ou laço; sempre 1 ativa).
- [ ] **Step 5: Commit** — `feat: repositório com exclusividade de reserva no instante da gravação`.

---

### Task 3: Tools de reservas e visitantes (Garantias 1 e 2)

**Files:**
- Create: `aurora/tools/__init__.py`, `aurora/tools/_comum.py`, `aurora/tools/reservas.py`, `aurora/tools/visitantes.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: funções de `db` da Task 2; `FakeToolContext` do conftest.
- Produces:
  - `_comum.apartamento_da_sessao(tool_context) -> str` — lê `tool_context.state["apartamento"]`; `KeyError` vira `RuntimeError` (sessão sem apartamento é bug, não entrada do usuário).
  - `_comum.validar_data(data: str) -> str | None` — devolve a data normalizada se `date.fromisoformat` aceitar **e** casar `^\d{4}-\d{2}-\d{2}$`; senão `None`.
  - `_comum.resolver_area(conn, valor: str) -> dict | None` — casa por `id` ou por `nome`, sem acento e sem caixa (`unicodedata.normalize("NFKD")`), aceitando também `nome` com hífens.
  - Tools (`tool_context: ToolContext` sempre último parâmetro; docstrings em português viram a descrição para o modelo):
    - `listar_areas() -> dict` → `{"areas":[{"id","nome","taxa","gera_cobranca":bool}]}`
    - `verificar_disponibilidade(area: str, data: str) -> dict` → `{"area","data","disponivel":bool}` | `{"status":"erro","mensagem"}`
    - `reservar_area(area: str, data: str, tool_context) -> dict` → status `pendente_confirmacao` | `negado` | `reservado` | `indisponivel` | `erro`
    - `listar_minhas_reservas(tool_context) -> dict` → `{"apartamento","reservas":[...]}`
    - `cancelar_reserva(tool_context, codigo: str = "", area: str = "", data: str = "") -> dict` → `{"status":"cancelado","reserva":{...}}` | `{"status":"nao_encontrada","mensagem":"Não encontrei reserva ativa do seu apartamento com esses dados."}` | `erro`
    - `autorizar_visitante(nome: str, data: str, tool_context) -> dict` → `pendente_confirmacao` | `negado` | `autorizado` | `erro`
    - `listar_meus_visitantes(tool_context) -> dict` → `{"apartamento","visitantes":[...]}`
  - Constantes `TOOLS_RESERVAS: list` e `TOOLS_VISITANTES: list` com as funções acima.

- [ ] **Step 1: Testes que falham**

```python
def test_quadra_reserva_sem_confirmacao():
    ctx = FakeToolContext("101")
    r = reservar_area("quadra", "2030-04-06", ctx)
    assert r["status"] == "reservado" and ctx.pedidos == []

def test_salao_pede_confirmacao_e_nao_grava():
    ctx = FakeToolContext("101")
    r = reservar_area("salao-de-festas", "2030-04-20", ctx)
    assert r["status"] == "pendente_confirmacao"
    assert ctx.pedidos[0]["payload"] == {"area": "salao-de-festas", "data": "2030-04-20", "taxa": 150.0}
    assert not _tem_reserva("101", "salao-de-festas", "2030-04-20")

def test_salao_negado_nao_grava():
    ctx = FakeToolContext("101", tool_confirmation=ToolConfirmation(confirmed=False))
    assert reservar_area("salao-de-festas", "2030-04-20", ctx)["status"] == "negado"
    assert not _tem_reserva("101", "salao-de-festas", "2030-04-20")

def test_salao_confirmado_grava_uma_vez_mesmo_reexecutado():
    ctx = FakeToolContext("101", function_call_id="call-x", tool_confirmation=ToolConfirmation(confirmed=True))
    a = reservar_area("salao-de-festas", "2030-04-20", ctx); b = reservar_area("salao-de-festas", "2030-04-20", ctx)
    assert a["status"] == "reservado" and a["codigo"] == b["codigo"]

def test_data_ocupada_recusa_sem_pedir_confirmacao_nem_vazar():
    ctx = FakeToolContext("101")
    r = reservar_area("salao-de-festas", "2030-03-16", ctx)
    assert r["status"] == "indisponivel" and ctx.pedidos == []
    assert "RSV-4821" not in str(r) and "302" not in str(r)

def test_area_por_nome_resolve():                                   # Review Focus 1
    assert verificar_disponibilidade("Salão de Festas", "2030-03-16") == {"area": "salao-de-festas", "data": "2030-03-16", "disponivel": False}

def test_data_formato_invalido():                                   # Review Focus 2
    ctx = FakeToolContext("101")
    assert reservar_area("salao-de-festas", "20/04/2030", ctx)["status"] == "erro" and ctx.pedidos == []

def test_cancelar_reserva_alheia_nao_vaza():                        # Review Focus 4
    r = cancelar_reserva(FakeToolContext("101"), area="salao-de-festas", data="2030-03-16")
    assert r["status"] == "nao_encontrada" and "RSV-4821" not in str(r) and "302" not in str(r)

def test_cancelar_propria_por_area_data_sem_confirmacao():
    ctx = FakeToolContext("101")
    assert cancelar_reserva(ctx, area="quadra", data="2030-03-09")["status"] == "cancelado" and ctx.pedidos == []

def test_listar_so_do_proprio_apartamento():
    assert [r["codigo"] for r in listar_minhas_reservas(FakeToolContext("101"))["reservas"]] == ["RSV-1377"]
    assert "Marina Duarte" not in str(listar_meus_visitantes(FakeToolContext("101")))

def test_visitante_sempre_pede_confirmacao():
    ctx = FakeToolContext("101")
    r = autorizar_visitante("Joana Ribeiro", "2030-04-21", ctx)
    assert r["status"] == "pendente_confirmacao" and ctx.pedidos[0]["payload"] == {"nome": "Joana Ribeiro", "data": "2030-04-21"}

def test_visitante_nome_vazio():                                     # Review Focus 5
    ctx = FakeToolContext("101")
    assert autorizar_visitante("   ", "2030-04-21", ctx)["status"] == "erro" and ctx.pedidos == []

def test_nenhuma_tool_aceita_apartamento():
    for f in TOOLS_RESERVAS + TOOLS_VISITANTES:
        assert not any("apart" in p for p in inspect.signature(f).parameters)
```

- [ ] **Step 2:** `uv run pytest tests/test_tools.py -v` → FAIL.
- [ ] **Step 3: Implementar.** Ordem em `reservar_area`: validar data → resolver área → `data_ocupada` (recusa cedo) → se `taxa > 0` e `tool_context.tool_confirmation is None`: `request_confirmation(hint=f"Reservar {nome} em {data} gera cobrança de R$ {taxa:.2f}. Confirma?", payload={"area","data","taxa"})` e retorna `{"status":"pendente_confirmacao","mensagem":"Aguardando o morador confirmar no aplicativo."}` → se confirmação existe e `confirmed is False`: `negado` → `inserir_reserva(..., origem=tool_context.function_call_id)`. `autorizar_visitante` idêntico, sempre pedindo. Retornos de erro com `mensagem` em português.
- [ ] **Step 4:** `uv run pytest tests/test_tools.py -v` → PASS.
- [ ] **Step 5: Commit** — `feat: tools de reservas e visitantes com confirmação e apartamento da sessão`.

---

### Task 4: Regulamento por capítulos (Garantia 4, parte 1)

**Files:**
- Create: `aurora/regulamento.py`, `aurora/tools/regulamento.py`
- Test: `tests/test_regulamento.py`

**Interfaces:**
- Produces:
  - `regulamento.Capitulo` (dataclass frozen: `numero: int`, `titulo: str`, `texto: str`)
  - `regulamento.capitulos() -> list[Capitulo]` (`functools.cache`; divide `dados/regulamento.md` em linhas `## Capítulo <romano>: <título>`; `numero` 1..14 pela ordem; `texto` sem a linha do título)
  - `regulamento.sumario() -> str` — linhas `"<n>. <título>"`, uma por capítulo
  - `tools.regulamento.ler_capitulo(numero: int) -> dict` → `{"numero","titulo","texto"}` | `{"status":"erro","mensagem":"Capítulo inexistente. Use um número de 1 a 14."}`

- [ ] **Step 1: Testes que falham**

```python
def test_quatorze_capitulos():
    cs = capitulos()
    assert len(cs) == 14 and cs[3].titulo == "Piscina" and cs[13].titulo == "Disposições finais"

def test_capitulo_piscina_tem_horario_de_domingo_e_nada_de_outros():
    t = ler_capitulo(4)["texto"]
    assert "Aos domingos e feriados, a piscina funciona das 9h às 20h." in t
    assert "Capítulo V" not in t and "Academia" not in t

def test_capitulo_inexistente():
    assert ler_capitulo(15)["status"] == "erro"

def test_sumario_so_titulos():
    s = sumario()
    assert s.splitlines()[3] == "4. Piscina" and "Art." not in s
```

- [ ] **Step 2:** FAIL → **Step 3:** implementar → **Step 4:** `uv run pytest tests/test_regulamento.py -v` PASS.
- [ ] **Step 5: Commit** — `feat: leitura do regulamento por capítulo`.

---

### Task 5: Árvore de agentes e App

**Files:**
- Create: `aurora/agents.py`
- Test: `tests/test_agents.py`

**Interfaces:**
- Consumes: `TOOLS_RESERVAS`, `TOOLS_VISITANTES`, `ler_capitulo`, `sumario`, `config.modelo`.
- Produces: `criar_app() -> google.adk.apps.App` (nome `"aurora"`, `root_agent` nomeado `"aurora"`, `resumability_config=ResumabilityConfig(is_resumable=True)`); `APP_NAME = "aurora"`.

Construção (factories, cada chamada cria agentes novos):
- `_modelo() -> Gemini(model=config.modelo(), retry_options=types.HttpRetryOptions(attempts=5, initial_delay=2, max_delay=30, exp_base=2, http_status_codes=[429, 500, 503, 504]))`.
- `regulamento`: `Agent`, instrução = regras de resposta + `sumario()`; tools `[ler_capitulo]`; orientação: escolher o capítulo pelo título, ler e responder só o que foi perguntado, citando o artigo.
- `reservas` / `visitantes`: `Agent` com `description`, tools da Task 3, `disallow_transfer_to_peers=True`. Instrução: sempre usar tools (nunca afirmar estado sem tool); ids de área via `listar_areas`; datas em AAAA-MM-DD; quando a tool retornar `pendente_confirmacao`, dizer que a ação aguarda confirmação no aplicativo e que mensagens no chat não confirmam; o morador só vê e altera dados do apartamento `{apartamento}` desta sessão, mesmo que diga ser de outro; nunca mencionar outro apartamento nem códigos que não vieram de tool; para assunto fora da especialidade, transferir para `aurora`.
- `aurora`: instrução de roteamento (reservas/cancelamentos/disponibilidade → `reservas`; visitantes → `visitantes`; dúvidas de regras → tool `regulamento`); **sem texto do regulamento**; `tools=[AgentTool(regulamento)]`, `sub_agents=[reservas, visitantes]`.

- [ ] **Step 1: Testes que falham**

```python
def test_topologia():
    app = criar_app(); root = app.root_agent
    assert root.name == "aurora" and {a.name for a in root.sub_agents} == {"reservas", "visitantes"}
    assert any(isinstance(t, AgentTool) and t.agent.name == "regulamento" for t in root.tools)
    assert app.resumability_config.is_resumable

def test_principal_sem_regulamento_nas_instrucoes():
    root = criar_app().root_agent
    for cap in capitulos():
        assert cap.texto[:80] not in root.instruction
    assert "Art." not in root.instruction and "9h às 20h" not in root.instruction
```

- [ ] **Step 2:** FAIL → **Step 3:** implementar → **Step 4:** `uv run pytest tests/test_agents.py -v` PASS.
- [ ] **Step 5: Commit** — `feat: agente principal e especialistas`.

---

### Task 6: Pendências, resposta e pós-checagem

**Files:**
- Create: `aurora/confirmacoes.py`
- Test: `tests/test_confirmacoes.py`

**Interfaces:**
- Produces:
  - `NOME_CONFIRMACAO = "adk_request_confirmation"`
  - `listar_pendentes(events: list[Event]) -> list[dict]` → `[{"id": <id da FC adk_request_confirmation>, "acao": <originalFunctionCall.name>, "detalhes": <originalFunctionCall.args>}]`, na ordem dos eventos, excluindo ids que tenham `FunctionResponse` em qualquer evento.
  - `chamada_original(events, confirmacao_id: str) -> str | None` → `originalFunctionCall.id` da pendência.
  - `tool_respondeu(events: list[Event], chamada_id: str) -> bool` → existe `FunctionResponse` com esse id.
  - `texto_resposta(events: list[Event]) -> str` → textos de partes não-`thought` de eventos com `author != "user"` e `partial` falso, unidos por `"\n"`, `strip()`.
  - `mensagem_confirmacao(confirmacao_id: str, confirmado: bool) -> types.Content` → `role="user"`, uma `FunctionResponse(id=..., name=NOME_CONFIRMACAO, response={"confirmed": confirmado})`.

- [ ] **Step 1: Testes que falham** — helpers locais `ev_fc(author, name, id, args)` e `ev_fr(author, name, id, response)` montam `Event(author=..., content=types.Content(role=..., parts=[types.Part(function_call=...)]))`.

```python
ORIG = {"id": "call-1", "name": "reservar_area", "args": {"area": "salao-de-festas", "data": "2030-04-20"}}

def test_pendente_aparece_com_detalhes():
    evs = [ev_fc("reservas", "adk_request_confirmation", "adk-1", {"originalFunctionCall": ORIG, "toolConfirmation": {"hint": "x"}})]
    assert listar_pendentes(evs) == [{"id": "adk-1", "acao": "reservar_area", "detalhes": {"area": "salao-de-festas", "data": "2030-04-20"}}]
    assert chamada_original(evs, "adk-1") == "call-1"

def test_respondida_sai_da_lista_aprovada_ou_negada():                # Review Focus 3
    for ok in (True, False):
        evs = [ev_fc("reservas", "adk_request_confirmation", "adk-1", {"originalFunctionCall": ORIG, "toolConfirmation": {}}),
               ev_fr("user", "adk_request_confirmation", "adk-1", {"confirmed": ok})]
        assert listar_pendentes(evs) == []

def test_tool_respondeu():
    evs = [ev_fr("reservas", "reservar_area", "call-1", {"status": "indisponivel"})]
    assert tool_respondeu(evs, "call-1") and not tool_respondeu(evs, "call-2")

def test_texto_resposta_ignora_usuario_e_thought():
    evs = [ev_text("user", "oi"), ev_text("reservas", "pensando", thought=True), ev_text("reservas", "Reservado.")]
    assert texto_resposta(evs) == "Reservado."
    assert texto_resposta([]) == ""

def test_mensagem_confirmacao_formato():
    fr = mensagem_confirmacao("adk-1", False).parts[0].function_response
    assert (fr.id, fr.name, fr.response) == ("adk-1", "adk_request_confirmation", {"confirmed": False})
```

- [ ] **Step 2:** FAIL → **Step 3:** implementar → **Step 4:** `uv run pytest tests/test_confirmacoes.py -v` PASS.
- [ ] **Step 5: Commit** — `feat: extração de confirmações pendentes dos eventos`.

---

### Task 7: API e comando de subida

**Files:**
- Create: `aurora/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `criar_app`, `APP_NAME`, `confirmacoes.*`, `db.*`, `config.sessoes_db_url`.
- Produces: `app: FastAPI`; `main() -> None` (`uvicorn.run("aurora.api:app", host="0.0.0.0", port=8000)`).

Comportamento (exatamente o contrato do spec):
- `lifespan`: `db.inicializar_se_preciso()`; cria `DatabaseSessionService(db_url=config.sessoes_db_url())` e `Runner(app=criar_app(), session_service=...)` em `app.state`; `locks: defaultdict(asyncio.Lock)`.
- `_carregar(session_id) -> (apartamento, Session)` — `db.apartamento_da_sessao` + `get_session(app_name=APP_NAME, user_id=apartamento, session_id=...)`; qualquer `None` → `HTTPException(404)`.
- `_executar(apartamento, session_id, content) -> list[Event]` — coleta os eventos de `runner.run_async(...)`; exceções de `google.genai.errors.APIError` → `HTTPException(502, "Modelo indisponível, tente novamente.")`.
- `_resposta(session_id, apartamento, eventos_da_execucao) -> dict` — recarrega a sessão; `{"resposta": texto_resposta(eventos_da_execucao), "confirmacoes_pendentes": listar_pendentes(session.events)}`.
- `POST /sessoes` body `{"apartamento": str}` → `create_session(app_name, user_id=apartamento, state={"apartamento": apartamento})`, `db.registrar_sessao`, `201 {"session_id"}`.
- `POST /sessoes/{id}/mensagens` body `{"texto": str}` → sob o lock: `_carregar`, `_executar` com `Content(role="user", parts=[Part(text=texto)])`, `200 _resposta`.
- `POST /sessoes/{id}/confirmacoes` body `{"id": str, "confirmado": bool}` → sob o lock: `_carregar`; se `id` não está em `listar_pendentes(session.events)` → `409 {"detail": "Não existe confirmação pendente com esse id nesta sessão."}` **sem chamar o Runner**; senão `orig = chamada_original(...)`, `_executar(mensagem_confirmacao(...))`, recarrega e, **se `confirmado` for verdadeiro** e `not tool_respondeu(session.events, orig)` → `500 {"detail": "A confirmação foi recebida, mas a ação não foi executada."}`; senão `200 _resposta`.
- `GET /sessoes/{id}/eventos` → `[e.model_dump(mode="json", by_alias=True, exclude_none=True) for e in session.events]`.
- `GET /apartamentos/{n}/reservas` → `db.reservas_ativas`; `GET /apartamentos/{n}/visitantes` → `db.visitantes_do_apartamento`.

- [ ] **Step 1: Testes que falham** (`TestClient(app)` como context manager; nenhum teste chama o modelo)

```python
def test_criar_sessao_201(cliente):
    r = cliente.post("/sessoes", json={"apartamento": "101"})
    assert r.status_code == 201 and r.json()["session_id"]

def test_rotas_de_verificacao(cliente):
    assert cliente.get("/apartamentos/101/reservas").json() == [{"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}]
    assert cliente.get("/apartamentos/302/visitantes").json() == [{"nome": "Marina Duarte", "data": "2030-03-16"}]

def test_404_sessao_inexistente(cliente):
    assert cliente.get("/sessoes/sessao-inexistente/eventos").status_code == 404
    assert cliente.post("/sessoes/x/mensagens", json={"texto": "oi"}).status_code == 404
    assert cliente.post("/sessoes/x/confirmacoes", json={"id": "a", "confirmado": True}).status_code == 404

def test_409_id_inexistente_nao_chama_runner(cliente, monkeypatch):
    sid = cliente.post("/sessoes", json={"apartamento": "101"}).json()["session_id"]
    monkeypatch.setattr(cliente.app.state.runner, "run_async", _explode)
    r = cliente.post(f"/sessoes/{sid}/confirmacoes", json={"id": "id-inexistente", "confirmado": True})
    assert r.status_code == 409

def test_409_id_ja_respondido(cliente, monkeypatch):
    sid = cliente.post("/sessoes", json={"apartamento": "101"}).json()["session_id"]
    # via cliente.app.state.session_service.append_event (rodado com anyio.from_thread / portal do TestClient):
    # 1) Event(author="reservas") com FC adk_request_confirmation id "adk-1" e args {"originalFunctionCall": ORIG, "toolConfirmation": {}}
    # 2) Event(author="user") com FR adk_request_confirmation id "adk-1" response {"confirmed": False}
    monkeypatch.setattr(cliente.app.state.runner, "run_async", _explode)
    r = cliente.post(f"/sessoes/{sid}/confirmacoes", json={"id": "adk-1", "confirmado": True})
    assert r.status_code == 409

def test_pos_checagem_runner_silencioso_da_500(cliente, monkeypatch):
    sid = cliente.post("/sessoes", json={"apartamento": "101"}).json()["session_id"]
    # injeta só o evento 1) acima (pendência "adk-1" aberta)
    chamadas = []
    async def runner_mudo(**kw):
        chamadas.append(kw); return; yield  # gerador async que não emite eventos
    monkeypatch.setattr(cliente.app.state.runner, "run_async", runner_mudo)
    assert cliente.post(f"/sessoes/{sid}/confirmacoes", json={"id": "adk-9", "confirmado": True}).status_code == 409
    assert chamadas == []
    r = cliente.post(f"/sessoes/{sid}/confirmacoes", json={"id": "adk-1", "confirmado": True})
    assert r.status_code == 500 and len(chamadas) == 1

def test_eventos_lista_vazia_em_sessao_nova(cliente):
    sid = cliente.post("/sessoes", json={"apartamento": "101"}).json()["session_id"]
    assert cliente.get(f"/sessoes/{sid}/eventos").json() == []
```

- [ ] **Step 2:** FAIL → **Step 3:** implementar → **Step 4:** `uv run pytest -v` (suite inteira) PASS.
- [ ] **Step 5:** Fumaça com modelo real: `uv run aurora-restaurar && uv run aurora-api` e, noutro terminal, criar sessão e enviar `Reserve a quadra para 2030-04-06.` → `200` com `confirmacoes_pendentes == []` e reserva visível em `/apartamentos/101/reservas`.
- [ ] **Step 6: Commit** — `feat: API conforme contrato do desafio`.

---

### Task 8: Roteiro do avaliador e ajuste fino dos prompts

**Files:**
- Create: `scripts/fluxo_avaliador.py`
- Modify (se necessário): instruções em `aurora/agents.py`

**Interfaces:**
- Consumes: a API rodando em `http://localhost:8000` (httpx).
- Produces: `uv run python scripts/fluxo_avaliador.py parte1` (passos 1–12; grava S1 e contagem de eventos em `var/fluxo.json`) e `... parte2` (passos 13–14, após reiniciar a API manualmente sem restaurar). Cada passo imprime `OK`/`FALHOU: <motivo>` e as respostas do assistente; sai com código ≠ 0 se algo falhar.

Verificações codificadas, uma por passo do enunciado, com os valores literais de lá: textos das mensagens; `RSV-4821`, `Marina Duarte` ausentes da resposta e de `/eventos` (passos 3, 4, 10); `302` isolado ausente das respostas do passo 10 (`re.search(r"(?<![\w-])302(?![\w-])", ...)`); ausência de pendências nos passos 5 e 6; pendência com `area`/`data` (7) e `nome`/`data` (11) em `detalhes`; `409` no reenvio (8) e em `id-inexistente` (9); `404` em `sessao-inexistente` (9); `20h` na resposta do passo 12; nos eventos de S1 após o passo 12, nenhuma frase-assinatura de outros capítulos (uma frase literal por capítulo ≠ IV, extraída de `regulamento.capitulos()`, e também o título de cada um); passo 13: mesma contagem de eventos, `200`, contagem aumenta, reservas/visitantes esperados e códigos distintos entre si e de `RSV-1377/4821/2950`; passo 14: duas aprovações concorrentes (`asyncio.gather`) ambas `200` e soma exatamente 1 reserva do salão em 2030-05-11. Se um passo que exige pendência não a obtiver na 1ª resposta (modelo perguntou algo), o roteiro responde uma vez `"Sim, pode seguir."` antes de reprovar.

- [ ] **Step 1:** Escrever o roteiro.
- [ ] **Step 2:** `uv run aurora-restaurar --sessoes && uv run aurora-api` (terminal A); `uv run python scripts/fluxo_avaliador.py parte1` (terminal B) → todos `OK`.
- [ ] **Step 3:** Ctrl+C no terminal A, `uv run aurora-api` de novo; `uv run python scripts/fluxo_avaliador.py parte2` → todos `OK`.
- [ ] **Step 4:** Se algum passo falhar por comportamento do modelo, ajustar só instruções (nunca mover regra para o prompt) e repetir os passos 2–3 até passar **duas execuções seguidas**.
- [ ] **Step 5: Commit** — `test: roteiro E2E do fluxo do avaliador`.

---

### Task 9: README

**Files:**
- Modify: `README.md` (substitui o enunciado)

- [ ] **Step 1:** Escrever as três seções exigidas:
  - **Arquitetura** — tabela dos 4 agentes (responsabilidade, acionamento, por quê: transferência para quem confirma porque `AgentTool` não retoma confirmação; `AgentTool` para o regulamento porque isola o texto numa sessão efêmera), diagrama em texto, fluxo de uma confirmação (pedido → pendência → rota → `FunctionResponse` → Runner → especialista), resumo do spike.
  - **Garantias** — para cada uma: arquivo, função/trecho (`aurora/tools/reservas.py` → `reservar_area`, `aurora/db.py` → `inserir_reserva` e índice `reserva_unica_ativa`, `aurora/api.py` → rota de confirmações e pós-checagem, `aurora/confirmacoes.py` → `listar_pendentes`, `aurora/agents.py` → instrução de `aurora`, `aurora/regulamento.py`) e por que não depende do modelo.
  - **Como rodar** — pré-requisitos (Python 3.12, uv, chave AI Studio com faturamento), `cp .env.example .env`, variáveis (`GOOGLE_API_KEY`, `GEMINI_MODEL` opcional), `uv sync`, `uv run aurora-restaurar` (e `--sessoes`), `uv run aurora-api`, `uv run pytest`, roteiro E2E.
- [ ] **Step 2:** Conferir que todo arquivo e função citados existem: `grep -n` de cada nome citado.
- [ ] **Step 3: Commit** — `docs: README com arquitetura, garantias e como rodar`.
