"""
Morning health check — 6:30 AM daily Telegram report.

Reports:
  • Process liveness (trader, fill monitor, dashboard)
  • Pending fills  (entry orders placed, waiting to fill)
  • Active positions (filled entries, TPs/SL still open)

── Cron setup on DigitalOcean server ─────────────────────────────────────────
  crontab -e   then add these two lines:

  TZ=America/New_York
  30 6 * * * /root/Binance-mcp/venv/bin/python /root/Binance-mcp/morning_check.py >> /root/Binance-mcp/logs/morning_check.log 2>&1

  mkdir -p /root/Binance-mcp/logs   # create log dir first if needed
──────────────────────────────────────────────────────────────────────────────
"""
import os
import json
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
import requests

load_dotenv()
TRADES_LOG = Path(__file__).parent / "trades.json"


def send_telegram(msg: str):
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("(no Telegram credentials)\n" + msg)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"Telegram failed: {e}")


def is_running(script: str) -> bool:
    result = subprocess.run(
        ["pgrep", "-f", f"python.*{script}"],
        capture_output=True,
    )
    return result.returncode == 0


def load_trades() -> list[dict]:
    if not TRADES_LOG.exists():
        return []
    return json.loads(TRADES_LOG.read_text())


def categorize_trades(trades: list[dict]) -> tuple[list, list]:
    pending = []  # limit orders placed, nothing filled yet
    active  = []  # at least one entry filled, position open

    cutoff = datetime.now(timezone.utc) - timedelta(days=14)

    for trade in trades:
        # Skip very old trades
        ts_raw = trade.get("received_at") or trade.get("timestamp", "")
        if ts_raw:
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                if ts < cutoff:
                    continue
            except ValueError:
                pass

        signal  = trade["signal"]
        result  = trade.get("result", {})
        orders  = result.get("placed_orders", [])
        symbol  = signal["symbol"]
        direction = signal.get("direction", "LONG")

        if not orders:
            continue

        skip_statuses = {"CANCELED", "CANCELLED_AFTER_TP"}
        live_orders = [
            o for o in orders
            if o.get("binance_status") not in skip_statuses
            and str(o.get("orderId", "")) not in ("", "DRY_RUN")
        ]

        if not live_orders:
            continue

        filled   = [o for o in live_orders if o.get("filled_qty")]
        unfilled = [o for o in live_orders if not o.get("filled_qty") and not o.get("tp_sl_placed")]

        if filled:
            avg_prices = [o["avg_fill_price"] for o in filled if o.get("avg_fill_price")]
            avg_entry  = sum(avg_prices) / len(avg_prices) if avg_prices else None
            tp_hit     = trade.get("entries_cancelled", False)
            active.append({
                "symbol":    symbol,
                "direction": direction,
                "avg_entry": avg_entry,
                "n_filled":  len(filled),
                "tp_hit":    tp_hit,
            })
        elif unfilled:
            prices = sorted(set(o["price"] for o in unfilled))
            pending.append({
                "symbol":    symbol,
                "direction": direction,
                "prices":    prices,
                "n":         len(unfilled),
            })

    return pending, active


def build_message(pending: list, active: list) -> str:
    trader_ok = is_running("trader.py")
    fill_ok   = is_running("fill_monitor.py")
    dash_ok   = is_running("dashboard.py")

    t = "✅" if trader_ok else "❌"
    f = "✅" if fill_ok   else "❌"
    d = "✅" if dash_ok   else "❌"

    lines = [
        "🌅 *TradingLuna* — 6:30 AM",
        "",
        f"{t} trader  {f} fill monitor  {d} dashboard",
    ]

    if not pending and not active:
        lines.append("No pending or active trades.")
    else:
        if pending:
            lines.append("")
            lines.append(f"⏳ *Pending fills ({len(pending)}):*")
            for p in pending:
                prices_str = " / ".join(f"${x:,.2f}" for x in p["prices"])
                lines.append(f"  {p['symbol']} {p['direction']} — {p['n']} orders @ {prices_str}")

        if active:
            lines.append("")
            lines.append(f"📈 *Active positions ({len(active)}):*")
            for a in active:
                entry_str = f"${a['avg_entry']:,.4f}" if a["avg_entry"] else "?"
                note      = "  TP hit, runner open" if a["tp_hit"] else ""
                lines.append(f"  {a['symbol']} {a['direction']} @ {entry_str}{note}")

    if not trader_ok or not fill_ok:
        lines.append("")
        lines.append("⚠️ *Process down — check the server!*")

    return "\n".join(lines)


def main():
    trades          = load_trades()
    pending, active = categorize_trades(trades)
    msg             = build_message(pending, active)
    print(msg)
    send_telegram(msg)


if __name__ == "__main__":
    main()
