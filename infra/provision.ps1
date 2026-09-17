<#
.SYNOPSIS
    Provisions everything this sample needs on Azure and writes a ready-to-use .env.

.DESCRIPTION
    Creates:
      * a resource group
      * an Azure Database for PostgreSQL flexible server (Burstable B1ms - the
        cheapest tier) with password authentication
      * a database
      * a firewall rule for your machine's real egress IP

    It then writes ../.env with everything the agent needs.

    The script is idempotent: run it again and it reuses what already exists and
    just refreshes the firewall rule.

.EXAMPLE
    pwsh infra/provision.ps1

.EXAMPLE
    pwsh infra/provision.ps1 -Location centralus -ServerName my-fiberops-db
#>
[CmdletBinding()]
param(
    [string] $ResourceGroup = 'rg-maf-postgres-demo',

    # NOTE: PostgreSQL flexible server provisioning is restricted in several
    # regions for many subscriptions (you get "The location is restricted from
    # performing this operation"). The script checks this before trying, and
    # tells you which regions you can use.
    [string] $Location = 'brazilsouth',

    # Must be globally unique. Defaults to a random suffix.
    [string] $ServerName = "maf-fiberops-$(-join ((48..57) + (97..122) | Get-Random -Count 6 | ForEach-Object { [char]$_ }))",

    [string] $AdminUser = 'fiberadmin',

    # Leave empty to generate a strong random password.
    [string] $AdminPassword = '',

    [string] $DatabaseName = 'fiberops',

    # Azure OpenAI settings written into .env. Override to match your resource.
    [string] $AzureOpenAiEndpoint = '',
    [string] $AzureOpenAiDeployment = 'gpt-4.1'
)

$ErrorActionPreference = 'Stop'

function Write-Step($message) { Write-Host "`n==> $message" -ForegroundColor Cyan }
function Write-Note($message) { Write-Host "    $message" -ForegroundColor DarkGray }

$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath  = Join-Path $repoRoot '.env'

# -----------------------------------------------------------------------------
# 0. Prerequisites
# -----------------------------------------------------------------------------
Write-Step 'Checking prerequisites'

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw 'Azure CLI (az) not found. Install it from https://aka.ms/installazurecli'
}

$account = az account show -o json 2>$null | ConvertFrom-Json
if (-not $account) {
    throw 'Not signed in to Azure. Run: az login'
}
Write-Note "Subscription: $($account.name) ($($account.id))"

# -----------------------------------------------------------------------------
# 1. Check that PostgreSQL can actually be provisioned in the chosen region
# -----------------------------------------------------------------------------
# Many subscriptions - especially trial, sponsored and MCAP ones - are blocked
# from creating flexible servers in the most popular regions. Failing here with
# a useful list beats failing 30 seconds later with a cryptic message.
Write-Step "Checking PostgreSQL availability in '$Location'"

$capabilitiesUrl = "https://management.azure.com/subscriptions/$($account.id)/providers/Microsoft.DBforPostgreSQL/locations/$Location/capabilities?api-version=2024-08-01"
$capabilities = az rest --method get --url $capabilitiesUrl -o json 2>$null | ConvertFrom-Json
$restriction  = $capabilities.value | Select-Object -First 1 -ExpandProperty reason -ErrorAction SilentlyContinue

if ($restriction) {
    Write-Host "`n    '$Location' is restricted for your subscription:" -ForegroundColor Yellow
    Write-Host "    $restriction" -ForegroundColor Yellow
    Write-Host "`n    Probing alternatives..." -ForegroundColor Yellow

    $available = @()
    foreach ($candidate in @('brazilsouth', 'centralus', 'northeurope', 'eastus', 'eastus2', 'westus3', 'westeurope')) {
        $url = "https://management.azure.com/subscriptions/$($account.id)/providers/Microsoft.DBforPostgreSQL/locations/$candidate/capabilities?api-version=2024-08-01"
        $caps = az rest --method get --url $url -o json 2>$null | ConvertFrom-Json
        $why  = $caps.value | Select-Object -First 1 -ExpandProperty reason -ErrorAction SilentlyContinue
        if (-not $why) { $available += $candidate }
    }

    if ($available.Count -eq 0) {
        throw 'No candidate region allows PostgreSQL provisioning for this subscription. Open a support request (Service and subscription limits).'
    }

    throw "Re-run with one of these: pwsh infra/provision.ps1 -Location $($available -join ' | ')"
}
Write-Note 'Region is available.'

# -----------------------------------------------------------------------------
# 2. Resource group
# -----------------------------------------------------------------------------
Write-Step "Creating resource group '$ResourceGroup'"
$existingGroup = az group show --name $ResourceGroup -o json 2>$null | ConvertFrom-Json
if ($existingGroup) {
    Write-Note "Already exists in '$($existingGroup.location)'. Reusing it."
} else {
    az group create --name $ResourceGroup --location $Location -o none
    Write-Note 'Created.'
}

# -----------------------------------------------------------------------------
# 3. PostgreSQL flexible server
# -----------------------------------------------------------------------------
if (-not $AdminPassword) {
    # Avoid ambiguous characters (0/O, 1/l/I) so the password stays copy-pasteable.
    $alphabet = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    $AdminPassword = (-join (1..24 | ForEach-Object { $alphabet[(Get-Random -Maximum $alphabet.Length)] })) + 'x7-Aq'
    $passwordWasProvided = $false
} else {
    $passwordWasProvided = $true
}

Write-Step "Creating PostgreSQL flexible server '$ServerName'"
$existingServer = az postgres flexible-server show -g $ResourceGroup -n $ServerName -o json 2>$null | ConvertFrom-Json

if ($existingServer) {
    Write-Note 'Server already exists. Reusing it.'

    # Azure never returns the admin password, so on a re-run we have to work out
    # what it is. Order of preference:
    #   1. -AdminPassword passed explicitly
    #   2. PGPASSWORD from an existing .env (the normal re-run case)
    #   3. reset the password to a freshly generated one
    if (-not $passwordWasProvided) {
        $recovered = ''
        if (Test-Path $envPath) {
            $line = Select-String -Path $envPath -Pattern '^PGPASSWORD=(.*)$' | Select-Object -First 1
            if ($line) { $recovered = $line.Matches[0].Groups[1].Value }
        }

        if ($recovered) {
            $AdminPassword = $recovered
            Write-Note 'Reusing the password from your existing .env.'
        } else {
            Write-Note 'No password available, so resetting the admin password...'
            az postgres flexible-server update -g $ResourceGroup -n $ServerName --admin-password $AdminPassword -o none
            Write-Note 'Admin password reset.'
        }
    }
} else {
    Write-Note 'This takes a few minutes...'
    az postgres flexible-server create `
        --resource-group $ResourceGroup `
        --name $ServerName `
        --location $Location `
        --admin-user $AdminUser `
        --admin-password $AdminPassword `
        --tier Burstable `
        --sku-name Standard_B1ms `
        --storage-size 32 `
        --version 16 `
        --yes -o none
    Write-Note 'Created.'
}

$server = az postgres flexible-server show -g $ResourceGroup -n $ServerName -o json | ConvertFrom-Json
$fqdn   = $server.fullyQualifiedDomainName
Write-Note "Host: $fqdn"

# -----------------------------------------------------------------------------
# 4. Database
# -----------------------------------------------------------------------------
Write-Step "Creating database '$DatabaseName'"
az postgres flexible-server db create -g $ResourceGroup -s $ServerName -d $DatabaseName -o none 2>$null
Write-Note 'Ready.'

# -----------------------------------------------------------------------------
# 5. Firewall
# -----------------------------------------------------------------------------
# Getting this right is fiddlier than it looks. Services like api.ipify.org
# report the IP of whatever proxy your HTTPS traffic goes through, which on
# corporate networks and VPNs is often NOT the IP that PostgreSQL sees on a raw
# TCP connection. Trusting it produces a firewall rule that silently does
# nothing and a connection that times out with no explanation.
#
# So: open the firewall wide for a moment, ask PostgreSQL itself
# (`SELECT inet_client_addr()`), then replace the wide rule with a narrow one.
Write-Step 'Configuring the firewall for your machine'

az postgres flexible-server firewall-rule create -g $ResourceGroup -n $ServerName `
    --rule-name TempDiscoverClientIp --start-ip-address 0.0.0.0 --end-ip-address 255.255.255.255 -o none 2>$null
Write-Note 'Opened temporarily to detect your real egress IP...'
Start-Sleep -Seconds 20

$connectionUri = "postgresql://$AdminUser`:$AdminPassword@$fqdn`:5432/$DatabaseName`?sslmode=require"

$pythonExe = if (Test-Path (Join-Path $repoRoot '.venv/Scripts/python.exe')) {
    Join-Path $repoRoot '.venv/Scripts/python.exe'
} else { 'python' }

$detectScript = @'
import sys, psycopg
try:
    with psycopg.connect(sys.argv[1], connect_timeout=30) as c:
        with c.cursor() as cur:
            cur.execute("SELECT host(inet_client_addr())")
            print(cur.fetchone()[0])
except Exception as exc:
    print("ERROR:" + str(exc), file=sys.stderr)
    sys.exit(1)
'@

$clientIp = $detectScript | & $pythonExe - $connectionUri 2>$null

if ($LASTEXITCODE -eq 0 -and $clientIp -match '^\d+\.\d+\.\d+\.\d+$') {
    Write-Note "PostgreSQL sees your connections coming from $clientIp"

    # Many corporate and cloud networks NAT through a pool of addresses, so the
    # exact IP can change between connections. Allowing the surrounding /24 is
    # the pragmatic middle ground for a demo. Narrow it if you prefer.
    $octets = $clientIp.Split('.')
    $rangeStart = "$($octets[0]).$($octets[1]).$($octets[2]).0"
    $rangeEnd   = "$($octets[0]).$($octets[1]).$($octets[2]).255"

    az postgres flexible-server firewall-rule create -g $ResourceGroup -n $ServerName `
        --rule-name AllowClientEgressPool --start-ip-address $rangeStart --end-ip-address $rangeEnd -o none 2>$null
    Write-Note "Allowed $rangeStart - $rangeEnd"

    az postgres flexible-server firewall-rule delete -g $ResourceGroup -n $ServerName `
        --rule-name TempDiscoverClientIp --yes -o none 2>$null
    Write-Note 'Removed the temporary wide-open rule.'
} else {
    Write-Host "`n    Could not detect your client IP (is psycopg installed?)." -ForegroundColor Yellow
    Write-Host '    The wide-open rule TempDiscoverClientIp was LEFT IN PLACE so you' -ForegroundColor Yellow
    Write-Host '    can keep going. Delete it when you are done:' -ForegroundColor Yellow
    Write-Host "      az postgres flexible-server firewall-rule delete -g $ResourceGroup -n $ServerName --rule-name TempDiscoverClientIp --yes" -ForegroundColor Yellow
}

# -----------------------------------------------------------------------------
# 6. Azure OpenAI endpoint
# -----------------------------------------------------------------------------
if (-not $AzureOpenAiEndpoint) {
    Write-Step 'Looking for an Azure OpenAI resource'
    $accounts = az cognitiveservices account list -o json 2>$null | ConvertFrom-Json
    $candidate = $accounts | Where-Object { $_.kind -in @('OpenAI', 'AIServices') } | Select-Object -First 1
    if ($candidate) {
        $AzureOpenAiEndpoint = "https://$($candidate.name).openai.azure.com/"
        Write-Note "Found '$($candidate.name)'. Verify the deployment name below matches one you have."
    } else {
        $AzureOpenAiEndpoint = 'https://<your-resource>.openai.azure.com/'
        Write-Note 'None found. Fill AZURE_OPENAI_ENDPOINT in .env manually.'
    }
}

# -----------------------------------------------------------------------------
# 7. Write .env
# -----------------------------------------------------------------------------
Write-Step "Writing $envPath"

if (Test-Path $envPath) {
    $backup = "$envPath.bak"
    Copy-Item $envPath $backup -Force
    Write-Note "Existing .env backed up to $backup"
}

@"
# Generated by infra/provision.ps1 on $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss').
# This file contains a password. It is git-ignored. Do not commit it.

AZURE_OPENAI_ENDPOINT=$AzureOpenAiEndpoint
AZURE_OPENAI_DEPLOYMENT=$AzureOpenAiDeployment
# Leave empty: Agent Framework needs the Responses API, which older
# api-versions do not support.
AZURE_OPENAI_API_VERSION=

PGHOST=$fqdn
PGPORT=5432
PGDATABASE=$DatabaseName
PGUSER=$AdminUser
PGPASSWORD=$AdminPassword
PGSSLMODE=require

POSTGRES_MCP_ACCESS_MODE=unrestricted
POSTGRES_MCP_APPROVAL_MODE=never_require
"@ | Set-Content -Path $envPath -Encoding utf8

# -----------------------------------------------------------------------------
# Done
# -----------------------------------------------------------------------------
Write-Host "`n=============================================================" -ForegroundColor Green
Write-Host ' Provisioning complete' -ForegroundColor Green
Write-Host '=============================================================' -ForegroundColor Green
Write-Host @"

  Resource group : $ResourceGroup
  Server         : $fqdn
  Database       : $DatabaseName
  Admin user     : $AdminUser

Next steps:

  1. Load the sample data:
       python -m infra.seed

  2. Sign in so the agent can call Azure OpenAI with Entra ID:
       az login

  3. Chat with the agent:
       python -m src.maf.main

When you are done, stop the server so it stops costing money:
       az postgres flexible-server stop -g $ResourceGroup -n $ServerName

Or delete everything:
       pwsh infra/teardown.ps1 -ResourceGroup $ResourceGroup

"@
