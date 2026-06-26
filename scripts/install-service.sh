#!/usr/bin/env bash
# One-time systemd install for the WorldPool bot.
# Run on the VPS with: sudo bash ~/worldpool/scripts/install-service.sh
set -e
SERVICE=quadriga-automations-worldpool
cp /home/murtaza/worldpool/deploy/$SERVICE.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now $SERVICE
sleep 4
echo "=== is-active ==="
systemctl is-active $SERVICE || true
echo "=== last 18 log lines ==="
journalctl -u $SERVICE -n 18 --no-pager
