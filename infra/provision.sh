#!/usr/bin/env bash
# =============================================================================
# Provisions everything this sample needs on Azure and writes a ready-to-use .env
# =============================================================================
#
# Creates:
#   * a resource group
#   * an Azure Database for PostgreSQL flexible server (Burstable B1ms - the
#     cheapest tier) with password authentication
#   * a database
#   * a firewall rule for your machine's real egress IP
#
# Idempotent: run it again and it reuses what already exists.
#
# Usage:
#   bash infra/provision.sh
#   LOCATION=centralus SERVER_NAME=my-fiberops-db bash infra/provision.sh
# =============================================================================

set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-maf-postgres-demo}"

# NOTE: PostgreSQL flexible server provisioning is restricted in several regions
# for many subscriptions ("The location is restricted from performing this
# operation"). We check before trying and suggest alternatives.
LOCATION="${LOCATION:-brazilsouth}"

SERVER_NAME="${SERVER_NAME:-maf-fiberops-$(head -c 4 /dev/urandom | od -An -tx1 | tr -d ' \n')}"
ADMIN_USER="${ADMIN_USER:-fiberadmin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
DATABASE_NAME="${DATABASE_NAME:-fiberops}"
AZURE_OPENAI_ENDPOINT_IN="${AZURE_OPENAI_ENDPOINT:-}"
AZURE_OPENAI_DEPLOYMENT_IN="${AZURE_OPENAI_DEPLOYMENT:-gpt-4.1}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_PATH="$REPO_ROOT/.env"

CYAN='\033[36m'; GREY='\033[90m'; YELLOW='\033[33m'; GREEN='\033[32m'; RESET='\033[0m'
step() { printf "\n${CYAN}==> %s${RESET}\n" "$1"; }
note() { printf "${GREY}    %s${RESET}\n" "$1"; }
warn() { printf "${YELLOW}    %s${RESET}\n" "$1"; }

# -----------------------------------------------------------------------------
# 0. Prerequisites
# -----------------------------------------------------------------------------
step "Checking prerequisites"

command -v az >/dev/null 2>&1 || {
  echo "Azure CLI (az) not found. Install it from https://aka.ms/installazurecli" >&2
  exit 1
}

SUBSCRIPTION_ID="$(az account show --query id -o tsv 2>/dev/null || true)"
if [[ -z "$SUBSCRIPTION_ID" ]]; then
  echo "Not signed in to Azure. Run: az login" >&2
  exit 1
fi
note "Subscription: $(az account show --query name -o tsv) ($SUBSCRIPTION_ID)"

# -----------------------------------------------------------------------------
# 1. Region availability
# -----------------------------------------------------------------------------
region_restriction() {
  az rest --method get \
    --url "https://management.azure.com/subscriptions/${SUBSCRIPTION_ID}/providers/Microsoft.DBforPostgreSQL/locations/$1/capabilities?api-version=2024-08-01" \
    --query "value[0].reason" -o tsv 2>/dev/null || true
}

step "Checking PostgreSQL availability in '$LOCATION'"
RESTRICTION="$(region_restriction "$LOCATION")"

if [[ -n "$RESTRICTION" && "$RESTRICTION" != "None" ]]; then
  warn "'$LOCATION' is restricted for your subscription:"
  warn "$RESTRICTION"
  warn "Probing alternatives..."
  AVAILABLE=()
  for candidate in brazilsouth centralus northeurope eastus eastus2 westus3 westeurope; do
    WHY="$(region_restriction "$candidate")"
    if [[ -z "$WHY" || "$WHY" == "None" ]]; then AVAILABLE+=("$candidate"); fi
  done
  if [[ ${#AVAILABLE[@]} -eq 0 ]]; then
    echo "No candidate region allows PostgreSQL provisioning for this subscription." >&2
    echo "Open a support request (Service and subscription limits)." >&2
    exit 1
  fi
  echo "Re-run with one of: LOCATION=${AVAILABLE[*]} bash infra/provision.sh" >&2
  exit 1
fi
note "Region is available."

# -----------------------------------------------------------------------------
# 2. Resource group
# -----------------------------------------------------------------------------
step "Creating resource group '$RESOURCE_GROUP'"
EXISTING_LOCATION="$(az group show --name "$RESOURCE_GROUP" --query location -o tsv 2>/dev/null || true)"
if [[ -n "$EXISTING_LOCATION" ]]; then
  note "Already exists in '$EXISTING_LOCATION'. Reusing it."
else
  az group create --name "$RESOURCE_GROUP" --location "$LOCATION" -o none
  note "Created."
fi

# -----------------------------------------------------------------------------
# 3. PostgreSQL flexible server
# -----------------------------------------------------------------------------
if [[ -z "$ADMIN_PASSWORD" ]]; then
  # Avoid ambiguous characters so the password stays copy-pasteable.
  ADMIN_PASSWORD="$(LC_ALL=C tr -dc 'a-km-zA-HJ-NP-Z2-9' </dev/urandom | head -c 24)x7-Aq"
  PASSWORD_WAS_PROVIDED="no"
else
  PASSWORD_WAS_PROVIDED="yes"
fi

step "Creating PostgreSQL flexible server '$SERVER_NAME'"
if az postgres flexible-server show -g "$RESOURCE_GROUP" -n "$SERVER_NAME" -o none 2>/dev/null; then
  note "Server already exists. Reusing it."

  # Azure never returns the admin password, so on a re-run we have to work out
  # what it is. Order of preference:
  #   1. ADMIN_PASSWORD passed explicitly
  #   2. PGPASSWORD from an existing .env (the normal re-run case)
  #   3. reset the password to a freshly generated one
  if [[ "$PASSWORD_WAS_PROVIDED" == "no" ]]; then
    RECOVERED=""
    if [[ -f "$ENV_PATH" ]]; then
      RECOVERED="$(sed -n 's/^PGPASSWORD=\(.*\)$/\1/p' "$ENV_PATH" | head -n 1)"
    fi

    if [[ -n "$RECOVERED" ]]; then
      ADMIN_PASSWORD="$RECOVERED"
      note "Reusing the password from your existing .env."
    else
      note "No password available, so resetting the admin password..."
      az postgres flexible-server update -g "$RESOURCE_GROUP" -n "$SERVER_NAME" --admin-password "$ADMIN_PASSWORD" -o none
      note "Admin password reset."
    fi
  fi
else
  note "This takes a few minutes..."
  az postgres flexible-server create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$SERVER_NAME" \
    --location "$LOCATION" \
    --admin-user "$ADMIN_USER" \
    --admin-password "$ADMIN_PASSWORD" \
    --tier Burstable \
    --sku-name Standard_B1ms \
    --storage-size 32 \
    --version 16 \
    --yes -o none
  note "Created."
fi

FQDN="$(az postgres flexible-server show -g "$RESOURCE_GROUP" -n "$SERVER_NAME" --query fullyQualifiedDomainName -o tsv)"
note "Host: $FQDN"

# -----------------------------------------------------------------------------
# 4. Database
# -----------------------------------------------------------------------------
step "Creating database '$DATABASE_NAME'"
az postgres flexible-server db create -g "$RESOURCE_GROUP" -s "$SERVER_NAME" -d "$DATABASE_NAME" -o none 2>/dev/null || true
note "Ready."

# -----------------------------------------------------------------------------
# 5. Firewall
# -----------------------------------------------------------------------------
# Getting this right is fiddlier than it looks. Services like api.ipify.org
# report the IP of whatever proxy your HTTPS traffic goes through, which on
# corporate networks and VPNs is often NOT the IP PostgreSQL sees on a raw TCP
# connection. Trusting it produces a firewall rule that silently does nothing
# and a connection that times out with no explanation.
#
# So: open the firewall wide for a moment, ask PostgreSQL itself
# (SELECT inet_client_addr()), then replace the wide rule with a narrow one.
step "Configuring the firewall for your machine"

az postgres flexible-server firewall-rule create -g "$RESOURCE_GROUP" -n "$SERVER_NAME" \
  --rule-name TempDiscoverClientIp --start-ip-address 0.0.0.0 --end-ip-address 255.255.255.255 -o none 2>/dev/null || true
note "Opened temporarily to detect your real egress IP..."
sleep 20

CONNECTION_URI="postgresql://${ADMIN_USER}:${ADMIN_PASSWORD}@${FQDN}:5432/${DATABASE_NAME}?sslmode=require"

if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON_EXE="$REPO_ROOT/.venv/bin/python"
else
  PYTHON_EXE="python3"
fi

CLIENT_IP="$("$PYTHON_EXE" - "$CONNECTION_URI" <<'PY' 2>/dev/null || true
import sys, psycopg
with psycopg.connect(sys.argv[1], connect_timeout=30) as c:
    with c.cursor() as cur:
        cur.execute("SELECT host(inet_client_addr())")
        print(cur.fetchone()[0])
PY
)"

if [[ "$CLIENT_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  note "PostgreSQL sees your connections coming from $CLIENT_IP"

  # Many corporate and cloud networks NAT through a pool of addresses, so the
  # exact IP can change between connections. Allowing the surrounding /24 is the
  # pragmatic middle ground for a demo. Narrow it if you prefer.
  IFS='.' read -r o1 o2 o3 _ <<<"$CLIENT_IP"
  az postgres flexible-server firewall-rule create -g "$RESOURCE_GROUP" -n "$SERVER_NAME" \
    --rule-name AllowClientEgressPool \
    --start-ip-address "${o1}.${o2}.${o3}.0" --end-ip-address "${o1}.${o2}.${o3}.255" -o none 2>/dev/null
  note "Allowed ${o1}.${o2}.${o3}.0 - ${o1}.${o2}.${o3}.255"

  az postgres flexible-server firewall-rule delete -g "$RESOURCE_GROUP" -n "$SERVER_NAME" \
    --rule-name TempDiscoverClientIp --yes -o none 2>/dev/null || true
  note "Removed the temporary wide-open rule."
else
  warn "Could not detect your client IP (is psycopg installed?)."
  warn "The wide-open rule TempDiscoverClientIp was LEFT IN PLACE so you can"
  warn "keep going. Delete it when you are done:"
  warn "  az postgres flexible-server firewall-rule delete -g $RESOURCE_GROUP -n $SERVER_NAME --rule-name TempDiscoverClientIp --yes"
fi

# -----------------------------------------------------------------------------
# 6. Azure OpenAI endpoint
# -----------------------------------------------------------------------------
if [[ -z "$AZURE_OPENAI_ENDPOINT_IN" ]]; then
  step "Looking for an Azure OpenAI resource"
  CANDIDATE="$(az cognitiveservices account list --query "[?kind=='OpenAI' || kind=='AIServices'] | [0].name" -o tsv 2>/dev/null || true)"
  if [[ -n "$CANDIDATE" && "$CANDIDATE" != "None" ]]; then
    AZURE_OPENAI_ENDPOINT_IN="https://${CANDIDATE}.openai.azure.com/"
    note "Found '$CANDIDATE'. Verify the deployment name below matches one you have."
  else
    AZURE_OPENAI_ENDPOINT_IN="https://<your-resource>.openai.azure.com/"
    note "None found. Fill AZURE_OPENAI_ENDPOINT in .env manually."
  fi
fi

# -----------------------------------------------------------------------------
# 7. Write .env
# -----------------------------------------------------------------------------
step "Writing $ENV_PATH"
if [[ -f "$ENV_PATH" ]]; then
  cp "$ENV_PATH" "$ENV_PATH.bak"
  note "Existing .env backed up to $ENV_PATH.bak"
fi

cat >"$ENV_PATH" <<EOF
# Generated by infra/provision.sh on $(date '+%Y-%m-%d %H:%M:%S').
# This file contains a password. It is git-ignored. Do not commit it.

AZURE_OPENAI_ENDPOINT=$AZURE_OPENAI_ENDPOINT_IN
AZURE_OPENAI_DEPLOYMENT=$AZURE_OPENAI_DEPLOYMENT_IN
# Leave empty: Agent Framework needs the Responses API, which older
# api-versions do not support.
AZURE_OPENAI_API_VERSION=

PGHOST=$FQDN
PGPORT=5432
PGDATABASE=$DATABASE_NAME
PGUSER=$ADMIN_USER
PGPASSWORD=$ADMIN_PASSWORD
PGSSLMODE=require

POSTGRES_MCP_ACCESS_MODE=unrestricted
POSTGRES_MCP_APPROVAL_MODE=never_require
EOF

printf "\n${GREEN}=============================================================${RESET}\n"
printf "${GREEN} Provisioning complete${RESET}\n"
printf "${GREEN}=============================================================${RESET}\n"
cat <<EOF

  Resource group : $RESOURCE_GROUP
  Server         : $FQDN
  Database       : $DATABASE_NAME
  Admin user     : $ADMIN_USER

Next steps:

  1. Load the sample data:
       python -m infra.seed

  2. Sign in so the agent can call Azure OpenAI with Entra ID:
       az login

  3. Chat with the agent:
       python -m src.maf.main

When you are done, stop the server so it stops costing money:
       az postgres flexible-server stop -g $RESOURCE_GROUP -n $SERVER_NAME

Or delete everything:
       bash infra/teardown.sh

EOF
