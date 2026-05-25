#!/bin/bash
# restart.sh — stop all services, pull latest code, restart everything
# Usage: bash restart.sh

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/venv/bin/activate"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  TradingLuna — Restart"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Stop all screen sessions ──────────────────────────────────────────────────
echo ""
echo "▶ Stopping services..."
for session in trader fill_monitor bot dashboard webhook; do
    if screen -S "$session" -X quit 2>/dev/null; then
        echo "  ✓ Stopped $session"
    else
        echo "  – $session was not running"
    fi
done
sleep 1

# ── Pull latest code ──────────────────────────────────────────────────────────
echo ""
echo "▶ Pulling latest code..."
cd "$DIR"
git pull origin main

# ── Restart all services ──────────────────────────────────────────────────────
echo ""
echo "▶ Starting services..."

screen -dmS trader      bash -c "cd $DIR && source $VENV && python trader.py"
echo "  ✓ trader started"

screen -dmS fill_monitor bash -c "cd $DIR && source $VENV && python fill_monitor.py"
echo "  ✓ fill_monitor started"

screen -dmS bot         bash -c "cd $DIR && source $VENV && python bot.py"
echo "  ✓ bot started"

screen -dmS dashboard   bash -c "cd $DIR && source $VENV && python dashboard.py"
echo "  ✓ dashboard started"

screen -dmS webhook     bash -c "cd $DIR && source $VENV && python webhook.py"
echo "  ✓ webhook started"

# ── Verify ────────────────────────────────────────────────────────────────────
echo ""
echo "▶ Running sessions:"
screen -ls

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Done! All services restarted."
echo "  Attach to any session with: screen -r <name>"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
