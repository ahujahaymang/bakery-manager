#!/bin/bash
# update_dns.sh — Update kitchenos.info A record with current EC2 public IP
#
# Runs at system startup via systemd (kitchenos-dns-update.service).
# Requires IONOS_API_KEY and IONOS_API_SECRET in /opt/kitchenos/.env
# or as environment variables.
#
# IONOS DNS API docs: https://developer.hosting.ionos.com/docs/dns

set -euo pipefail

DOMAIN="kitchenos.info"
RECORDS=("@" "www")  # A records to update
ENV_FILE="/opt/kitchenos/.env"
LOG_TAG="kitchenos-dns"

log() { logger -t "$LOG_TAG" "$*"; echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

# Load env file
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  export $(grep -E '^IONOS_API_KEY=|^IONOS_API_SECRET=' "$ENV_FILE" | xargs)
fi

if [ -z "${IONOS_API_KEY:-}" ] || [ -z "${IONOS_API_SECRET:-}" ]; then
  log "IONOS_API_KEY or IONOS_API_SECRET not set — skipping DNS update"
  exit 0
fi

# Get current public IP from EC2 instance metadata (IMDSv2)
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
PUBLIC_IP=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/public-ipv4)

if [ -z "$PUBLIC_IP" ]; then
  log "Could not determine public IP — skipping DNS update"
  exit 1
fi

log "Current public IP: $PUBLIC_IP"

# IONOS API base URL
IONOS_API="https://api.hosting.ionos.com/dns/v1"
AUTH_HEADER="X-API-Key: ${IONOS_API_KEY}.${IONOS_API_SECRET}"

# Get the zone ID for kitchenos.info
ZONES=$(curl -s -H "$AUTH_HEADER" "$IONOS_API/zones")
ZONE_ID=$(echo "$ZONES" | python3 -c "
import json, sys
zones = json.load(sys.stdin)
for z in zones:
    if z.get('name') == '$DOMAIN':
        print(z['id'])
        break
")

if [ -z "$ZONE_ID" ]; then
  log "Zone '$DOMAIN' not found in IONOS — check API credentials"
  exit 1
fi

log "Zone ID: $ZONE_ID"

# Update each A record
for RECORD_NAME in "${RECORDS[@]}"; do
  # Build the full record name (@ = root = kitchenos.info)
  if [ "$RECORD_NAME" = "@" ]; then
    FULL_NAME="$DOMAIN"
  else
    FULL_NAME="${RECORD_NAME}.${DOMAIN}"
  fi

  # Patch the A record
  RESULT=$(curl -s -o /dev/null -w "%{http_code}" \
    -X PATCH "$IONOS_API/zones/$ZONE_ID" \
    -H "$AUTH_HEADER" \
    -H "Content-Type: application/json" \
    -d "[{\"name\": \"$FULL_NAME\", \"type\": \"A\", \"content\": \"$PUBLIC_IP\", \"ttl\": 300, \"prio\": 0, \"disabled\": false}]")

  if [ "$RESULT" = "200" ]; then
    log "Updated $FULL_NAME → $PUBLIC_IP"
  else
    log "Failed to update $FULL_NAME (HTTP $RESULT)"
  fi
done

log "DNS update complete"
