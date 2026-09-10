# Agent Framework + Postgres MCP Server

**Um exemplo pequeno e reprodutível: um agente de IA local que lê *e escreve* num banco Azure PostgreSQL, usando o [Microsoft Agent Framework](https://learn.microsoft.com/pt-br/agent-framework/overview/?pivots=programming-language-python) e o [Postgres MCP Server](https://github.com/crystaldba/postgres-mcp).**

*[Read in English](README.md)*

Ninguém escreve SQL para o agente. Você descreve um resultado em linguagem natural, o modelo escreve o SQL, e um servidor MCP executa contra o banco.

```mermaid
flowchart LR
    User["👤 Você<br/><small>CLI</small>"]
    Agent["🤖 Agent<br/><small>Agent Framework<br/>+ MCPStdioTool</small>"]
    MCP["🔌 postgres-mcp<br/><small>iniciado pelo uvx</small>"]
    DB[("🐘 Azure<br/>PostgreSQL")]
    AOAI["🧠 Azure OpenAI<br/><small>gpt-4.1</small>"]

    User <--> Agent
    Agent <-->|"stdio<br/>JSON-RPC"| MCP
    MCP <-->|"TCP 5432<br/>+ TLS"| DB
    Agent <-->|HTTPS| AOAI
```

O banco de exemplo é de uma operadora de telecom fictícia monitorando seu backbone de fibra óptica: sites, enlaces, alertas e anotações de técnicos.

---

## Duas formas de usar este repositório

**A. Percurso completo** — provisione um Azure PostgreSQL descartável, carregue os dados de telecom de exemplo e explore. Comece pelo [Início rápido](#início-rápido).

**B. Traga o seu** — você já tem um banco PostgreSQL e um deployment do Azure OpenAI, e só quer apontar o agente para eles. **Sem nenhuma alteração de código**, apenas o `.env`. Comece por **[Usando seu próprio banco e modelo](docs/bring-your-own.pt-BR.md)**.

---

## Sumário

- [O que você vai aprender](#o-que-você-vai-aprender)
- [Pré-requisitos](#pré-requisitos)
- [Início rápido](#início-rápido)
- [Usando seu próprio banco](#usando-seu-próprio-banco)
- [O que cada arquivo faz](#o-que-cada-arquivo-faz)
- [Os três exemplos](#os-três-exemplos)
- [Coisas para experimentar](#coisas-para-experimentar)
- [Como funciona](#como-funciona)
- [Segurança](#segurança)
- [Custo e limpeza](#custo-e-limpeza)
- [Solução de problemas](#solução-de-problemas)
- [Próximos passos](#próximos-passos)

---

## O que você vai aprender

Ao final, você será capaz de:

1. Conectar um agente do Microsoft Agent Framework a um deployment do Azure OpenAI usando Entra ID, sem nenhuma API key.
2. Dar acesso ao banco de dados para esse agente através de um servidor MCP, sem escrever nenhuma função de ferramenta.
3. Deixar o agente executar `SELECT`, `INSERT` e `UPDATE` que ele mesmo escreveu, e comprovar que as escritas persistiram.
4. Manter contexto entre turnos com um `AgentSession`, para que perguntas de acompanhamento como *"agora resolva esse aí"* funcionem.
5. Colocar um humano no circuito, de forma que nenhum comando rode sem aprovação.
6. Provisionar e destruir os recursos no Azure com um comando.

> **Atenção se você já viu outros exemplos.** A API do Agent Framework mudou entre os betas `1.0.0bYYMMDD` e o release 1.x GA. A maioria dos posts e exemplos na internet — incluindo os que inspiraram este repositório — usa os nomes antigos e **não roda hoje**. Veja [Mudanças de API](#mudanças-de-api-desde-os-betas).

---

## Pré-requisitos

| Requisito | Observações |
|---|---|
| **Python 3.10+** | 3.11 ou mais novo é recomendado. |
| **[uv](https://docs.astral.sh/uv/)** | Fornece o `uvx`, usado para rodar o servidor MCP do Postgres num ambiente isolado. Instalação: `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` (Windows) ou `curl -LsSf https://astral.sh/uv/install.sh \| sh`. |
| **[Azure CLI](https://aka.ms/installazurecli)** | Usado no provisionamento e no login com Entra ID. |
| **Uma subscription Azure** | Com permissão para criar um PostgreSQL flexible server. |
| **Um deployment do Azure OpenAI / Foundry** | Qualquer modelo com suporte a tool calling: `gpt-4.1`, `gpt-4.1-mini`, `gpt-4o`, ... Você precisa da role **Cognitive Services OpenAI User** no recurso. |

Você **não** precisa de `psql`, Docker, nem de conhecimento prévio de MCP.

---

## Início rápido

### 1. Baixe o código e instale as dependências

```powershell
git clone <este-repo> postgres-maf
cd postgres-maf

python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Windows
# source .venv/bin/activate           # macOS / Linux

pip install -r requirements.txt
```

### 2. Faça login no Azure

```powershell
az login
```

### 3. Provisione o PostgreSQL

```powershell
pwsh infra/provision.ps1              # Windows
# bash infra/provision.sh             # macOS / Linux
```

Isso cria um resource group e o PostgreSQL flexible server mais barato possível (Burstable `Standard_B1ms`, 32 GB), abre o firewall para a sua máquina e escreve um `.env` pronto para uso.

O script também resolve duas armadilhas comuns: subscriptions que estão **restritas de provisionar PostgreSQL em certas regiões** (ele testa e sugere regiões que funcionam) e redes onde **o seu IP real de saída é diferente do que os serviços de consulta de IP reportam** (ele pergunta diretamente ao PostgreSQL). Veja [Como funciona a detecção de firewall](docs/architecture.pt-BR.md#detecção-de-firewall).

> Abra o `.env` e confira se `AZURE_OPENAI_ENDPOINT` e `AZURE_OPENAI_DEPLOYMENT` correspondem a um deployment que você realmente tem. O script chuta; ele pode chutar errado.

### 4. Carregue os dados de exemplo

```powershell
python -m infra.seed
```

```
Verifying:
  sites                  6
  fiber_links            8
  fiber_alerts           25
  alert_notes            5
  open critical alerts   3
```

### 5. Converse com o agente

```powershell
python -m src.main
```

```
you > Quais alertas críticos estão abertos agora?

agent > Existem três alertas críticos abertos no momento:

| alert_id | link_code       | horas_aberto |
|----------|-----------------|--------------|
| 1        | LNK-SPO-BHZ-01  | 3,2          |
| 21       | LNK-CWB-POA-01  | 2,2          |
| 3        | LNK-SPO-RIO-01  | 0,9          |

--- what the agent did (1 tool call(s)) ---
[1] execute_sql
    SELECT a.alert_id, l.code AS link_code,
           EXTRACT(EPOCH FROM (now() - a.opened_at))/3600 AS hours_open
    FROM fiber_alerts a
    JOIN fiber_links l ON a.link_id = l.link_id
    WHERE a.status = 'open' AND a.severity = 'critical'
    ORDER BY a.opened_at ASC;
    -> [{'alert_id': 1, 'link_code': 'LNK-SPO-BHZ-01', ...}]
--- end of trace ---
```

O trace embaixo de cada resposta é o ponto central do exemplo: **nada fica escondido**. Foi o modelo que escreveu aquele SQL.

---

## Usando seu próprio banco

Tudo acima assume que você rodou o script de provisionamento. Se você já tem um banco PostgreSQL e um deployment do Azure OpenAI, pode pular tudo isso — **sem nenhuma alteração de código**.

```bash
pip install -r requirements.txt
cp .env.example .env
```

Depois edite o `.env`:

```ini
# Seu banco - Azure, on-premises, Docker, RDS, Neon, Supabase, qualquer um
PGHOST=meu-servidor.exemplo.com
PGDATABASE=meu_banco
PGUSER=meu_usuario
PGPASSWORD=minha_senha
PGSSLMODE=require            # `disable` para um Postgres local no Docker

# Seu modelo
AZURE_OPENAI_ENDPOINT=https://meu-recurso.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=nome-do-meu-deployment
AZURE_OPENAI_API_VERSION=

# Ensine ao agente o SEU schema em vez do exemplo do FiberOps
AGENT_INSTRUCTIONS_FILE=prompts/auto-discover-schema.md

# Comece somente-leitura em dados que você preza
POSTGRES_MCP_ACCESS_MODE=restricted
```

```bash
az login
python -m src.main
```

**O passo que realmente importa é o `AGENT_INSTRUCTIONS_FILE`.** Por padrão o agente usa um prompt que descreve o schema de exemplo do FiberOps; aponte-o para o seu banco sem mudar isso e ele vai procurar tabelas que não existem. Você tem duas opções:

| | |
|---|---|
| `prompts/auto-discover-schema.md` | O agente inspeciona seu schema em tempo de execução. Esforço zero, funciona em qualquer lugar, custa algumas chamadas extras ao modelo. |
| `prompts/template.md` | Copie, descreva suas tabelas e aponte o `AGENT_INSTRUCTIONS_FILE` para a sua cópia. Melhores resultados — e o próprio agente pode escrever o primeiro rascunho. |

A CLI mostra qual prompt está ativo na inicialização, então você nunca fica no escuro.

Os scripts em `src/examples/` foram escritos para os dados de exemplo e se recusam a rodar no seu banco, em vez de fazer algo sem sentido ou destrutivo.

**→ Guia completo, incluindo segurança, permissões e um setup só com Docker: [docs/bring-your-own.pt-BR.md](docs/bring-your-own.pt-BR.md)**

---

## O que cada arquivo faz

```
postgres-maf/
├── src/
│   ├── config.py       Carrega e valida o .env. Python puro, sem framework.
│   ├── agent.py        >>> O ARQUIVO QUE VALE A PENA LER <<<
│   │                   Monta o chat client, a ferramenta MCP e o Agent.
│   ├── main.py         CLI interativa: streaming, sessões, slash commands.
│   ├── trace.py        Extrai as tool calls da resposta para você ver o SQL.
│   └── examples/
│       ├── read_only.py       Exemplo 1 - apenas consultas
│       ├── read_write.py      Exemplo 2 - lê, escreve, comprova a persistência
│       └── human_approval.py  Exemplo 3 - aprove cada comando
├── infra/                      SÓ é necessário para criar o ambiente de demo
│   ├── provision.ps1 / .sh    Cria os recursos no Azure + escreve o .env
│   ├── seed.sql               Schema de telecom e dados de exemplo
│   ├── seed.py                Aplica o seed.sql (sem precisar de psql)
│   └── teardown.ps1 / .sh     Para ou apaga tudo
├── prompts/                    System prompts - troque pelo seu schema
│   ├── auto-discover-schema.md  Agente inspeciona qualquer banco em runtime
│   └── template.md              Copie e descreva suas próprias tabelas
├── docs/
│   ├── bring-your-own.pt-BR.md    Usando seu próprio banco e modelo
│   ├── architecture.pt-BR.md      Como as peças se encaixam
│   └── troubleshooting.pt-BR.md   Todos os erros que enfrentamos, e a solução
├── .env.example               Todas as configurações, documentadas
└── requirements.txt / pyproject.toml
```

Comece por [`src/agent.py`](src/agent.py). São cerca de 80 linhas de código de verdade; o resto é explicação.

---

## Os três exemplos

Rode-os nesta ordem.

### Exemplo 1 — leitura

```powershell
python -m src.examples.read_only
```

Quatro perguntas independentes, de uma contagem simples até o tempo médio de resolução por severidade. Nenhum `session=` é passado, então cada pergunta é isolada.

### Exemplo 2 — escrita

```powershell
python -m src.examples.read_write
```

O exemplo que importa. Quatro turnos compartilhando **uma sessão**:

1. **Leitura** — encontra o alerta crítico aberto mais antigo.
2. **Escrita** — adiciona uma anotação de técnico *naquele* alerta.
3. **Escrita** — muda o status *daquele* alerta para `acknowledged`.
4. **Leitura** — relê o alerta e lista as anotações, comprovando que as escritas persistiram.

Os passos 2 e 3 funcionam sem repetir o id do alerta porque a sessão carrega o contexto.

Você pode restaurar os dados a qualquer momento com `python -m infra.seed`.

### Exemplo 3 — humano no circuito

```powershell
python -m src.examples.human_approval
```

Muda a ferramenta MCP para `approval_mode="always_require"` e pede ao agente para apagar alertas antigos. Cada comando é mostrado a você antes:

```
APPROVAL REQUIRED - the agent wants to run this:
  tool: execute_sql
  SQL:
      DELETE FROM fiber_alerts
      WHERE status = 'resolved' AND resolved_at < NOW() - INTERVAL '7 days'
      RETURNING alert_id;
Allow it? [y/N]
```

Responda `n` e nada acontece. Esse é o padrão que você quer antes de apontar um agente para qualquer coisa real.

---

## Coisas para experimentar

Com o `python -m src.main` rodando:

**Leitura**
- Quais enlaces estão degradados, e quantos alertas cada um gerou?
- Qual a atenuação média dos alertas abertos, por tipo de alerta?
- Me mostre todos os alertas em enlaces com mais de 700 km.

**Escrita**
- Adicione uma nota no alerta 3 dizendo que a equipe de campo confirmou rompimento perto de Resende.
- Reconheça todos os alertas abertos com severidade `low`.
- Resolva o alerta 24 e explique exatamente o que você mudou.

**Múltiplos turnos** (onde as sessões mostram seu valor)
- `Qual enlace tem mais alertas abertos?`
- `Adicione uma nota em todos eles dizendo que uma equipe de emergência foi acionada.`
- `Agora me mostre essas notas.`

**Além do CRUD** — o `postgres-mcp` também traz ferramentas de análise:
- Explique o plano de execução da contagem de alertas abertos por enlace.
- Está faltando algum índice para essa consulta?
- Verifique a saúde deste banco de dados.

O agente responde no idioma em que você escreve.

---

## Como funciona

Três objetos, montados em [`src/agent.py`](src/agent.py).

### 1. O chat client — o cérebro

```python
from agent_framework.openai import OpenAIChatClient
from azure.identity import AzureCliCredential

client = OpenAIChatClient(
    model="gpt-4.1",                                   # nome do seu DEPLOYMENT
    azure_endpoint="https://<res>.openai.azure.com/",  # é isso que seleciona o Azure
    credential=AzureCliCredential(),                   # Entra ID, sem chaves
)
```

Duas pegadinhas:

- A classe é `OpenAIChatClient`, e **não** `AzureOpenAIChatClient`. Passar `azure_endpoint=` é o que aponta o cliente para o Azure.
- O parâmetro `model=` espera o **nome do deployment**, que no Azure frequentemente é diferente do id do modelo.

### 2. A ferramenta — as mãos

```python
from agent_framework import MCPStdioTool

pg = MCPStdioTool(
    name="postgres",
    command="uvx",
    args=["--with", "mcp<2", "postgres-mcp", "--access-mode=unrestricted"],
    env={"DATABASE_URI": "postgresql://user:senha@host:5432/db?sslmode=require"},
    approval_mode="never_require",
)
```

O `uvx` baixa e roda o servidor MCP no ambiente dele. O Agent Framework o inicia como processo filho, conversa MCP via stdin/stdout e descobre automaticamente todas as ferramentas oferecidas — `execute_sql`, `list_objects`, `get_object_details`, `explain_query`, `analyze_db_health` e outras. **Nós não descrevemos nenhuma delas.** O MCP anuncia nomes, descrições e schemas JSON, e o framework repassa tudo isso para o modelo.

A string de conexão vai em `env`, nunca em `args`: argumentos de linha de comando são visíveis para qualquer processo que consiga listar a tabela de processos.

O pin `--with "mcp<2"` é obrigatório. O `postgres-mcp` foi feito para a v1 do SDK do MCP; sem o pin você recebe um confuso erro `Connection closed`. Detalhes em [solução de problemas](docs/troubleshooting.pt-BR.md#mcp-server-failed-to-initialize-connection-closed).

### 3. O agente — juntando tudo

```python
from agent_framework import Agent

agent = Agent(client=client, instructions=PROMPT_DO_SCHEMA, tools=pg)

async with agent:                      # <-- inicia o processo filho do MCP
    session = agent.create_session()   # <-- memória da conversa
    result = await agent.run("Quantos alertas estão abertos?", session=session)
    print(result.text)
```

**O `async with` não é opcional.** É ele que inicia e encerra o servidor MCP. Se você esquecer, o modelo vai dizer que não tem como acessar o banco — o erro mais comum com ferramentas MCP.

O system prompt em `agent.py` descreve todo o schema. O servidor MCP *poderia* descobrir isso sozinho, mas declarar de antemão significa menos idas e vindas, muito menos nomes de coluna inventados, e um lugar natural para regras que o schema não consegue expressar (*"ao resolver um alerta você também precisa preencher `resolved_at`"*).

### Mudanças de API desde os betas

Se você está portando código de um exemplo antigo:

| API beta (maioria dos exemplos online) | 1.x GA (este repositório) |
|---|---|
| `from agent_framework.azure import AzureOpenAIChatClient` | `from agent_framework.openai import OpenAIChatClient` |
| `AzureOpenAIChatClient(endpoint=..., deployment_name=...)` | `OpenAIChatClient(azure_endpoint=..., model=...)` |
| `client.create_agent(...)` | `Agent(client=..., ...)` |
| `agent.run_stream(...)` | `agent.run(..., stream=True)` |
| `AgentThread`, `agent.get_new_thread()` | `AgentSession`, `agent.create_session()` |
| `MCPSSETool` | removido — use `MCPStdioTool` ou `MCPStreamableHTTPTool` |

---

## Segurança

Este é um exemplo didático. Ele é deliberadamente permissivo, e você não deve copiar seus padrões para nada real.

**`--access-mode=unrestricted` significa que o agente pode dar `DROP TABLE`.** Combinado com `approval_mode="never_require"`, um prompt infeliz pode destruir dados sem ninguém no circuito. Esse é um risco aceitável para um banco de demonstração descartável, e para mais nada.

Antes de apontar um agente para dados que você preza, aplique estas medidas em ordem de eficácia:

1. **Uma role restrita no banco.** `GRANT SELECT, INSERT ON ...` e nada mais. É a única barreira que um LLM não consegue contornar com conversa.
2. **`POSTGRES_MCP_ACCESS_MODE=restricted`.** O servidor MCP passa a permitir apenas transações somente-leitura.
3. **`POSTGRES_MCP_APPROVAL_MODE=always_require`.** Um humano aprova cada comando — veja o Exemplo 3.
4. **`allowed_tools=[...]`** no `MCPStdioTool`, escondendo tudo exceto as chamadas que você quer.

Além disso:

- O `.env` guarda a senha do banco e está no `.gitignore`. Mantenha assim.
- Prefira Entra ID (`az login`) em vez de `AZURE_OPENAI_API_KEY`; o exemplo já usa Entra ID por padrão.
- O script de provisionamento libera o firewall para uma faixa /24 por conveniência. Restrinja a um único IP para qualquer coisa duradoura.

---

## Custo e limpeza

Um servidor Burstable `Standard_B1ms` com 32 GB custa aproximadamente **USD 15–20 por mês** se ficar ligado. O consumo de Azure OpenAI neste exemplo é de alguns centavos.

**Pause entre as sessões** (mantém seus dados; o Azure religa sozinho depois de 7 dias):

```powershell
pwsh infra/teardown.ps1 -Stop
# bash infra/teardown.sh --stop
```

**Apague tudo:**

```powershell
pwsh infra/teardown.ps1
# bash infra/teardown.sh
```

---

## Solução de problemas

A versão curta de [docs/troubleshooting.pt-BR.md](docs/troubleshooting.pt-BR.md):

| Sintoma | Causa |
|---|---|
| `MCP server ... failed to initialize: Connection closed` | Falta o `--with "mcp<2"`, ou o `uvx` não está no PATH. |
| `400 BadRequest: API version not supported` | `AZURE_OPENAI_API_VERSION` está fixado num valor antigo. Deixe **vazio**. |
| `connection timeout expired` | A regra de firewall não corresponde ao seu IP real de saída. Rode o script de provisionamento de novo. |
| `The location is restricted from performing this operation` | Sua subscription não pode criar PostgreSQL naquela região. O script sugere alternativas. |
| O agente diz que não consegue acessar o banco | Você esqueceu do `async with agent:`. |
| `401` / `PermissionDenied` do Azure OpenAI | Você precisa da role **Cognitive Services OpenAI User** e de um novo `az login`. |
| Muitos `INFO Processing request of type ...` | Normal. É o servidor MCP escrevendo log no stderr. |

---

## Próximos passos

- **Adicione suas próprias ferramentas.** O parâmetro `tools=` aceita funções Python comuns junto com a ferramenta MCP; o Agent Framework as converte em definições de ferramenta automaticamente.
- **Adicione um segundo servidor MCP.** Passe uma lista em `tools=`. O [Microsoft Learn MCP server](https://learn.microsoft.com/api/mcp) funciona bem com `MCPStreamableHTTPTool`.
- **Busca vetorial.** Habilite o `pgvector` no servidor e dê ao agente busca semântica sobre as descrições dos alertas — veja [Agentes de IA no Azure Database for PostgreSQL](https://learn.microsoft.com/pt-br/azure/postgresql/azure-ai/generative-ai-agents).
- **Persista as conversas.** O Agent Framework traz `SessionStore` / `FileSessionStore`; guarde as sessões no próprio Postgres.
- **Multi-agente.** O Agent Framework tem workflows e orquestrações para agentes que passam trabalho entre si.

### Referências

- [Microsoft Agent Framework](https://learn.microsoft.com/pt-br/agent-framework/overview/?pivots=programming-language-python)
- [Agentes de IA no Azure Database for PostgreSQL flexible server](https://learn.microsoft.com/pt-br/azure/postgresql/azure-ai/generative-ai-agents)
- [Postgres MCP Server (crystaldba/postgres-mcp)](https://github.com/crystaldba/postgres-mcp)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Ignite 2025 LAB515 — Agentes de IA avançados com PostgreSQL](https://github.com/microsoft/ignite25-LAB515-build-advanced-ai-agents-with-postgresql)
- [schneidenbach/using-agent-framework-with-postgres](https://github.com/schneidenbach/using-agent-framework-with-postgres)

## Licença

MIT.
