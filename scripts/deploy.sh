#!/bin/bash
# =============================================================================
# KitchenOS Safe Deploy Script
#
# Deploys the latest code from GitHub with:
#   - Pre-deploy smoke test (import check)
#   - Automatic rollback if the service fails to start
#   - Git tag on every successful deploy
#
# Usage (on the EC2 instance):
#   bash /opt/kitchenos/scripts/deploy.sh
#
# Or remotely via SSM:
#   aws ssm send-command \
#     --instance-ids i-XXXXXXXXX \
#     --document-name "AWS-RunShellScript" \
#     --parameters 'commands=["bash /opt/kitchenos/scripts/deploy.sh"]'
# =============================================================================

set -euo pipefail

APP_DIR=/opt/kitchenos
NEW_DIR=/opt/kitchenos-new
OLD_DIR=/opt/kitchenos-old
SERVICE=kitchenos
REGION=us-east-1
SSM_PREFIX=/kitchenos/prod

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
fail() { log "ERROR: $*"; exit 1; }

# ── Step 1: Fetch GitHub token ────────────────────────────────────────────────
log "Fetching deploy credentials..."
GITHUB_TOKEN=$(aws ssm get-parameter \
  --name "$SSM_PREFIX/GITHUB_TOKEN" \
  --with-decryption \
  --query Parameter.Value \
  --output text \
  --region "$REGION" 2>/dev/null || echo "")

# ── Step 2: Clone latest code to a staging directory ─────────────────────────
log "Cloning latest code to $NEW_DIR..."
rm -rf "$NEW_DIR"

if [ -n "$GITHUB_TOKEN" ]; then
  git clone "https://$GITHUB_TOKEN@github.com/ahujahaymang/bakery-manager" "$NEW_DIR"
else
  git clone "https://github.com/ahujahaymang/bakery-manager" "$NEW_DIR"
fi

NEW_COMMIT=$(git -C "$NEW_DIR" rev-parse --short HEAD)
CURRENT_COMMIT=$(git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")
log "Current: $CURRENT_COMMIT → New: $NEW_COMMIT"

if [ "$NEW_COMMIT" = "$CURRENT_COMMIT" ]; then
  log "Already up to date. Nothing to deploy."
  rm -rf "$NEW_DIR"
  exit 0
fi

# ── Step 3: Install dependencies ─────────────────────────────────────────────
log "Installing dependencies..."
pip3.11 install -q -r "$NEW_DIR/requirements.txt"

# ── Step 4: Copy .env (preserve secrets — never stored in git) ───────────────
log "Copying .env from current deployment..."
cp "$APP_DIR/.env" "$NEW_DIR/.env"

# App-first: ensure the app-level HTTPS redirect is OFF behind nginx, which
# already forces https (certbot --redirect). Left on, uvicorn behind the proxy
# sees plain http on 127.0.0.1:8000 and 307-loops /app. Idempotent — only
# append when the key is not already present.
if ! grep -q '^ENABLE_HTTPS_REDIRECT=' "$NEW_DIR/.env"; then
  echo 'ENABLE_HTTPS_REDIRECT=false' >> "$NEW_DIR/.env"
  log "Added ENABLE_HTTPS_REDIRECT=false to .env (nginx handles the redirect)"
fi

# ── Step 5: Smoke test — does the app import cleanly? ────────────────────────
log "Running smoke test..."
cd "$NEW_DIR"
if ! timeout 15 python3.11 -c "
from app.telegram_listener import TelegramBotListener
from app.handlers.request_handler import RequestHandler
from app.services.admin_notifier import AdminNotifier
print('Smoke test passed')
"; then
  log "Smoke test FAILED — aborting deploy"
  rm -rf "$NEW_DIR"
  fail "New code failed import check. Deploy aborted. Production is unchanged."
fi

# ── Step 6: Run database migrations ──────────────────────────────────────────
log "Running database migrations..."
cd "$NEW_DIR"
if ! python3.11 -m alembic upgrade head; then
  log "Migration FAILED — aborting deploy"
  rm -rf "$NEW_DIR"
  fail "Database migration failed. Deploy aborted. Production is unchanged."
fi

# App-first pivot migration: create the registry auth tables and add
# orders.created_by_user_id per tenant. Required because `alembic upgrade` does
# not create the SQLite app-first schema. Idempotent and additive, so it is
# safe to re-run on every deploy.
log "Running app-first migration (auth tables + attribution column)..."
if ! python3.11 -m scripts.migrate_app_first; then
  log "App-first migration FAILED — aborting deploy"
  rm -rf "$NEW_DIR"
  fail "App-first migration failed. Deploy aborted. Production is unchanged."
fi

# ── Step 7: Tag the current working deploy before swapping ───────────────────
log "Tagging current deploy as rollback point..."
cd "$APP_DIR"
git tag "deploy-$(date +%Y%m%d-%H%M)" HEAD 2>/dev/null || true

# ── Step 8: Swap directories ──────────────────────────────────────────────────
log "Swapping to new version..."
rm -rf "$OLD_DIR"
mv "$APP_DIR" "$OLD_DIR"
mv "$NEW_DIR" "$APP_DIR"

# ── Step 9: Restart service ───────────────────────────────────────────────────
log "Restarting $SERVICE..."
systemctl restart "$SERVICE"
sleep 8

# ── Step 10: Health check — did it stay up? ───────────────────────────────────
if systemctl is-active --quiet "$SERVICE"; then
  log "✅ Deploy successful! Running commit: $NEW_COMMIT"
  log "Previous version saved at: $OLD_DIR"
else
  log "❌ Service failed to start after deploy — rolling back..."
  systemctl stop "$SERVICE" 2>/dev/null || true
  mv "$APP_DIR" "${NEW_DIR}-failed"
  mv "$OLD_DIR" "$APP_DIR"
  systemctl start "$SERVICE"
  sleep 5
  if systemctl is-active --quiet "$SERVICE"; then
    log "✅ Rollback successful. Running previous commit: $CURRENT_COMMIT"
  else
    fail "Rollback also failed. Manual intervention required. Check: journalctl -u $SERVICE -n 50"
  fi
  fail "Deploy failed and was rolled back to $CURRENT_COMMIT"
fi
