# Agente de IA + Postgres — Agent Framework *e* Foundry Agent Service

**Um exemplo pequeno e reprodutível: um agente de IA local que lê *e escreve* num banco Azure PostgreSQL através do [Postgres MCP Server](https://github.com/crystaldba/postgres-mcp) — construído duas vezes, uma com o [Microsoft Agent Framework](https://learn.microsoft.com/pt-br/agent-framework/overview/?pivots=programming-language-python) e outra com o [Foundry Agent Service](https://learn.microsoft.com/azure/ai-foundry/agents/overview), para você comparar lado a lado.**

*[Read in English](README.md)*

Ninguém escreve SQL para o agente. Você descreve um resultado em linguagem natural, o modelo escreve o SQL, e um servidor MCP executa contra o banco.

```mermaid
flowchart LR
    User["👤 Você<br/><small>CLI</small>"]

    subgraph impl[" Escolha uma implementação "]
        direction TB
        MAF["🤖 <b>src/maf</b><br/><small>Agent Framework<br/>agente montado em memória</small>"]
        FDY["☁️ <b>src/foundry</b><br/><small>Agent Service<br/>agente salvo num projeto</small>"]
    end

    MCP["🔌 postgres-mcp<br/><small>iniciado pelo uvx</small>"]
    DB[("🐘 Azure<br/>PostgreSQL")]
    Model["🧠 gpt-4.1"]

    User <--> impl
    MAF <-->|"stdio<br/>JSON-RPC"| MCP
    FDY <-->|"stdio<br/>JSON-RPC"| MCP
    MCP <-->|"TCP 5432<br/>+ TLS"| DB
    impl <-->|HTTPS| Model
```

O banco de exemplo é de uma operadora de telecom fictícia monitorando seu backbone de fibra óptica: sites, enlaces, alertas e anotações de técnicos.

---

## As duas implementações

Mesmo banco, mesmo servidor MCP, mesmo prompt de sistema, mesma experiência de CLI. **Só o runtime muda.** É esse o ponto do repositório: tudo o que difere entre as duas é uma diferença real entre os SDKs, não uma diferença de cenário.

```
src/
├── common/     compartilhado: .env, o prompt de sistema, o trace de SQL
├── maf/        implementação 1 — Microsoft Agent Framework
└── foundry/    implementação 2 — Foundry Agent Service
```

| | `src/maf/` | `src/foundry/` |
|---|---|---|
| **SDK** | `agent-framework-core` + `agent-framework-openai` | `azure-ai-projects` |
| **Onde o agente vive** | Em memória, enquanto seu processo roda | Salvo num projeto Foundry |
| **Visível no portal** | Não — é um objeto Python | **Sim**, em Agents, com instruções e ferramentas |
| **Versionamento** | Nenhum. Edite o código e reinicie | Cada sync cria uma nova versão imutável |
| **Etapa de implantação** | Nenhuma | `python -m src.foundry.sync` |
| **Quem executa o laço de ferramentas** | O framework, de forma invisível | O Agent Service, remotamente |
| **Como o Postgres é ligado** | `MCPStdioTool` — um processo filho gerenciado pelo framework | Uma tool `mcp` apontando para um servidor MCP hospedado |
| **Onde o servidor MCP roda** | Na sua máquina, iniciado pelo `uvx` | No Azure Container Apps |
| **Funciona sem o seu código rodando** | Não | **Sim** — playground do portal, routines, outros agentes |
| **Memória de conversa** | `AgentSession` | `previous_response_id` |
| **Streaming de tokens** | Sim, nativo | Não implementado aqui |
| **Configuração do modelo** | `AZURE_OPENAI_*` (`openai.azure.com`) | `FOUNDRY_*` (`services.ai.azure.com/api/projects/...`) |
| **Credenciais do banco** | Na sua máquina, no `.env` | Só no contêiner — este processo nunca as vê |
| **Permissão extra no Azure** | Cognitive Services OpenAI User | Azure AI User no projeto |
| **Infraestrutura extra** | Nenhuma | Container Registry + Container Apps |
| **Como rodar** | `python -m src.maf.main` | `pwsh infra/deploy-mcp.ps1`, `python -m src.foundry.sync` e `python -m src.foundry.main` |

### Quando usar cada uma

**Use o Agent Framework quando o agente faz parte de uma aplicação que você está escrevendo.** É menos código, ele cuida de streaming e sessões, e o agente é só um objeto — sem etapa de implantação, sem nada para manter sincronizado, sem permissão extra no Azure. É o padrão certo para embutir um agente dentro de um serviço, de um job ou de uma CLI.

**Use o Agent Service quando o agente em si é o artefato.** Quando ele precisa ser encontrável por outras pessoas, versionado e governado, avaliado com o ferramental de avaliação do Foundry, editado no portal por alguém que não tem o seu repositório, ou invocado por algo que não é o seu código — routines agendadas, Teams, outro agente.

**Não são excludentes.** Um destino razoável é usar as duas: a definição e a governança no projeto Foundry, a orquestração na sua aplicação.

> ⚠️ **Uma restrição que molda todo o resto.** Um prompt agent do Foundry roda *dentro do serviço*, e o Agent Service só aceita servidores MCP que sejam **endpoints HTTPS remotos**. O `postgres-mcp` é um processo local em stdio — nada no Azure consegue subi-lo. Então a implementação 2 primeiro o publica como contêiner (`infra/deploy-mcp.ps1`) e o agente aponta para ele. É por isso que ela precisa de uma infraestrutura que a implementação 1 dispensa, e também por isso que ela funciona sem nenhum cliente rodando. Veja **[docs/implementations.pt-BR.md](docs/implementations.pt-BR.md)**.

---

## Duas formas de começar

**A. Percurso completo** — provisione um Azure PostgreSQL descartável, carregue os dados de telecom de exemplo e explore. Comece pelo [Início rápido](#início-rápido).

**B. Traga o seu** — você já tem um banco PostgreSQL e um deployment de modelo, e só quer apontar o agente para eles. **Sem nenhuma alteração de código**, apenas o `.env`. Funciona com qualquer uma das implementações. Comece por **[Usando seu próprio banco e modelo](docs/bring-your-own.pt-BR.md)**.

---

## Sumário

- [As duas implementações](#as-duas-implementações)
- [O que você vai aprender](#o-que-você-vai-aprender)
- [Pré-requisitos](#pré-requisitos)
- [Início rápido](#início-rápido)
- [Implementação 2 — o Foundry Agent Service](#implementação-2--o-foundry-agent-service)
- [A planilha de contratos](#a-planilha-de-contratos)
- [A interface web](#a-interface-web)
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
7. Construir o *mesmo* agente no Foundry Agent Service, vê-lo versionado no portal e entender exatamente o que o framework estava fazendo por você.

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
| **Um projeto Foundry** | *Somente para a implementação 2.* Mais a role **Azure AI User** nele, que é o que concede escrita em agents. |
| **Container Registry + Container Apps** | *Somente para a implementação 2.* O `infra/deploy-mcp.ps1` cria os dois; você só precisa ter permissão. A imagem é construída no Azure, então Docker local não é necessário. |

Você **não** precisa de `psql`, Docker, nem de conhecimento prévio de MCP. A implementação 1 não precisa de projeto Foundry, e a implementação 2 não precisa das variáveis `AZURE_OPENAI_*` — você pode rodar uma sem configurar a outra.

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

Esta é a **implementação 1**, a do Agent Framework:

```powershell
python -m src.maf.main
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

## Implementação 2 — o Foundry Agent Service

Tudo acima monta o agente **em memória**: ele existe enquanto o `python` está rodando e desaparece quando o processo termina.

A segunda implementação registra exatamente o mesmo agente num projeto Foundry. Acrescente três linhas ao `.env`:

```ini
FOUNDRY_PROJECT_ENDPOINT=https://<recurso>.services.ai.azure.com/api/projects/<projeto>
FOUNDRY_AGENT_NAME=fiberops-agent
FOUNDRY_MODEL_DEPLOYMENT=gpt-4.1
```

### 1. Publique o servidor MCP

O Agent Service chama o servidor MCP sozinho, então ele precisa estar em algum lugar que o Azure alcance. Este script constrói a imagem (no Azure — sem Docker local), implanta no Container Apps, libera o firewall do banco para ele e guarda a credencial do endpoint numa project connection do Foundry:

```powershell
pwsh infra/deploy-mcp.ps1 -AccessMode restricted
```

```
==> Building the image (this runs in Azure, not on your machine)
    Built mafmcp....azurecr.io/postgres-mcp:20260916181350
==> Deploying the container app 'postgres-mcp'
    Endpoint: https://postgres-mcp....azurecontainerapps.io/mcp
==> Creating the Foundry project connection
    Connection 'postgres-mcp' holds the secret; the agent references it by name.
```

Ele escreve `MCP_SERVER_URL` e `MCP_CONNECTION_NAME` no `.env` para você.

### 2. Registre o agente

```powershell
python -m src.foundry.sync
```

```
Done. 'fiberops-agent' is now at version 1.
It has one tool, 'postgres', pointing at the MCP server above.
```

O agente passa a aparecer em **Agents** no portal do Foundry com exatamente **uma ferramenta**:

```json
{
  "type": "mcp",
  "server_label": "postgres",
  "server_url": "https://postgres-mcp....azurecontainerapps.io/mcp",
  "require_approval": "never",
  "project_connection_id": "postgres-mcp"
}
```

Rode o sync de novo e você tem a versão 2 — a definição fica no controle de versão, e cada implantação dela fica registrada no projeto.

### 3. Converse com ele

```powershell
python -m src.foundry.main
```

A experiência de chat é propositalmente idêntica à do `python -m src.maf.main`, incluindo o trace de SQL. **Mas agora essa CLI é opcional** — abra o agente no playground do portal do Foundry e ele responde por lá também, porque nada roda na sua máquina.

Você precisa da role **Azure AI User** no projeto. Ler agentes e escrevê-los são permissões diferentes, então listar agentes pode funcionar enquanto o sync falha com `403 ... agents/write`.

> 💰 O contêiner roda com `min-replicas 1`, então ele custa dinheiro enquanto existir. Quando terminar: `az containerapp delete -g rg-maf-postgres-demo -n postgres-mcp --yes`

**→ Por que foi construído assim, as quatro coisas que só apareceram em tempo de execução e como endurecer isso: [docs/implementations.pt-BR.md](docs/implementations.pt-BR.md)**

---

## A planilha de contratos

Um banco de dados raramente é a única fonte de verdade. Os dois agentes também
recebem um **code interpreter** com uma planilha anexada,
[data/fiberops-contracts.xlsx](data/fiberops-contracts.xlsx):

| Aba | Contém | Liga com |
|---|---|---|
| `SLA` | Cliente, tier de serviço, prazo de resolução, multa por hora | `fiber_links.code` |
| `Maintenance` | Janelas planejadas, tipo de trabalho, equipe de campo | `fiber_links.code` |
| `OnCall` | Engenheiro de plantão, telefone, gestor de escalação | `sites.name` |

Nada disso existe no Postgres — e é esse o ponto. Pergunte:

> Qual alerta aberto tem a maior exposição financeira?

e o agente precisa usar **as duas ferramentas no mesmo turno**: SQL pelo servidor
MCP para achar os alertas abertos, depois pandas sobre a planilha para cruzá-los
com a aba SLA e multiplicar as horas em atraso pela multa contratual. Nenhuma
das duas responde isso sozinha.

O código roda num contêiner isolado no provedor, nunca na sua máquina. É por isso
que este repo não tem pandas como dependência, mesmo com o agente usando pandas
o tempo todo.

A planilha está commitada, então não há nada a rodar. Para mudar os dados de
exemplo, edite as tabelas em [infra/make_workbook.py](infra/make_workbook.py) e
regenere:

```powershell
pip install openpyxl
python -m infra.make_workbook
python -m src.foundry.sync     # a implementação 2 fixa um file id, então re-sincronize
```

As duas implementações sobem o arquivo para **lugares diferentes** — Azure OpenAI
na implementação 1, o projeto Foundry na implementação 2 — então a mesma planilha
tem um id diferente em cada uma. Nenhuma das duas reenvia se já existir um
arquivo com o mesmo nome.

---

## A interface web

As CLIs respondem *o que o agente disse?*. Esta responde *o que o agente fez?*

```powershell
pip install -e ".[ui]"       # ou: pip install -r requirements.txt
python -m src.ui.server      # depois abra http://127.0.0.1:8100
```

Uma janela de chat, um seletor entre as duas implementações e um painel de trace
inspirado no playground do portal do Foundry:

| Painel | Mostra |
|---|---|
| **Tokens** | Entrada, saída e total do turno, mais quantos vieram do cache |
| **Tool calls** | Cada chamada que o modelo fez, com o SQL realcado e o resultado recebido |
| **Metadata** | Modelo, id da resposta, motivo de parada, transporte MCP, modo de acesso, latência |
| **Turns** | O histórico da conversa — clique em qualquer turno para reinspecioná-lo |

O seletor no canto superior direito é o ponto de tudo isso: faça a mesma
pergunta às duas implementações e veja o mesmo SQL chegar por dois caminhos
completamente diferentes. Cada uma guarda a própria conversa, então alternar
entre elas não perde nada.

É uma camada fina sobre as mesmas funções que as CLIs chamam, ou seja, não há
uma segunda implementação de agente escondida aí. Se só uma implementação
estiver configurada no `.env`, a outra aparece desabilitada com o motivo.

---

## Usando seu próprio banco

Tudo acima assume que você rodou o script de provisionamento. Se você já tem um banco PostgreSQL e um deployment de modelo, pode pular tudo isso — **sem nenhuma alteração de código**, e funciona com qualquer uma das implementações.

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
python -m src.maf.main
```

**O passo que realmente importa é o `AGENT_INSTRUCTIONS_FILE`.** Por padrão o agente usa um prompt que descreve o schema de exemplo do FiberOps; aponte-o para o seu banco sem mudar isso e ele vai procurar tabelas que não existem. Você tem duas opções:

| | |
|---|---|
| `prompts/auto-discover-schema.md` | O agente inspeciona seu schema em tempo de execução. Esforço zero, funciona em qualquer lugar, custa algumas chamadas extras ao modelo. |
| `prompts/template.md` | Copie, descreva suas tabelas e aponte o `AGENT_INSTRUCTIONS_FILE` para a sua cópia. Melhores resultados — e o próprio agente pode escrever o primeiro rascunho. |

A CLI mostra qual prompt está ativo na inicialização, então você nunca fica no escuro.

Os scripts em `src/maf/examples/` foram escritos para os dados de exemplo e se recusam a rodar no seu banco, em vez de fazer algo sem sentido ou destrutivo.

**→ Guia completo, incluindo segurança, permissões e um setup só com Docker: [docs/bring-your-own.pt-BR.md](docs/bring-your-own.pt-BR.md)**

---

## O que cada arquivo faz

```
postgres-maf/
├── src/
│   ├── common/          COMPARTILHADO PELAS DUAS IMPLEMENTAÇÕES
│   │   ├── config.py       Carrega e valida a parte comum do .env.
│   │   ├── prompts.py      O prompt de sistema que descreve o schema.
│   │   ├── workbook.py     Sobe a planilha de contratos para o code interpreter.
│   │   ├── preflight.py    Falha rápido quando o banco está inacessível.
│   │   └── trace.py        Imprime o SQL que o modelo escreveu.
│   ├── maf/             IMPLEMENTAÇÃO 1 - MICROSOFT AGENT FRAMEWORK
│   │   ├── agent.py        >>> O ARQUIVO QUE VALE A PENA LER <<<
│   │   │                   Monta o chat client, a ferramenta MCP e o Agent.
│   │   ├── config.py       Somente AZURE_OPENAI_*.
│   │   ├── mcp_server.py   Sobe o postgres-mcp com uvx.
│   │   ├── workbook.py     Sobe a planilha para o Azure OpenAI.
│   │   ├── main.py         CLI interativa: streaming, sessões, slash commands.
│   │   └── examples/
│   │       ├── read_only.py       Exemplo 1 - apenas consultas
│   │       ├── read_write.py      Exemplo 2 - lê, escreve, comprova a persistência
│   │       └── human_approval.py  Exemplo 3 - aprove cada comando
│   └── foundry/         IMPLEMENTAÇÃO 2 - FOUNDRY AGENT SERVICE
│       ├── agent.py        >>> O ARQUIVO QUE VALE A PENA LER <<<
│       │                   A definição do prompt agent: modelo, prompt, uma tool MCP.
│       ├── config.py       FOUNDRY_* e o endpoint MCP. Sem credenciais de banco.
│       ├── sync.py         Registra o agente no seu projeto Foundry.
│       └── main.py         CLI interativa. Uma requisição por turno.
├── src/ui/                     INTERFACE WEB LOCAL - opcional, fala com as duas
│   ├── server.py              Camada fina de FastAPI sobre os dois runners
│   └── static/                Um HTML, um CSS e um JS. Sem etapa de build.
├── infra/                      SÓ é necessário para criar o ambiente de demo
│   ├── provision.ps1 / .sh    Cria os recursos no Azure + escreve o .env
│   ├── deploy-mcp.ps1         Publica o servidor MCP (só implementação 2)
│   ├── mcp-server/            Dockerfile + camada de autenticação do contêiner
│   ├── make_workbook.py       Regenera data/fiberops-contracts.xlsx
│   ├── seed.sql               Schema de telecom e dados de exemplo
│   ├── seed.py                Aplica o seed.sql (sem precisar de psql)
│   └── teardown.ps1 / .sh     Para ou apaga tudo
├── prompts/                    System prompts - troque pelo seu schema
│   ├── auto-discover-schema.md  Agente inspeciona qualquer banco em runtime
│   └── template.md              Copie e descreva suas próprias tabelas
├── docs/
│   ├── implementations.pt-BR.md   As duas implementações, comparadas a fundo
│   ├── bring-your-own.pt-BR.md    Usando seu próprio banco e modelo
│   ├── architecture.pt-BR.md      Como as peças se encaixam
│   └── troubleshooting.pt-BR.md   Todos os erros que enfrentamos, e a solução
├── .env.example               Todas as configurações, documentadas
└── requirements.txt / pyproject.toml
```

**Nada em `src/common/` sabe qual implementação está rodando**, e nenhuma das duas importa da outra. É isso que torna a comparação honesta: se as duas se comportam de forma diferente, é o SDK, não o setup.

Comece por [`src/maf/agent.py`](src/maf/agent.py) ou [`src/foundry/agent.py`](src/foundry/agent.py). Cada um tem cerca de 80 linhas de código de verdade; o resto é explicação.

---

## Os três exemplos

Eles pertencem à implementação 1. Rode-os nesta ordem.

### Exemplo 1 — leitura

```powershell
python -m src.maf.examples.read_only
```

Quatro perguntas independentes, de uma contagem simples até o tempo médio de resolução por severidade. Nenhum `session=` é passado, então cada pergunta é isolada.

### Exemplo 2 — escrita

```powershell
python -m src.maf.examples.read_write
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
python -m src.maf.examples.human_approval
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

Funciona com qualquer uma das implementações — `python -m src.maf.main` ou `python -m src.foundry.main`:

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

### Implementação 1 — Agent Framework

Três objetos, montados em [`src/maf/agent.py`](src/maf/agent.py).

#### 1. O chat client — o cérebro

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

#### 2. A ferramenta — as mãos

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

#### 3. O agente — juntando tudo

```python
from agent_framework import Agent

agent = Agent(client=client, instructions=PROMPT_DO_SCHEMA, tools=pg)

async with agent:                      # <-- inicia o processo filho do MCP
    session = agent.create_session()   # <-- memória da conversa
    result = await agent.run("Quantos alertas estão abertos?", session=session)
    print(result.text)
```

**O `async with` não é opcional.** É ele que inicia e encerra o servidor MCP. Se você esquecer, o modelo vai dizer que não tem como acessar o banco — o erro mais comum com ferramentas MCP.

O system prompt vem de [`src/common/prompts.py`](src/common/prompts.py) e descreve todo o schema. O servidor MCP *poderia* descobrir isso sozinho, mas declarar de antemão significa menos idas e vindas, muito menos nomes de coluna inventados, e um lugar natural para regras que o schema não consegue expressar (*"ao resolver um alerta você também precisa preencher `resolved_at`"*).

#### Mudanças de API desde os betas

Se você está portando código de um exemplo antigo:

| API beta (maioria dos exemplos online) | 1.x GA (este repositório) |
|---|---|
| `from agent_framework.azure import AzureOpenAIChatClient` | `from agent_framework.openai import OpenAIChatClient` |
| `AzureOpenAIChatClient(endpoint=..., deployment_name=...)` | `OpenAIChatClient(azure_endpoint=..., model=...)` |
| `client.create_agent(...)` | `Agent(client=..., ...)` |
| `agent.run_stream(...)` | `agent.run(..., stream=True)` |
| `AgentThread`, `agent.get_new_thread()` | `AgentSession`, `agent.create_session()` |
| `MCPSSETool` | removido — use `MCPStdioTool` ou `MCPStreamableHTTPTool` |

### Implementação 2 — Foundry Agent Service

Três passos, em [`src/foundry/`](src/foundry/) e [`infra/mcp-server/`](infra/mcp-server/).

#### 1. Publique o servidor MCP

O `postgres-mcp` fala stdio por padrão, e o Agent Service não consegue subir um processo na sua máquina. Então ele roda como contêiner, servindo as mesmas ferramentas sobre Streamable HTTP:

```python
app = postgres_mcp.mcp.streamable_http_app()
app.add_middleware(BearerTokenMiddleware, token=token)   # ele não tem auth própria
```

Duas coisas aqui merecem atenção antes de você copiar isso. Primeiro, esse transporte só existia no `main` quando isto foi escrito, então a imagem fixa um commit em vez de uma release. Segundo, o `postgres-mcp` **não tem nenhuma autenticação** — o middleware de bearer acima é nosso, e é a única razão de o endpoint não ser um proxy de SQL aberto.

As credenciais do banco vivem nesse contêiner. Nada na sua máquina precisa delas.

#### 2. Descreva o agente e guarde-o no projeto

```python
definition = PromptAgentDefinition(
    model="gpt-4.1",
    instructions=PROMPT_DO_SCHEMA,
    tools=[MCPTool(
        server_label="postgres",
        server_url=cfg.mcp_server_url,
        project_connection_id=cfg.mcp_connection_name,   # não headers - veja abaixo
        require_approval="never",
    )],
)
client.agents.create_version(agent_name="fiberops-agent", definition=definition)
```

**Uma ferramenta, não nove.** A definição do agente diz *"existe um servidor MCP de Postgres ali"*; o serviço conecta, lista as ferramentas e as chama. As nove ferramentas nunca aparecem na definição, que é como uma integração MCP deveria se parecer.

Passar o token bearer como header inline é rejeitado de cara:

> *Headers that can include sensitive information are not allowed in the headers property for MCP tools. Use project_connection_id instead.*

Então o segredo vai para uma **project connection** e o agente a referencia pelo nome. O `deploy-mcp.ps1` a cria para você.

Diferente da implementação 1, esse objeto agora *persiste*. Ele está no projeto, versionado, e qualquer pessoa com acesso consegue vê-lo.

#### 3. Envie uma requisição

```python
AGENT = {"agent_reference": {"name": agent_name, "type": "agent_reference"}}

# O serviço rejeita null explícito, então o primeiro turno precisa omitir isto.
carry = {"previous_response_id": previous_response_id} if previous_response_id else {}

response = openai_client.responses.create(
    model=model, input=user_input, extra_body=AGENT, **carry,
)
```

É o turno inteiro. Não há laço de ferramentas aqui — o serviço o executa, e o SQL que a nossa CLI imprime é lido de volta do `response.output` depois, apenas para você ver o que aconteceu.

O `extra_body` é o que seleciona o agente armazenado — sem ele você está falando com um deployment de modelo puro, sem instruções e sem ferramentas. A chave é `agent_reference`; a antiga `agent` é rejeitada como descontinuada.

Como nada disso é necessário para o agente funcionar, o mesmo agente responde no **playground do portal**, a partir de uma routine agendada ou de outro agente. Essa é a diferença real entre as duas implementações.

---

## Segurança

Este é um exemplo didático. Ele é deliberadamente permissivo, e você não deve copiar seus padrões para nada real.

**`--access-mode=unrestricted` significa que o agente pode dar `DROP TABLE`.** Combinado com `approval_mode="never_require"`, um prompt infeliz pode destruir dados sem ninguém no circuito. Esse é um risco aceitável para um banco de demonstração descartável, e para mais nada.

Antes de apontar um agente para dados que você preza, aplique estas medidas em ordem de eficácia:

1. **Uma role restrita no banco.** `GRANT SELECT, INSERT ON ...` e nada mais. É a única barreira que um LLM não consegue contornar com conversa.
2. **`POSTGRES_MCP_ACCESS_MODE=restricted`.** O servidor MCP passa a permitir apenas transações somente-leitura.
3. **`POSTGRES_MCP_APPROVAL_MODE=always_require`.** Um humano aprova cada comando — veja o Exemplo 3.
4. **`allowed_tools=[...]`** no `MCPStdioTool`, escondendo tudo exceto as chamadas que você quer.

Na implementação 2 os três primeiros valem sem mudança — são propriedades do banco e do servidor MCP, não do runtime. Para restringir a lista de ferramentas lá, sincronize menos delas: filtre o retorno de `discover_tools()` antes de montar a definição.

Além disso:

- O `.env` guarda a senha do banco e está no `.gitignore`. Mantenha assim.
- Prefira Entra ID (`az login`) em vez de `AZURE_OPENAI_API_KEY`; o exemplo já usa Entra ID por padrão.
- O script de provisionamento libera o firewall para uma faixa /24 por conveniência. Restrinja a um único IP para qualquer coisa duradoura.

---

## Custo e limpeza

Um servidor Burstable `Standard_B1ms` com 32 GB custa aproximadamente **USD 15–20 por mês** se ficar ligado. O consumo de Azure OpenAI neste exemplo é de alguns centavos.

Se você rodou a implementação 2, o contêiner do MCP fica com `min-replicas 1` para o Agent Service nunca pegar cold start — o que significa cobrança contínua. Apague quando terminar:

```powershell
az containerapp delete -g rg-maf-postgres-demo -n postgres-mcp --yes
```

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
| `403 ... agents/write` no sync | Implementação 2. Você precisa da role **Azure AI User** no projeto Foundry. |
| `421 Misdirected Request` vindo do endpoint MCP | A proteção contra DNS rebinding do SDK do MCP rejeitando o header `Host`. Defina `ALLOWED_HOST` no container app com o FQDN dele mesmo. |
| `Headers that can include sensitive information are not allowed` | Você colocou o token bearer em `headers` da tool. Use `project_connection_id`. |
| `connection timeout expired` *vindo do contêiner*, não de você | O firewall do banco não libera a saída do Container Apps. Rode `infra/deploy-mcp.ps1` de novo. |
| Muitos `INFO Processing request of type ...` | Normal. É o servidor MCP escrevendo log no stderr. |

---

## Próximos passos

- **Adicione suas próprias ferramentas.** Na implementação 1, o parâmetro `tools=` aceita funções Python comuns junto com a ferramenta MCP. Na implementação 2, acrescente outra entrada em `tools=` na definição — um segundo `MCPTool`, ou um `FunctionTool` que você trata no cliente.
- **Adicione um segundo servidor MCP.** Passe uma lista em `tools=`. O [Microsoft Learn MCP server](https://learn.microsoft.com/api/mcp) funciona bem com `MCPStreamableHTTPTool` — ou, na implementação 2, como um `MCPTool` nativo, já que ele é um endpoint HTTPS remoto.
- **Troque o token bearer por Entra ID.** A autenticação do contêiner é um segredo compartilhado, que é o ponto mais fraco da implementação 2. Coloque autenticação do Container Apps ou API Management na frente e dê uma managed identity ao projeto — veja [docs/implementations.pt-BR.md](docs/implementations.pt-BR.md).
- **Avalie o agente.** Um agente armazenado no Foundry pode passar pelo ferramental de avaliação do Foundry, que é uma das melhores razões para escolher a implementação 2.
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
