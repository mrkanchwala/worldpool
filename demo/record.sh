#!/usr/bin/env bash
# WorldPool demo recording — full auto.
#
# What it does:
#   1. Stops the VPS bot (frees the bot token for local use)
#   2. Starts demo_match.py locally
#   3. Finds your main screen with ffmpeg
#   4. Starts screen recording
#   5. Runs drive_demo.py (auto-sends commands + clicks every button)
#   6. Stops recording + demo bot
#   7. Restarts the VPS bot
#
# First run only: drive_demo.py will prompt your phone number + Telegram OTP.
# After that, demo_driver.session saves credentials — fully silent from then on.
#
# Usage (from worldpool repo root):
#   bash demo/record.sh
#
# Output: worldpool-demo-YYYYMMDD-HHMMSS.mp4 in repo root
#
# Built by Quadriga Automations · Murtaza Kanchwala · quadrigasolutions.com

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT="$REPO/worldpool-demo-$(date +%Y%m%d-%H%M%S).mp4"
VPS="murtaza@46.225.110.140"
SERVICE="quadriga-automations-worldpool"
VENV="$REPO/.venv/bin/python3"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  WorldPool — Demo Recording                          ║"
echo "║  Quadriga Automations · Murtaza Kanchwala            ║"
echo "║  quadrigasolutions.com                               ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

cd "$REPO"

# ── 1. Stop VPS bot ───────────────────────────────────────────────────────────
echo "==> [1/8] Stopping VPS bot (freeing bot token)..."
if ssh -o ConnectTimeout=5 "$VPS" "sudo systemctl stop $SERVICE" 2>/dev/null; then
    echo "    stopped."
else
    echo "    VPS unreachable or already stopped — continuing."
fi
sleep 1

# ── 2. Start demo bot locally ─────────────────────────────────────────────────
echo "==> [2/8] Starting demo bot locally..."
"$VENV" demo/demo_match.py > /tmp/worldpool-demo-bot.log 2>&1 &
BOT_PID=$!
echo "    PID $BOT_PID — waiting 6s for Telegram connect..."
sleep 6

# Check bot started correctly
if ! kill -0 $BOT_PID 2>/dev/null; then
    echo "    ❌ Demo bot failed to start. Check /tmp/worldpool-demo-bot.log"
    exit 1
fi
echo "    ✅ Demo bot running."

# ── 3. Detect screen ─────────────────────────────────────────────────────────
echo "==> [3/8] Detecting screen index for ffmpeg..."
# Try to find "Capture screen" device; default to 1 if not found
DISPLAY_IDX=$(ffmpeg -f avfoundation -list_devices true -i "" 2>&1 | \
    grep -i "capture screen\|display" | head -1 | \
    grep -oE '\[[0-9]+\]' | tr -d '[]' 2>/dev/null || echo "1")
echo "    Using display index: $DISPLAY_IDX"

# ── 4. Pre-recording pause ───────────────────────────────────────────────────
echo ""
echo "    ┌──────────────────────────────────────────────────┐"
echo "    │  ACTION REQUIRED:                                │"
echo "    │  Open Telegram Desktop → navigate to @txodds_mkbot│"
echo "    │  Make the window visible and large on screen.   │"
echo "    │                                                  │"
echo "    │  Recording starts in 8 seconds...               │"
echo "    └──────────────────────────────────────────────────┘"
echo ""
sleep 8

# ── 5. Start recording ────────────────────────────────────────────────────────
echo "==> [4/8] Recording → $(basename "$OUTPUT")"
ffmpeg -f avfoundation -framerate 30 -i "${DISPLAY_IDX}" \
    -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" \
    -c:v libx264 -preset fast -crf 20 \
    -t 80 \
    "$OUTPUT" \
    > /tmp/worldpool-ffmpeg.log 2>&1 &
REC_PID=$!
sleep 2

if ! kill -0 $REC_PID 2>/dev/null; then
    echo "    ❌ ffmpeg failed to start. Check /tmp/worldpool-ffmpeg.log"
    cat /tmp/worldpool-ffmpeg.log | tail -10
    kill $BOT_PID 2>/dev/null || true
    exit 1
fi
echo "    ✅ Recording started."

# ── 6. Run auto-driver ────────────────────────────────────────────────────────
echo "==> [5/8] Auto-driver running (controls @txodds_mkbot)..."
echo ""
"$VENV" demo/drive_demo.py
echo ""

# ── 7. Hold on payout card, then stop ────────────────────────────────────────
echo "==> [6/8] Holding on payout screen (3s)..."
sleep 3

echo "==> [7/8] Stopping recording..."
kill $REC_PID 2>/dev/null || true
wait $REC_PID 2>/dev/null || true

# ── 8. Cleanup ────────────────────────────────────────────────────────────────
echo "==> [8/8] Stopping demo bot..."
kill $BOT_PID 2>/dev/null || true
wait $BOT_PID 2>/dev/null || true

echo "    Restarting VPS bot..."
if ssh -o ConnectTimeout=5 "$VPS" "sudo systemctl start $SERVICE" 2>/dev/null; then
    echo "    ✅ VPS bot restarted."
else
    echo "    ⚠️  Restart manually: ssh $VPS sudo systemctl start $SERVICE"
fi

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  ✅ Done!                                            ║"
printf  "║  Video: %-44s║\n" "$(basename "$OUTPUT")"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "  Full path: $OUTPUT"
echo ""
