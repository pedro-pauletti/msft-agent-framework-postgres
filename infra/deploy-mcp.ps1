<#
.SYNOPSIS
    Publishes the Postgres MCP server so the Foundry Agent Service can reach it.

.DESCRIPTION
    A Foundry prompt agent runs inside the service, so an MCP tool must be a
    remote HTTPS endpoint. This script creates that endpoint:

      * an Azure Container Registry
      * the image, built from infra/mcp-server (in ACR - no local Docker)
      * a Container Apps environment and a Container App with public ingress
      * a PostgreSQL firewall rule for the Container App's outbound IPs
      * a shared secret, written to ../.env along with the endpoint URL

    Run this once before `python -m src.foundry.sync`. It is idempotent: run it
    again and it rebuilds the image and updates the app in place.

    Only the Foundry path needs this. The Agent Framework path in src/maf/ runs
    the MCP server locally and needs none of it.

.EXAMPLE
    pwsh infra/deploy-mcp.ps1

.EXAMPLE
    pwsh infra/deploy-mcp.ps1 -AccessMode unrestricted
#>
[CmdletBinding()]
param(
    [string] $ResourceGroup = 'rg-maf-postgres-demo',

    # Container Apps is not available in every region PostgreSQL is. This
    # defaults to the region the rest of the sample uses when it can.
    [string] $Location = 'brazilsouth',

    # Must be globally unique, alphanumeric only.
    [string] $RegistryName = "mafmcp$(-join ((48..57) + (97..122) | Get-Random -Count 8 | ForEach-Object { [char]$_ }))",

    [string] $EnvironmentName = 'maf-mcp-env',
    [string] $AppName = 'postgres-mcp',

    # The Foundry project connection that holds the endpoint's shared secret.
    # The agent references it by name, so the secret never lands in the agent
    # definition - which the Agent Service enforces rather than suggests.
    [string] $ConnectionName = 'postgres-mcp',

    # restricted   -> the agent can only read. The right default for an endpoint
    #                 that is reachable from the internet.
    # unrestricted -> INSERT / UPDATE / DELETE / DDL allowed.
    [ValidateSet('restricted', 'unrestricted')]
    [string] $AccessMode = 'restricted',

    # PostgreSQL server to point the MCP server at. Defaults to whatever is in .env.
    [string] $PostgresServerName = '',
    [string] $PostgresResourceGroup = 'rg-maf-postgres-demo',

    # Defaults to the active `az` subscription. Worth setting explicitly if you
    # have several - the CLI's active subscription changes more easily than you
    # expect, and every step below silently targets the wrong place if it does.
    [string] $Subscription = ''
)

$ErrorActionPreference = 'Stop'

function Write-Step($message) { Write-Host "`n==> $message" -ForegroundColor Cyan }
function Write-Note($message) { Write-Host "    $message" -ForegroundColor DarkGray }
function Write-Warn($message) { Write-Host "    $message" -ForegroundColor Yellow }

# `az` is a native command, so a failure sets $LASTEXITCODE but does not raise -
# $ErrorActionPreference does nothing for it. Without this the script cheerfully
# carries on building on top of a resource that was never created.
#
# Deliberately a *simple* function: an advanced one would try to bind `-o` to
# -OutVariable and fail with "the parameter name 'o' is ambiguous".
function Invoke-Az {
    $output = & az @args 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "az $($args -join ' ') failed:`n$output"
    }
    return $output
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot '.env'

# -----------------------------------------------------------------------------
# 0. Read .env - the MCP server needs the same database the agent uses
# -----------------------------------------------------------------------------
Write-Step 'Reading .env'

if (-not (Test-Path $envPath)) {
    throw "No .env found at $envPath. Run infra/provision.ps1 first."
}

$envValues = @{}
Get-Content $envPath | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        $envValues[$Matches[1]] = $Matches[2].Trim()
    }
}

foreach ($key in 'PGHOST', 'PGDATABASE', 'PGUSER', 'PGPASSWORD') {
    if (-not $envValues.ContainsKey($key) -or -not $envValues[$key]) {
        throw "$key is missing from .env. Run infra/provision.ps1 first."
    }
}

Add-Type -AssemblyName System.Web
$pgUser = [System.Web.HttpUtility]::UrlEncode($envValues['PGUSER'])
$pgPass = [System.Web.HttpUtility]::UrlEncode($envValues['PGPASSWORD'])
$pgPort = if ($envValues['PGPORT']) { $envValues['PGPORT'] } else { '5432' }
$pgSsl = if ($envValues['PGSSLMODE']) { $envValues['PGSSLMODE'] } else { 'require' }
$databaseUri = "postgresql://${pgUser}:${pgPass}@$($envValues['PGHOST']):${pgPort}/$($envValues['PGDATABASE'])?sslmode=${pgSsl}"

if (-not $PostgresServerName) {
    $PostgresServerName = ($envValues['PGHOST'] -split '\.')[0]
}
Write-Note "Database : $($envValues['PGHOST'])/$($envValues['PGDATABASE'])"
Write-Note "Access   : $AccessMode"

# -----------------------------------------------------------------------------
# 1. Prerequisites
# -----------------------------------------------------------------------------
Write-Step 'Checking prerequisites'

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw 'Azure CLI not found. Install it from https://aka.ms/installazurecli'
}
az account show -o none 2>$null
if ($LASTEXITCODE -ne 0) { throw 'Not signed in. Run `az login` first.' }

if ($Subscription) {
    Invoke-Az account set --subscription $Subscription | Out-Null
}
$account = az account show --query "{name:name, user:user.name}" -o json | ConvertFrom-Json
Write-Note "Subscription: $($account.name)"
Write-Note "Signed in as: $($account.user)"

if (-not (az extension list --query "[?name=='containerapp'].name" -o tsv)) {
    Write-Note 'Installing the containerapp CLI extension...'
    Invoke-Az extension add --name containerapp -o none | Out-Null
}
Invoke-Az provider register --namespace Microsoft.App -o none | Out-Null
Invoke-Az provider register --namespace Microsoft.OperationalInsights -o none | Out-Null
Write-Note 'ok'

# -----------------------------------------------------------------------------
# 2. Resource group and registry
# -----------------------------------------------------------------------------
# A resource group's location is fixed at creation and says nothing about where
# the resources inside it live, so reuse an existing one rather than failing on
# a location mismatch.
$groupLocation = az group show -n $ResourceGroup --query location -o tsv 2>$null
if ($groupLocation) {
    Write-Step "Reusing resource group '$ResourceGroup' ($groupLocation)"
} else {
    Write-Step "Creating resource group '$ResourceGroup'"
    Invoke-Az group create -n $ResourceGroup -l $Location -o none | Out-Null
}
Write-Note 'ok'

$existingRegistry = az acr list -g $ResourceGroup --query "[0].name" -o tsv 2>$null
if ($existingRegistry) {
    $RegistryName = $existingRegistry
    Write-Step "Reusing container registry '$RegistryName'"
} else {
    Write-Step "Creating container registry '$RegistryName'"
    Invoke-Az acr create -g $ResourceGroup -n $RegistryName --sku Basic --admin-enabled true -o none | Out-Null
}
Write-Note 'ok'

# -----------------------------------------------------------------------------
# 3. Build the image - in ACR, so no local Docker is needed
# -----------------------------------------------------------------------------
Write-Step 'Building the image (this runs in Azure, not on your machine)'

$contextPath = Join-Path $PSScriptRoot 'mcp-server'
$imageTag = "postgres-mcp:$(Get-Date -Format 'yyyyMMddHHmmss')"

# `--no-logs` is not about noise. Streaming the build log crashes the Azure CLI
# on a Windows console using a legacy code page ("'charmap' codec can't encode
# characters"), losing a build that had already started. The build still runs
# and this still waits for it; only the live log is suppressed. Recover it with
#   az acr task logs -r <registry> --run-id <id>
Write-Note 'This takes a few minutes. The live log is suppressed on purpose - see the comment in this script.'
Invoke-Az acr build --registry $RegistryName --image $imageTag `
    --file (Join-Path $contextPath 'Dockerfile') $contextPath --no-logs -o none | Out-Null

$image = "$RegistryName.azurecr.io/$imageTag"
Write-Note "Built $image"

# -----------------------------------------------------------------------------
# 4. Container Apps environment and app
# -----------------------------------------------------------------------------
Write-Step "Creating the Container Apps environment '$EnvironmentName'"
if (-not (az containerapp env show -g $ResourceGroup -n $EnvironmentName --query name -o tsv 2>$null)) {
    Invoke-Az containerapp env create -g $ResourceGroup -n $EnvironmentName -l $Location -o none | Out-Null
}
Write-Note 'ok'

# A fresh secret on every run would break an already-synced agent, so reuse the
# one in .env when it is there.
$authToken = $envValues['MCP_AUTH_TOKEN']
if (-not $authToken) {
    $bytes = [byte[]]::new(32)
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $authToken = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    Write-Note 'Generated a new shared secret.'
} else {
    Write-Note 'Reusing the shared secret already in .env.'
}

$acrPassword = az acr credential show -n $RegistryName --query "passwords[0].value" -o tsv

Write-Step "Deploying the container app '$AppName'"
$appExists = az containerapp show -g $ResourceGroup -n $AppName --query name -o tsv 2>$null

if ($appExists) {
    Invoke-Az containerapp secret set -g $ResourceGroup -n $AppName `
        --secrets "database-uri=$databaseUri" "auth-token=$authToken" -o none | Out-Null
    Invoke-Az containerapp registry set -g $ResourceGroup -n $AppName `
        --server "$RegistryName.azurecr.io" --username $RegistryName --password $acrPassword -o none | Out-Null
    Invoke-Az containerapp update -g $ResourceGroup -n $AppName --image $image `
        --set-env-vars "DATABASE_URI=secretref:database-uri" "MCP_AUTH_TOKEN=secretref:auth-token" "ACCESS_MODE=$AccessMode" "PORT=8000" -o none | Out-Null
} else {
    Invoke-Az containerapp create -g $ResourceGroup -n $AppName --environment $EnvironmentName `
        --image $image `
        --registry-server "$RegistryName.azurecr.io" --registry-username $RegistryName --registry-password $acrPassword `
        --secrets "database-uri=$databaseUri" "auth-token=$authToken" `
        --env-vars "DATABASE_URI=secretref:database-uri" "MCP_AUTH_TOKEN=secretref:auth-token" "ACCESS_MODE=$AccessMode" "PORT=8000" `
        --ingress external --target-port 8000 --transport http `
        --min-replicas 1 --max-replicas 1 `
        --cpu 0.5 --memory 1.0Gi -o none | Out-Null
}

$fqdn = az containerapp show -g $ResourceGroup -n $AppName --query "properties.configuration.ingress.fqdn" -o tsv
if (-not $fqdn) {
    throw "The container app has no ingress FQDN. Check: az containerapp show -g $ResourceGroup -n $AppName"
}
$mcpUrl = "https://$fqdn/mcp"

# The MCP SDK rejects any Host header it does not know about (DNS rebinding
# protection), and the FQDN only exists once the app does - hence the second
# update rather than passing it at creation.
Invoke-Az containerapp update -g $ResourceGroup -n $AppName `
    --set-env-vars "ALLOWED_HOST=$fqdn" -o none | Out-Null

Write-Note "Endpoint: $mcpUrl"

# -----------------------------------------------------------------------------
# 5. Let the container reach PostgreSQL
# -----------------------------------------------------------------------------
Write-Step 'Opening the PostgreSQL firewall for the container'

# Container Apps on the consumption profile egresses from a large shared pool -
# `outboundIpAddresses` returns dozens of addresses and they are not stable, so
# one firewall rule per address is both unmanageable and wrong by tomorrow.
#
# 0.0.0.0-0.0.0.0 is PostgreSQL's special "allow Azure services" rule. It is
# broader than it looks: it admits any Azure resource, including ones that are
# not yours. Acceptable for a disposable demo database behind a strong password;
# for anything real, give the Container App a VNet with a NAT gateway so it has
# one predictable egress IP, and allow only that.
Invoke-Az postgres flexible-server firewall-rule create -g $PostgresResourceGroup -n $PostgresServerName `
    --rule-name AllowAzureServices --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0 -o none | Out-Null
Write-Note 'Allowed Azure services (0.0.0.0) - see the comment in this script about narrowing it.'

# -----------------------------------------------------------------------------
# 6. Store the secret as a project connection
# -----------------------------------------------------------------------------
# The Agent Service refuses to accept an Authorization header inline on an MCP
# tool - "Headers that can include sensitive information are not allowed ...
# Use project_connection_id instead". Which is the right call: it keeps the
# secret out of the agent definition, where anyone with read access could see it.
Write-Step 'Creating the Foundry project connection'

$foundryEndpoint = $envValues['FOUNDRY_PROJECT_ENDPOINT']
if (-not $foundryEndpoint) {
    Write-Warn 'FOUNDRY_PROJECT_ENDPOINT is not in .env - skipping. Set it and re-run before `python -m src.foundry.sync`.'
} else {
    # https://<account>.services.ai.azure.com/api/projects/<project>
    $accountName = ([Uri]$foundryEndpoint).Host.Split('.')[0]
    $projectName = ($foundryEndpoint.TrimEnd('/') -split '/api/projects/')[-1]

    $accountId = az cognitiveservices account list --query "[?name=='$accountName'].id" -o tsv
    if (-not $accountId) {
        throw "Could not find the Foundry account '$accountName' in this subscription. Check FOUNDRY_PROJECT_ENDPOINT and `az account show`."
    }

    $connectionBody = @{
        properties = @{
            category      = 'RemoteTool'
            target        = $mcpUrl
            authType      = 'CustomKeys'
            isSharedToAll = $true
            credentials   = @{ keys = @{ Authorization = "Bearer $authToken" } }
        }
    } | ConvertTo-Json -Depth 8

    $bodyFile = Join-Path ([System.IO.Path]::GetTempPath()) "mcp-connection-$([Guid]::NewGuid()).json"
    try {
        Set-Content -Path $bodyFile -Value $connectionBody -Encoding UTF8
        Invoke-Az rest --method put `
            --url "https://management.azure.com$accountId/projects/$projectName/connections/$ConnectionName`?api-version=2025-06-01" `
            --body "@$bodyFile" -o none | Out-Null
    } finally {
        Remove-Item $bodyFile -Force -ErrorAction SilentlyContinue
    }
    Write-Note "Connection '$ConnectionName' holds the secret; the agent references it by name."
}

# -----------------------------------------------------------------------------
# 7. Write the endpoint and secret into .env
# -----------------------------------------------------------------------------
Write-Step 'Updating .env'

$lines = @(Get-Content $envPath | Where-Object { $_ -notmatch '^\s*(MCP_SERVER_URL|MCP_AUTH_TOKEN|MCP_CONNECTION_NAME)\s*=' })
$lines += ''
$lines += '# Written by infra/deploy-mcp.ps1. Used by src/foundry/ to attach a real MCP tool.'
$lines += "MCP_SERVER_URL=$mcpUrl"
$lines += "MCP_CONNECTION_NAME=$ConnectionName"
$lines += '# Kept so re-running deploy-mcp.ps1 reuses the same secret. The agent never sees it.'
$lines += "MCP_AUTH_TOKEN=$authToken"
Set-Content -Path $envPath -Value $lines -Encoding UTF8
Write-Note 'ok'

Write-Host "`n=============================================================" -ForegroundColor Green
Write-Host ' MCP server published' -ForegroundColor Green
Write-Host '=============================================================' -ForegroundColor Green
Write-Host ''
Write-Host "  Endpoint   : $mcpUrl"
Write-Host "  Access mode: $AccessMode"
Write-Host "  Image      : $image"
Write-Host ''
Write-Host 'Next steps:'
Write-Host ''
Write-Host '  1. Register the agent with a real MCP tool:'
Write-Host '       python -m src.foundry.sync'
Write-Host ''
Write-Host '  2. Try it in the Foundry portal playground - it now works with no client.'
Write-Host ''
Write-Host 'When you are done, delete the endpoint so it stops costing money:'
Write-Host "       az containerapp delete -g $ResourceGroup -n $AppName --yes"
Write-Host ''
