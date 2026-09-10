#!/usr/bin/env bash
# =============================================================================
# Stops or deletes the Azure resources created by provision.sh
# =============================================================================
#
# A Burstable B1ms PostgreSQL server costs roughly USD 15-20 a month if you
# leave it running. Two ways to stop paying:
#
#   --stop    pause the server (keeps your data; Azure auto-resumes it after
#             7 days, so this is not a permanent solution)
#   default   delete the whole resource group (irreversible)
#
# Usage:
#   bash infra/teardown.sh --stop
#   bash infra/teardown.sh
#   bash infra/teardown.sh --force
# =============================================================================

set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-maf-postgres-demo}"
SERVER_NAME="${SERVER_NAME:-}"
MODE="delete"
FORCE="no"

for arg in "$@"; do
  case "$arg" in
    --stop)  MODE="stop" ;;
    --force) FORCE="yes" ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

az account show -o none 2>/dev/null || { echo "Not signed in to Azure. Run: az login" >&2; exit 1; }

if ! az group show --name "$RESOURCE_GROUP" -o none 2>/dev/null; then
  echo "Resource group '$RESOURCE_GROUP' does not exist. Nothing to do."
  exit 0
fi

if [[ "$MODE" == "stop" ]]; then
  if [[ -z "$SERVER_NAME" ]]; then
    SERVER_NAME="$(az postgres flexible-server list -g "$RESOURCE_GROUP" --query '[0].name' -o tsv)"
  fi
  if [[ -z "$SERVER_NAME" || "$SERVER_NAME" == "None" ]]; then
    echo "No PostgreSQL server found in '$RESOURCE_GROUP'."
    exit 0
  fi

  echo "Stopping '$SERVER_NAME'..."
  az postgres flexible-server stop -g "$RESOURCE_GROUP" -n "$SERVER_NAME" -o none
  cat <<EOF

Stopped. Compute charges pause; storage is still billed (a few cents a month).

  Restart it with:
    az postgres flexible-server start -g $RESOURCE_GROUP -n $SERVER_NAME

  Azure automatically restarts stopped servers after 7 days.
EOF
  exit 0
fi

# --- full delete -------------------------------------------------------------
echo
echo "About to DELETE the resource group '$RESOURCE_GROUP' and everything in it."
az resource list -g "$RESOURCE_GROUP" --query '[].{name:name, type:type}' -o table

if [[ "$FORCE" != "yes" ]]; then
  read -r -p $'\nType the resource group name to confirm: ' ANSWER
  if [[ "$ANSWER" != "$RESOURCE_GROUP" ]]; then
    echo "Cancelled."
    exit 1
  fi
fi

az group delete --name "$RESOURCE_GROUP" --yes --no-wait
echo
echo "Deletion started in the background. Check progress with:"
echo "  az group show --name $RESOURCE_GROUP"
echo
echo "Remember to delete your local .env too - it still has the old credentials."
