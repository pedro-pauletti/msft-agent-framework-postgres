# Solução de problemas

*[Read in English](troubleshooting.md)*

Todos os erros abaixo são erros que realmente enfrentamos construindo este exemplo, junto com a solução.

---

## Instalação e dependências

### `ERROR: No matching distribution found for agent-framework-openai>=1.17`

`agent-framework-core` e `agent-framework-openai` têm **versionamento independente**. No momento em que escrevemos isto, o core está na 1.17 enquanto o conector OpenAI está na 1.14 — e a 1.14 já exige core >= 1.17. Os números descasados são esperados.

Use as faixas do `requirements.txt`:

```
agent-framework-core>=1.17,<2
agent-framework-openai>=1.14,<2
```

### `ModuleNotFoundError: The package agent-framework-openai is required to use OpenAIChatClient`

O `agent-framework-core` sozinho não inclui o conector do OpenAI, apesar do que parte da documentação dá a entender.

```powershell
pip install agent-framework-openai
```

### `uvx` não foi encontrado no PATH

O `uvx` vem junto com o [uv](https://docs.astral.sh/uv/):

```powershell
# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Reinicie o terminal depois, para o `PATH` ser recarregado.

---

## Servidor MCP

### `MCP server 'uvx postgres-mcp ...' failed to initialize: Connection closed`

O erro mais confuso desta pilha. O cliente MCP só informa que o processo filho sumiu; o erro de verdade foi impresso pelo filho antes de ele morrer.

**A causa mais comum: a versão do SDK do MCP.** O `postgres-mcp` 0.3.x foi feito para a v1 do SDK Python do MCP. A v2 renomeou `FastMCP` para `MCPServer`, então na v2 o servidor morre no import com:

```
ModuleNotFoundError: No module named 'mcp.server.fastmcp'
```

O `uvx` resolve a versão mais nova compatível por padrão, que hoje é a v2. A solução é o pin que este repositório já usa:

```python
args=["--with", "mcp<2", "postgres-mcp", "--access-mode=unrestricted"]
```

**Para ver o erro real você mesmo,** rode o servidor na mão:

```powershell
$env:DATABASE_URI = "postgresql://user:senha@host:5432/db?sslmode=require"
uvx --with "mcp<2" postgres-mcp --access-mode=unrestricted
```

Ele deve imprimir `Starting PostgreSQL MCP Server in UNRESTRICTED mode` e ficar esperando entrada. Qualquer outra coisa é o seu problema real. `Ctrl+C` para sair.

**Em rede corporativa, desconfie de inspeção TLS.** Rodar na mão mostra o que o cliente MCP não consegue ver:

```
Caused by: client error (Connect)
Caused by: received fatal alert: HandshakeFailure
```

O `uvx` resolve o pacote no PyPI a cada execução, e um proxy interceptador quebra o handshake. Se o pacote já está no cache do `uv`, pule a rede adicionando isto ao `.env`:

```ini
UV_OFFLINE=1
```

No `.env`, não no shell, para sobreviver a terminais novos. Isso afeta só a implementação 1 — a implementação 2 nunca sobe processo, porque o servidor MCP é um contêiner construído no Azure.

**Outras causas:** `uvx` fora do PATH; `DATABASE_URI` malformada; sem acesso de rede ao PyPI na primeira execução.

### O agente diz que não tem como acessar o banco

Você esqueceu do context manager assíncrono. É ele que inicia o processo filho do MCP:

```python
# ERRADO - nenhuma ferramenta é carregada
agent = build_agent(config)
result = await agent.run("...")

# CERTO
async with build_agent(config) as agent:
    result = await agent.run("...")
```

### Muitos `INFO Processing request of type CallToolRequest` no terminal

Isso é normal. O `postgres-mcp` escreve log no stderr, e o stderr é herdado pelo seu processo. Até que é útil — você acompanha as chamadas de ferramenta ao vivo.

Para silenciar, redirecione o stderr:

```powershell
python -m src.maf.main 2>$null          # PowerShell
python -m src.maf.main 2>/dev/null      # bash
```

### O agente se recusa a modificar dados

Confira o `.env`:

```
POSTGRES_MCP_ACCESS_MODE=unrestricted
```

No modo `restricted` o servidor MCP só permite transações somente-leitura, e as escritas falham com um erro de permissão vindo do servidor, não do PostgreSQL.

---

## Azure OpenAI

### `400 BadRequest: API version not supported`

O `OpenAIChatClient` do Agent Framework usa a **Responses API** do Azure OpenAI. Valores antigos de `api-version` não a suportam. Confirmamos:

| `AZURE_OPENAI_API_VERSION` | Resultado |
|---|---|
| *(vazio)* | funciona |
| `preview` | funciona |
| `v1` | funciona |
| `2025-04-01-preview` | falha |
| `2024-10-21` | falha |

**Deixe `AZURE_OPENAI_API_VERSION` vazio** e deixe o SDK escolher.

### `401 Unauthorized` ou `PermissionDenied`

Com Entra ID você precisa da role **Cognitive Services OpenAI User** no recurso do Azure OpenAI — ser Owner da subscription não basta por si só.

```powershell
az role assignment create `
  --assignee "$(az ad signed-in-user show --query id -o tsv)" `
  --role "Cognitive Services OpenAI User" `
  --scope "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<recurso>"
```

Depois rode `az login` de novo para a nova role entrar num token novo. Atribuições de role podem levar alguns minutos para propagar.

### `DeploymentNotFound` / `The API deployment for this resource does not exist`

`AZURE_OPENAI_DEPLOYMENT` precisa ser o **nome do deployment**, não o nome do modelo. Liste os seus:

```powershell
az cognitiveservices account deployment list `
  -g <resource-group> -n <nome-do-recurso> `
  --query "[].{deployment:name, model:properties.model.name}" -o table
```

### Minhas chamadas vão para o OpenAI público em vez do Azure

O `OpenAIChatClient` resolve a configuração nesta ordem:

1. Entradas Azure explícitas (`credential=` ou `azure_endpoint=`)
2. `OPENAI_API_KEY`
3. Fallback pelas variáveis de ambiente Azure (`AZURE_OPENAI_ENDPOINT`, ...)

Se `OPENAI_API_KEY` estiver exportada no seu shell e você não passar uma entrada Azure explícita, você cai silenciosamente no OpenAI público. Este exemplo sempre passa `azure_endpoint=`, então está seguro — mas o `src/maf/config.py` imprime um aviso quando vê essa variável, e você deve ficar atento à mesma armadilha no seu próprio código.

### `AZURE_OPENAI_ENDPOINT looks like a Foundry project endpoint`

Um recurso do Foundry expõe vários endpoints. Você precisa do endpoint do Azure OpenAI:

- Correto: `https://<nome>.openai.azure.com/`
- Errado aqui: `https://<nome>.services.ai.azure.com/`

---

## PostgreSQL

### `connection timeout expired`

Quase sempre é o firewall, e quase sempre porque a regra tem o IP errado.

Serviços de consulta de IP como o `api.ipify.org` reportam o endereço do proxy pelo qual seu tráfego HTTPS passa. Em redes corporativas, VPNs e dev boxes na nuvem, esse frequentemente **não** é o endereço que o PostgreSQL vê numa conexão TCP crua. Nós batemos exatamente nisso ao construir o exemplo: o serviço de consulta reportou um endereço e o PostgreSQL via outro, dentro da mesma /24.

Rodar o script de provisionamento de novo resolve, porque ele pergunta diretamente ao PostgreSQL:

```powershell
pwsh infra/provision.ps1 -ServerName <seu-servidor>
```

Para conferir na mão:

```powershell
# 1. abra temporariamente
az postgres flexible-server firewall-rule create -g <rg> -n <servidor> `
  --rule-name Temp --start-ip-address 0.0.0.0 --end-ip-address 255.255.255.255

# 2. pergunte ao PostgreSQL qual endereço ele vê
python -c "import psycopg,os;from dotenv import load_dotenv;load_dotenv();c=psycopg.connect(f\"postgresql://{os.environ['PGUSER']}:{os.environ['PGPASSWORD']}@{os.environ['PGHOST']}:5432/{os.environ['PGDATABASE']}?sslmode=require\");cur=c.cursor();cur.execute('SELECT host(inet_client_addr())');print(cur.fetchone()[0])"

# 3. crie uma regra estreita para esse endereço e apague a Temp
```

Outras possibilidades: o servidor está parado (`az postgres flexible-server start -g <rg> -n <servidor>`), ou a porta 5432 de saída está bloqueada na sua rede. Verifique o segundo caso com:

```powershell
Test-NetConnection -ComputerName portquiz.net -Port 5432
```

Se isso falhar, o bloqueio é local, não do Azure.

### `ERROR: The location is restricted from performing this operation`

Sua subscription não pode criar PostgreSQL flexible servers naquela região. Isso é comum em subscriptions trial, sponsored e MCAP, e é independente de quota.

Os scripts de provisionamento detectam isso e sugerem regiões que funcionam. Para verificar manualmente:

```powershell
$sub = az account show --query id -o tsv
az rest --method get --url "https://management.azure.com/subscriptions/$sub/providers/Microsoft.DBforPostgreSQL/locations/eastus/capabilities?api-version=2024-08-01" --query "value[0].reason" -o tsv
```

Resultado vazio significa que a região está disponível. Então:

```powershell
pwsh infra/provision.ps1 -Location centralus
```

### `ERROR: incorrect usage: --database-name can only be used when --cluster-option is set to ElasticCluster`

Versões mais novas do Azure CLI removeram o `--database-name` do `flexible-server create`. Crie o banco num passo separado (que é o que o `infra/provision.ps1` faz):

```powershell
az postgres flexible-server db create -g <rg> -s <servidor> -d fiberops
```

### Erros de `sslmode`, ou conexões recusadas sem motivo aparente

O Azure Database for PostgreSQL exige TLS. Mantenha `PGSSLMODE=require` no `.env`. `disable` não funciona, e o erro resultante não ajuda em nada.

### Rodei o script de provisionamento de novo e agora a senha está errada

O Azure nunca devolve a senha de administrador. Numa nova execução o script a recupera do seu `.env` existente; se não houver `.env`, ele redefine a senha no servidor e escreve a nova.

Para definir explicitamente:

```powershell
pwsh infra/provision.ps1 -ServerName <servidor> -AdminPassword '<senha-conhecida>'
```

---

## Comportamento

### O agente fala de alertas de fibra, mas esse não é o meu banco

Você apontou `PGHOST`/`PGDATABASE` para o seu banco mas não mexeu no system prompt, então o agente continua trabalhando com a descrição embutida do schema FiberOps.

Defina `AGENT_INSTRUCTIONS_FILE` no `.env`:

```ini
AGENT_INSTRUCTIONS_FILE=prompts/auto-discover-schema.md
```

A CLI imprime qual prompt está ativo na inicialização. Veja [docs/bring-your-own.pt-BR.md](bring-your-own.pt-BR.md).

### `AGENT_INSTRUCTIONS_FILE points to a file that does not exist`

O caminho é resolvido em relação à raiz do repositório, não ao diretório atual. Então `prompts/meu-banco.md` está certo, `./meu-banco.md` normalmente não. Pontos de partida prontos ficam em `prompts/`.

### Um exemplo se recusa a rodar: "you are probably pointed at your own database"

Funcionando como esperado. O `read_write.py` e o `human_approval.py` escrevem (e apagam) nas tabelas de exemplo do FiberOps. Quando `AGENT_INSTRUCTIONS_FILE` está definido, eles assumem que você está nos seus próprios dados e param.

Use `python -m src.maf.main`, ou edite as constantes `STEPS` / `QUESTION` desses arquivos antes.

### A CLI interativa parece travada e não me deixa digitar

Duas causas diferentes, e a mensagem diz qual é:

**O banco está inacessível.** Antes desta verificação existir, a CLI subia o
servidor MCP, que tentava conectar por ~30 segundos enquanto inundava o terminal
com warnings — e o prompt `you >` nunca aparecia. Parecia exatamente um
travamento.

O `python -m src.maf.main` agora verifica a conexão antes e falha em poucos segundos
com uma explicação. Se você vir `Checking database connection... FAILED`, leia o
parágrafo abaixo — quase sempre é o firewall (veja
[`connection timeout expired`](#connection-timeout-expired)).

**O stdin não é um terminal.** Se você redirecionou a entrada, ou o console da
sua IDE não repassa o stdin, o `input()` recebe EOF na hora e a CLI imprime:

```
[stdin is not interactive, so there is nothing to read]
```

Rode a partir de um terminal de verdade, ou use os exemplos roteirizados
(`python -m src.maf.examples.read_only`), que não precisam de entrada.

### Demora muito na primeiríssima execução

Esperado, uma única vez. O `uvx` baixa e faz cache do servidor MCP do Postgres na
primeira vez, o que leva de 10 a 30 segundos dependendo da sua conexão. A CLI
imprime `Starting the Postgres MCP server (first run downloads it)...` para você
saber que está funcionando. As execuções seguintes sobem em cerca de um segundo.

### O agente escreve SQL ruim, ou inventa nomes de coluna

Olhe o `FIBEROPS_INSTRUCTIONS` em `src/common/prompts.py`. Se você mudou o `infra/seed.sql`, o prompt está desatualizado — os dois precisam ser mantidos em sincronia.

Confirme também que o seu deployment é de um modelo com suporte a tool calling. `gpt-4.1`, `gpt-4.1-mini` e `gpt-4o` funcionam bem.

### O agente dá uma resposta diferente a cada execução

Esperado. Nunca dizemos qual SQL escrever, apenas qual resultado queremos. O `src/maf/examples/read_write.py` foi escrito para tolerar isso: o passo 4 relê o que quer que o passo 1 tenha escolhido.

### Caracteres acentuados aparecem errados no Windows

As duas CLIs forçam UTF-8 no stdout e no stderr. Se você estiver rodando seu próprio script, faça o mesmo:

```python
sys.stdout.reconfigure(encoding="utf-8")
```

### Quero recomeçar com os dados de exemplo limpos

```powershell
python -m infra.seed
```

Ele apaga e recria todas as tabelas.

---

## Ainda travado?

Confirme cada camada separadamente:

```powershell
# 1. Login no Azure
az account show

# 2. Banco acessível e populado
python -m infra.seed

# 3. Servidor MCP sobe sozinho
$env:DATABASE_URI = "postgresql://user:senha@host:5432/db?sslmode=require"
uvx --with "mcp<2" postgres-mcp --access-mode=unrestricted   # Ctrl+C para sair

# 4. Tudo junto
python -m src.maf.examples.read_only
```

O passo 4 exercita todas as camadas de uma vez, e o traceback diz qual delas falhou.
