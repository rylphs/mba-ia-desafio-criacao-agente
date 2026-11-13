# Design — Assistente do Residencial Aurora

Data: 2026-11-13 · Enunciado completo: `README.md` do repositório base (cópia local não versionada em `DESAFIO.md`)

## Intenção

Entrega do desafio do MBA: um assistente em Google ADK, exposto por uma API FastAPI em `http://localhost:8000`, que reserva áreas, cancela reservas, autoriza visitantes e responde dúvidas do regulamento **sem que nenhuma mensagem fure as regras**. A correção é feita por um avaliador que roda os 15 passos do enunciado contra a API e inspeciona o repositório. A construção é feita em par: cada decisão precisa ser explicável e defensável pelo autor.

Filosofia: o modelo decide o caminho, o código decide o que é permitido.

## Objetivos

- Contrato da API exatamente como no enunciado (rotas, campos, status `201/200/404/409`).
- As cinco garantias implementadas em código, não em prompt.
- Agente principal + especialistas, com reservas e visitantes lidos e gravados só por tools.
- Um comando para subir a API e outro para restaurar os dados; README com Arquitetura, Garantias (apontando arquivos e trechos reais) e Como rodar.

## Não-objetivos

Interface visual, autenticação real, pagamento, regras de antecedência/horário/capacidade, testes automatizados, evals e deploy (todos fora de escopo no enunciado).

## Anti-objetivos (falharia mesmo "funcionando")

- Aprovação que devolve `200` sem a ação ter executado (falha silenciosa da retomada).
- Qualquer dado de outro apartamento (código, nome de visitante, número do apartamento dono) chegando à resposta ou aos eventos da sessão.
- Texto do regulamento de outros capítulos nos eventos da sessão principal, ou regulamento nas instruções do agente principal.
- Regra que só existe no prompt.

## Restrições

- Python 3.12, projeto uv (`pyproject.toml` + `uv.lock` versionados).
- `google-adk[db]==2.14.1` fixado exato (série 2, ≥ 2.2.0). O extra `db` traz SQLAlchemy, exigido pelo `DatabaseSessionService`.
- Modelos Gemini via chave do AI Studio (`GOOGLE_API_KEY`), conta com faturamento ativo.
- Arquivos de `dados/` intocados. `.env` fora do Git, `.env.example` versionado sem valores.

## Abordagens consideradas

**Confirmação (Garantia 1)**

- **A — Confirmação nativa do ADK** *(escolhida)*: a tool chama `tool_context.request_confirmation(...)`; a rota `/confirmacoes` devolve ao Runner um `FunctionResponse` `adk_request_confirmation` com `{"confirmed": bool}` e o Runner retoma o especialista, que reexecuta a tool já confirmada.
- **B — Tabela de pendências própria**: a tool só registra a intenção; a rota executa direto em código. Mais robusta, mas contorna o mecanismo que o desafio quer ver exercitado.

Escolhida A **com proteções**, porque é o conceito do curso e o spike mostrou que funciona com SQLite. Proteções: (1) a API decide o `409` antes de chamar o Runner; (2) gravação idempotente pela chave da chamada original; (3) depois de toda retomada, a API confere se a tool respondeu à chamada original — se não respondeu, devolve erro explícito em vez de `200`.

**Acionamento dos especialistas**

- **Transferência (`sub_agents`, modo chat)** *(escolhida)*: menos chamadas ao modelo; o especialista conduz até o assunto mudar e então transfere de volta.
- **`mode="task"`**: o principal sempre responde, mas custa ~2 chamadas extras por ação (`finish_task` + resposta do principal).
- **`AgentTool`**: descartado para quem pede confirmação — o ADK descarta a sessão aninhada e não retoma (`tools/agent_tool.py`). Usado só para o regulamento, onde esse isolamento é desejável.

### Evidência do spike (descartável, fora do repositório)

ADK 2.14.1, `DatabaseSessionService` SQLite, `gemini-3.5-flash`; pedido num processo, aprovação noutro (simula reinício):

| Combinação | Pendência | Aprovação após reinício | Reenvio do mesmo id |
|---|---|---|---|
| transferência + retomada ligada | no especialista | retomou o especialista, gravou 1× | ADK não regravou |
| transferência + retomada desligada | idem | idem | idem |
| task + retomada ligada | idem | gravou 1×, principal respondeu | idem |
| task + retomada desligada | idem | idem | idem |

Achados que moldam o design: o ADK **aceita** reenvio de id já respondido (às vezes chama o modelo de novo, que repete "reserva efetuada") → o `409` é nosso; na retomada a tool roda de novo com os **argumentos originais** da chamada → `detalhes` vem desses argumentos; o `payload` enviado na resposta é irrelevante.

## Arquitetura decidida

### Agentes

| Agente | Tipo / acionamento | Responsabilidade | Tools |
|---|---|---|---|
| `aurora` (principal) | `root_agent` do `App` | Acolhe, entende o pedido, transfere para o especialista certo; chama o regulamento como tool. **Sem regulamento nas instruções.** | `AgentTool(regulamento)`; `sub_agents=[reservas, visitantes]` |
| `reservas` | sub-agente por transferência | Listar áreas, consultar disponibilidade, reservar, listar e cancelar as reservas do próprio apartamento. Transfere de volta ao principal quando o assunto muda. | `listar_areas`, `verificar_disponibilidade`, `reservar_area`, `listar_minhas_reservas`, `cancelar_reserva` |
| `visitantes` | sub-agente por transferência | Autorizar visitante e listar os visitantes do próprio apartamento. | `autorizar_visitante`, `listar_meus_visitantes` |
| `regulamento` | `AgentTool` do principal | Responder dúvidas lendo só o capítulo pertinente. Roda em sessão aninhada efêmera: só a resposta final entra nos eventos da sessão principal. | `ler_capitulo(numero)` |

Especialistas podem transferir só para o pai (`disallow_transfer_to_peers=True`); o principal faz o roteamento.

`App(name="aurora", root_agent=aurora, resumability_config=ResumabilityConfig(is_resumable=True))`.

### Modelo

`gemini-3.5-flash` por padrão, configurável por `GEMINI_MODEL` no `.env`. Cada agente usa `Gemini(model=..., retry_options=types.HttpRetryOptions(...))` com algumas tentativas e espera exponencial para absorver `429/503`.

### Armazenamento

Dois arquivos SQLite em `var/` (no `.gitignore`):

- `var/sessoes.db` — `DatabaseSessionService(db_url="sqlite+aiosqlite:///var/sessoes.db")`. Garante a Garantia 3 para conversas.
- `var/condominio.db` — dados de negócio, acessados com `sqlite3` da biblioteca padrão em transações curtas (`BEGIN IMMEDIATE`, `busy_timeout`).

Esquema de `condominio.db`:

```sql
apartamentos(numero TEXT PRIMARY KEY, morador TEXT)
areas(id TEXT PRIMARY KEY, nome TEXT, taxa REAL)
reservas(
  codigo TEXT PRIMARY KEY,           -- nunca reutilizado: cancelamento é lógico
  apartamento TEXT NOT NULL,
  area TEXT NOT NULL REFERENCES areas(id),
  data TEXT NOT NULL,                -- AAAA-MM-DD
  status TEXT NOT NULL DEFAULT 'ativa',  -- 'ativa' | 'cancelada'
  origem TEXT UNIQUE                 -- id da chamada de tool que criou (idempotência); NULL nas iniciais
)
CREATE UNIQUE INDEX reserva_unica_ativa ON reservas(area, data) WHERE status = 'ativa';
visitantes(
  id INTEGER PRIMARY KEY,
  apartamento TEXT NOT NULL, nome TEXT NOT NULL, data TEXT NOT NULL,
  origem TEXT UNIQUE
)
sessoes(session_id TEXT PRIMARY KEY, apartamento TEXT NOT NULL)  -- mapeia id → user_id do ADK
```

### Onde mora cada garantia

**Garantia 1 — confirmação** (`aurora/tools/reservas.py`, `aurora/tools/visitantes.py`, `aurora/confirmacoes.py`, `aurora/api.py`)
- A decisão de pedir confirmação é do código da tool: `reservar_area` consulta a taxa da área no banco e só chama `request_confirmation` se `taxa > 0`; `autorizar_visitante` sempre pede. Quadra (taxa 0) grava direto; cancelamento grava direto.
- Antes de pedir confirmação, `reservar_area` valida área/data e já recusa data ocupada (sem gerar pendência inútil).
- Sem `tool_confirmation` → pede e retorna `pendente`. `confirmed=False` → retorna `negado` sem gravar. `confirmed=True` → grava.
- Pendências: `confirmacoes.py` varre os eventos da sessão e lista chamadas `adk_request_confirmation` sem `FunctionResponse` correspondente. Cada uma vira `{"id": <id da chamada adk_request_confirmation>, "acao": <nome da tool original>, "detalhes": <args da chamada original>}`.
- `POST /confirmacoes`: com o lock da sessão, recalcula as pendências; `id` ausente → `409` sem tocar no Runner. Presente → envia `FunctionResponse(id, name="adk_request_confirmation", response={"confirmed": ...})`.
- Pós-checagem: após a execução, se não existe `FunctionResponse` da tool original (id em `originalFunctionCall.id`) posterior à resposta, a API responde `500` com mensagem explícita. (Resultado "indisponível" conta como executado.)
- Mensagens como "já estou confirmando" não têm efeito: só a rota envia o `FunctionResponse`.

**Garantia 2 — sessão pertence a um apartamento** (`aurora/api.py`, `aurora/tools/*.py`)
- `POST /sessoes` cria a sessão ADK com `user_id=<apartamento>` e `state={"apartamento": "<numero>"}`; grava o mapeamento em `sessoes`.
- Nenhuma tool tem parâmetro de apartamento. Todas leem `tool_context.state["apartamento"]`. Nenhuma tool ou callback escreve essa chave.
- Consultas filtram por apartamento da sessão: `listar_minhas_reservas`, `listar_meus_visitantes`, `cancelar_reserva` (`WHERE codigo=? AND apartamento=?` ou por área+data do próprio apartamento). Reserva alheia → "não encontrei reserva sua com esses dados", sem eco de código ou dono.
- `verificar_disponibilidade` e a recusa de `reservar_area` retornam só `livre`/`ocupada`, nunca código ou apartamento.

**Garantia 3 — reinício** — `DatabaseSessionService` + `condominio.db` em disco. A API não guarda estado em memória além de locks.

**Garantia 4 — regulamento consultado** (`aurora/regulamento.py`, `aurora/agents.py`)
- `regulamento.py` divide `dados/regulamento.md` por `## Capítulo` (14 capítulos).
- O agente `regulamento` recebe nas instruções **apenas os títulos** dos capítulos e usa `ler_capitulo(numero)` para ler um capítulo inteiro.
- Ele é `AgentTool`: suas chamadas de tool e o texto do capítulo ficam na sessão aninhada efêmera; na sessão principal entra só a pergunta (args) e a resposta final.
- A instrução do principal não contém regulamento; ele só sabe que existe a tool para dúvidas de regras.
- Validação: após o passo 12, conferir que os eventos de S1 não contêm frases de outros capítulos (ex.: Capítulo VIII).

**Garantia 5 — corrida de reservas** (`aurora/db.py`)
- `inserir_reserva` faz `INSERT` dentro de `BEGIN IMMEDIATE`; o índice único parcial `reserva_unica_ativa` é a garantia no instante da gravação. `IntegrityError` no índice → retorna `indisponivel` (resposta normal da tool, a API responde `200`).
- Idempotência: `origem = tool_context.function_call_id` (`UNIQUE`). Se já existe linha com essa origem, retorna a reserva existente em vez de inserir.
- Código: `RSV-` + 6 caracteres hexadecimais aleatórios, com nova tentativa se colidir com a `PRIMARY KEY` (que inclui canceladas). Nunca repete `RSV-1377/4821/2950` nem códigos cancelados.

### API (`aurora/api.py`)

- `POST /sessoes` → `201 {"session_id"}`.
- `POST /sessoes/{id}/mensagens` → `200 {"resposta", "confirmacoes_pendentes"}`; `404` se a sessão não existe.
- `POST /sessoes/{id}/confirmacoes` → `200` mesmo formato; `409` se o id não está pendente; `404`.
- `GET /sessoes/{id}/eventos` → lista de `event.model_dump(mode="json", by_alias=True, exclude_none=True)`, em ordem; `404`.
- `GET /apartamentos/{n}/reservas` → `[{"codigo","area","data"}]` só ativas, ordenadas por data.
- `GET /apartamentos/{n}/visitantes` → `[{"nome","data"}]`.
- `resposta` = concatenação dos textos (não-thought) de eventos de agentes produzidos nesta execução; pode ser `""`.
- Um `asyncio.Lock` por sessão serializa mensagens e confirmações da mesma sessão. Sessões diferentes rodam em paralelo (o passo 14 depende disso e do índice único).
- Erros do modelo após retentativas → `502` com mensagem; nunca grava nada fora das tools.
- Um único processo uvicorn (sem `--workers`), porque os locks são em memória.
- Na subida, se `condominio.db` não existir, cria esquema e carrega os dados iniciais.

### Comandos

- Restaurar: `uv run aurora-restaurar` — recria `reservas` e `visitantes` a partir de `dados/*.json` (e recarrega apartamentos/áreas). **Mantém as sessões** (decisão minha: o enunciado deixa livre; manter evita apagar conversas sem querer). Uma flag `--sessoes` apaga também `var/sessoes.db`.
- Subir: `uv run aurora-api` (entry point que chama uvicorn em `0.0.0.0:8000`).

### Estrutura do projeto

```
pyproject.toml  uv.lock  .env.example  .gitignore  README.md
dados/                      # intocado
aurora/
  __init__.py
  config.py                 # caminhos, .env, nome do modelo
  db.py                     # conexão, esquema, seed/restore, repositório (inclui Garantia 5)
  regulamento.py            # parser de capítulos
  tools/reservas.py         # tools de reservas (Garantias 1, 2, 5)
  tools/visitantes.py       # tools de visitantes (Garantias 1, 2)
  tools/regulamento.py      # ler_capitulo
  agents.py                 # árvore de agentes + App
  confirmacoes.py           # pendências, resposta, pós-checagem
  api.py                    # FastAPI + Runner
  restaurar.py              # CLI de restauração
docs/superpowers/specs/     # este documento
var/                        # bancos SQLite (ignorado)
```

## Decisões minhas para você revisar

1. Dois arquivos SQLite separados (sessões vs. negócio) em vez de um só — separa o que é do ADK do que é nosso e facilita restaurar.
2. Restauração mantém sessões por padrão; `--sessoes` apaga.
3. Formato do código: `RSV-` + 6 hex aleatórios.
4. Regulamento: o especialista escolhe o capítulo pelos títulos, em vez de busca por palavras-chave — mais simples e o modelo acerta melhor.
5. `DESAFIO.md` **não** é versionado (cópia local de leitura, no `.gitignore`); `README.md` é reescrito. *(decidido pelo autor)*
6. Os arquivos de skill que você instalou (`.agents/`, `.claude/skills/`, `skills-lock.json`) ficam fora do fork, via `.gitignore`, a menos que você prefira versioná-los.
7. Nova mensagem com confirmação pendente: processada normalmente; a pendência antiga continua respondível pela rota (o enunciado deixa livre, e nada executa sem confirmação).

## Deixado para quem implementa

- Redação exata das instruções dos agentes (sempre em português, objetivas; regras críticas ficam no código, o prompt só orienta).
- Mensagens de retorno das tools e textos de erro.
- Parâmetros exatos de retentativa.
- Mapeamento de `acao` para nomes amigáveis (ex.: `reservar_area` → `reservar_area`; livre pelo contrato).
- Um roteiro manual (`scripts/` ou seção do README) que reproduz o fluxo do avaliador com `curl`, para validação antes da entrega.
