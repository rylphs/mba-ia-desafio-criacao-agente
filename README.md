# Residencial Aurora: assistente de condomínio com Google ADK

API HTTP de um assistente de condomínio feito com o Google ADK (2.14.1, versão fixada em `pyproject.toml`). O morador conversa por sessões, cada sessão pertence a um apartamento, e o assistente reserva áreas comuns, autoriza visitantes e responde dúvidas do regulamento. A ideia central: o modelo decide o caminho, o código decide o que é permitido.

## Arquitetura

### Agentes

| Agente | Acionamento | Responsabilidade | Por quê |
|---|---|---|---|
| `aurora` (principal) | `root_agent` do `App` (`aurora/agents.py`, `criar_app`) | Acolhe o morador e roteia. Não tem o regulamento nas instruções. | Concentra o roteamento; assuntos fora do escopo são recusados aqui. |
| `reservas` | Transferência (`sub_agents` do principal) | Listar áreas, ver disponibilidade, reservar, listar e cancelar as reservas do próprio apartamento. | Quem pede a confirmação precisa ser o agente que a retoma. Com transferência, o Runner devolve a resposta ao especialista. `AgentTool` não serve: descarta a sessão aninhada e não retoma confirmação. |
| `visitantes` | Transferência (`sub_agents` do principal) | Autorizar visitante (sempre com confirmação) e listar os visitantes do apartamento. | Mesmo motivo de `reservas`. |
| `regulamento` | `AgentTool` do principal | Escolhe o capítulo pelo sumário, lê só ele com `ler_capitulo` e responde citando o artigo. | O `AgentTool` roda numa sessão efêmera: só a resposta final entra nos eventos da sessão principal, então o texto dos capítulos não fica salvo na conversa. |

Os especialistas só transferem de volta para o pai (`disallow_transfer_to_peers=True`).

```
aurora (root, sem regulamento nas instruções)
├── tool: AgentTool(regulamento) ── ler_capitulo(numero)     [sessão efêmera]
├── sub_agent: reservas   ── listar_areas, verificar_disponibilidade,
│                            reservar_area, listar_minhas_reservas, cancelar_reserva
└── sub_agent: visitantes ── autorizar_visitante, listar_meus_visitantes
```

### Fluxo de uma confirmação

```
1. POST /sessoes/{id}/mensagens  "Reserve o salão para 2030-05-11"
2. aurora transfere para reservas; reservar_area valida área e data,
   chama request_confirmation(payload={area, data, taxa}) e NÃO grava
3. A resposta traz confirmacoes_pendentes: [{id, acao, detalhes}]
   (extraídas dos eventos por confirmacoes.listar_pendentes)
4. POST /sessoes/{id}/confirmacoes {id, confirmado}
   - id não pendente nesta sessão -> 409, sem chamar o Runner
   - senão: monta um FunctionResponse "adk_request_confirmation"
     e entrega ao Runner, que retoma o especialista
5. reservar_area roda de novo com tool_confirmation preenchida
   e grava via db.inserir_reserva (idempotente pela chamada original)
6. A API confere que a tool original respondeu; se não, devolve 500
```

### Confirmação com sessão persistida (spike)

A documentação do ADK diz que a confirmação de tools não suporta `DatabaseSessionService`. Em spike descartável (ADK 2.14.1, SQLite, pedido num processo e aprovação em outro para simular reinício) a retomada funcionou em transferência e em `mode="task"`, com e sem `ResumabilityConfig`. O que o spike mostrou e moldou o desenho:

- O ADK aceita reenvio de um id já respondido, então o `409` é da API.
- Na retomada, a tool roda de novo com os argumentos originais; por isso a gravação é idempotente.
- Com `AgentTool` a retomada não funciona (sessão aninhada descartada), daí a transferência.

A suíte de E2E (`scripts/fluxo_avaliador.py`) reproduziu o fluxo do avaliador, incluindo reinício da API e aprovações simultâneas: 2/2 execuções completas OK.

## Garantias

### 1. Cobrança ou acesso só com confirmação

A confirmação é pedida dentro da tool, em código. Em `aurora/tools/reservas.py`, `reservar_area` só chega ao `INSERT` depois de a confirmação existir e ser positiva (áreas com taxa):

```python
if a["taxa"] > 0:
    confirmacao = tool_context.tool_confirmation
    if confirmacao is None:
        ...
        tool_context.request_confirmation(
            hint=f"Reservar {a['nome']} em {data_ok} gera cobrança de R$ {a['taxa']:.2f}. Confirma?",
            payload={"area": a["id"], "data": data_ok, "taxa": a["taxa"]},
        )
        return {
            "status": "pendente_confirmacao",
```

`autorizar_visitante` (`aurora/tools/visitantes.py`) pede confirmação sempre, sem condição. O morador dizendo "já confirmei" no chat não muda nada, porque só um `FunctionResponse` de confirmação vindo da rota libera a gravação.

Rota e conferências, em `aurora/api.py`, `responder_confirmacao`:

```python
if corpo.id not in {p["id"] for p in confirmacoes.listar_pendentes(session.events)}:
    raise HTTPException(409, "Não existe confirmação pendente com esse id nesta sessão.")
...
if corpo.confirmado and not confirmacoes.tool_respondeu(session.events, original):
    raise HTTPException(500, "A confirmação foi recebida, mas a ação não foi executada.")
```

- `409` para id inexistente ou já respondido: decidido pela API a partir dos eventos persistidos, antes de chamar o Runner.
- Pós-checagem: se a aprovação for aceita mas a tool original não produzir resposta, o resultado é `500`, nunca um `200` silencioso.
- `detalhes` vêm do payload que a tool validou (id da área, data normalizada, nome), com fallback nos argumentos da chamada (`aurora/confirmacoes.py`, `listar_pendentes`):

```python
"detalhes": _payload(fc) or dict(orig.get("args") or {}),
```

- Retomada é pelo menos uma vez, então a escrita é idempotente pelo id da chamada original: `db.inserir_reserva` e `db.inserir_visitante` consultam a coluna `origem` (`UNIQUE`) antes de inserir.

Nada disso depende do modelo: ele pode chamar a tool, mas quem grava é o código, e só depois de um `FunctionResponse` que o modelo não emite.

### 2. Cada sessão pertence a um apartamento

O apartamento é gravado no estado da sessão na criação (`aurora/api.py`, `criar_sessao`: `state={"apartamento": corpo.apartamento}`) e lido pelas tools em `aurora/tools/_comum.py`:

```python
def apartamento_do_contexto(tool_context) -> str:
    try:
        return tool_context.state["apartamento"]
```

Nenhuma tool tem parâmetro de apartamento: `reservar_area(area, data, tool_context)`, `listar_minhas_reservas(tool_context)`, `cancelar_reserva(tool_context, codigo, area, data)`, `autorizar_visitante(nome, data, tool_context)`. Todas as consultas e o cancelamento filtram por esse valor (`db.cancelar_reserva` usa `WHERE apartamento = ? ...`), então um código do 302 informado numa sessão do 101 resulta em `nao_encontrada`. Para disputa de data, `reservar_area` devolve só `indisponivel`, sem dizer quem tem a reserva. O modelo não consegue escolher outro apartamento porque não existe argumento para isso.

### 3. Nada se perde no reinício

Sessões ficam em SQLite via `DatabaseSessionService` (`aurora/api.py`, `lifespan`; URL em `aurora/config.py`, `sessoes_db_url`) e os dados do condomínio no SQLite `var/condominio.db` (`aurora/db.py`). Nada fica em memória do processo, exceto os locks por sessão, que são só para serializar requisições. `db.inicializar_se_preciso` só carrega `dados/` quando a tabela de áreas está vazia, então subir de novo não restaura nada. Os códigos de reserva são `RSV-` mais 6 hex aleatórios com retentativa em colisão (`inserir_reserva`), por isso não repetem códigos anteriores. A associação sessão para apartamento também é persistida (tabela `sessoes`).

### 4. O regulamento é consultado, não carregado

`aurora/agents.py`: a instrução do principal (`_INSTRUCAO_PRINCIPAL`) não contém o regulamento, só manda usar a tool `regulamento`. O agente `regulamento` recebe apenas o sumário (`aurora/regulamento.py`, `sumario`: números e títulos) e lê um capítulo por vez com `ler_capitulo` (`aurora/tools/regulamento.py`), cujo texto vem de `capitulos()`, que divide `dados/regulamento.md` por `## Capítulo`:

```python
return Agent(
    name="regulamento",
    ...
    tools=[ler_capitulo],
)
...
tools=[AgentTool(agent=_criar_regulamento())],
```

Como o agente é um `AgentTool`, a sessão dele é aninhada e descartada: os eventos da sessão do morador guardam a chamada da tool e a resposta final, não os capítulos lidos. Isso é estrutura do ADK, não comportamento do modelo.

### 5. Dois moradores, uma reserva

A exclusividade é do banco, no instante da gravação. `aurora/db.py`, `ESQUEMA`:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS reserva_unica_ativa ON reservas(area, data) WHERE status = 'ativa';
```

E `inserir_reserva` abre `BEGIN IMMEDIATE` (serializa escritores) e trata a violação como resultado normal:

```python
except sqlite3.IntegrityError as e:
    msg = str(e)
    if "reservas.area" in msg:
        conn.execute("ROLLBACK")
        return {"status": "indisponivel", "area": area_id, "data": data}
```

A conferência `db.data_ocupada` em `reservar_area` serve só para recusar cedo e não pedir confirmação à toa; quem garante a regra é o índice. Na disputa, os dois recebem `200`: o perdedor recebe `indisponivel` da tool e o assistente explica. Cancelar uma reserva (`status = 'cancelada'`) libera o índice parcial.

## Como rodar

Pré-requisitos:

- Python 3.12 ou superior
- [uv](https://docs.astral.sh/uv/)
- Chave do Google AI Studio (`GOOGLE_API_KEY`). Recomenda-se conta com faturamento ativo, porque o fluxo faz muitas chamadas ao modelo e a cota gratuita estoura.

Configuração:

```bash
cp .env.example .env     # preencha GOOGLE_API_KEY
uv sync
```

Variáveis do `.env`:

| Variável | Obrigatória | Descrição |
|---|---|---|
| `GOOGLE_API_KEY` | sim | chave do AI Studio |
| `GEMINI_MODEL` | não | modelo; padrão `gemini-3.5-flash` |

Chamadas ao modelo têm retentativa automática em 429/500/503/504 (`HttpRetryOptions` em `aurora/agents.py`).

Restaurar os dados iniciais:

```bash
uv run aurora-restaurar             # reservas e visitantes voltam ao estado de dados/; mantém as sessões
uv run aurora-restaurar --sessoes   # também apaga as sessões de conversa
```

Subir a API (porta 8000, `http://localhost:8000`):

```bash
uv run aurora-api
```

Os dados vivem em `var/` (`condominio.db` e `sessoes.db`, SQLite, fora do git). Os arquivos de `dados/` são só a carga inicial e nunca são modificados.

Testes:

```bash
uv run pytest
```

Roteiro E2E do avaliador (precisa da API rodando e de chave válida):

```bash
uv run aurora-restaurar --sessoes && uv run aurora-api      # terminal A
uv run python scripts/fluxo_avaliador.py parte1             # passos 1 a 12
# Ctrl+C no terminal A, suba de novo com `uv run aurora-api` (sem restaurar)
uv run python scripts/fluxo_avaliador.py parte2             # passos 13 e 14
```

Cada passo imprime OK ou FALHOU; o script sai com código 1 se algum falhar.
