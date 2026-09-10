<#
.SYNOPSIS
    Stops or deletes the Azure resources created by provision.ps1.

.DESCRIPTION
    A Burstable B1ms PostgreSQL server costs roughly USD 15-20 a month if you
    leave it running. Two ways to stop paying:

      -Stop    pause the server (keeps your data; Azure auto-resumes it after
               7 days, so this is not a permanent solution)
      default  delete the whole resource group (irreversible)

.EXAMPLE
    pwsh infra/teardown.ps1 -Stop

.EXAMPLE
    pwsh infra/teardown.ps1
#>
[CmdletBinding()]
param(
    [string] $ResourceGroup = 'rg-maf-postgres-demo',

    # Only needed with -Stop. Discovered automatically when omitted.
    [string] $ServerName = '',

    # Pause the server instead of deleting everything.
    [switch] $Stop,

    # Skip the confirmation prompt.
    [switch] $Force
)

$ErrorActionPreference = 'Stop'

az account show -o none 2>$null
if ($LASTEXITCODE -ne 0) {
    throw 'Not signed in to Azure. Run: az login'
}

az group show --name $ResourceGroup -o none 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Resource group '$ResourceGroup' does not exist. Nothing to do." -ForegroundColor Yellow
    exit 0
}

if ($Stop) {
    if (-not $ServerName) {
        $ServerName = az postgres flexible-server list -g $ResourceGroup --query '[0].name' -o tsv
    }
    if (-not $ServerName) {
        Write-Host "No PostgreSQL server found in '$ResourceGroup'." -ForegroundColor Yellow
        exit 0
    }

    Write-Host "Stopping '$ServerName'..." -ForegroundColor Cyan
    az postgres flexible-server stop -g $ResourceGroup -n $ServerName -o none
    Write-Host @"

Stopped. Compute charges pause; storage is still billed (a few cents a month).

  Restart it with:
    az postgres flexible-server start -g $ResourceGroup -n $ServerName

  Azure automatically restarts stopped servers after 7 days.
"@ -ForegroundColor Green
    exit 0
}

# --- full delete -------------------------------------------------------------
Write-Host "`nAbout to DELETE the resource group '$ResourceGroup' and everything in it." -ForegroundColor Red
az resource list -g $ResourceGroup --query '[].{name:name, type:type}' -o table

if (-not $Force) {
    $answer = Read-Host "`nType the resource group name to confirm"
    if ($answer -ne $ResourceGroup) {
        Write-Host 'Cancelled.' -ForegroundColor Yellow
        exit 1
    }
}

az group delete --name $ResourceGroup --yes --no-wait
Write-Host "`nDeletion started in the background. Check progress with:" -ForegroundColor Green
Write-Host "  az group show --name $ResourceGroup" -ForegroundColor Green
Write-Host "`nRemember to delete your local .env too - it still has the old credentials." -ForegroundColor Yellow
