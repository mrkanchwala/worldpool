#!/usr/bin/env bash
# WorldPool VPS deploy — rsync code+secrets over SSH, set up venv, (re)start systemd service.
# Run from the worldpool repo root:  bash scripts/deploy-vps.sh
#
# Secrets (.env, keys/) ARE transferred (rsync over SSH is encrypted) — the bot needs them.
# node_modules / anchor / .git / *.db / *.session are excluded (not needed at runtime on VPS).
set -euo pipefail

VPS="murtaza@46.225.110.140"
REMOTE="/home/murtaza/worldpool"
SERVICE="quadriga-automations-worldpool"

echo "==> [1/4] Syncing to VPS ($REMOTE)..."
rsync -az --delete \
  --exclude '.git/' \
  --exclude 'node_modules/' \
  --exclude 'anchor/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '*.db' --exclude '*.db-wal' --exclude '*.db-shm' \
  --exclude '*.session' --exclude '*.session-journal' \
  --exclude '.venv/' \
  ./ "$VPS:$REMOTE/"

echo "==> [2/4] Python venv + deps on VPS..."
ssh "$VPS" "cd $REMOTE && (test -d .venv || python3 -m venv .venv) && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt"

echo "==> [3/4] Restarting service..."
if ssh "$VPS" "sudo systemctl restart $SERVICE" 2>/dev/null; then
  echo "    restarted."
else
  echo ""
  echo "    Service not installed yet. One-time install (run these on VPS — needs sudo):"
  echo "      sudo cp $REMOTE/deploy/$SERVICE.service /etc/systemd/system/"
  echo "      sudo systemctl daemon-reload"
  echo "      sudo systemctl enable --now $SERVICE"
  echo ""
  exit 0
fi

echo "==> [4/4] Status:"
ssh "$VPS" "systemctl is-active $SERVICE; journalctl -u $SERVICE -n 12 --no-pager"
echo "==> Deploy complete."
