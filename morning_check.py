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


def process_count(script: str) -> int:
    # Match only the venv Python binary — avoids double-counting the bash
    # wrapper screen uses (its cmdline also contains "python <script>").
    result = subprocess.run(
        ["pgrep", "-fc", f"venv/bin/python.*{script}"],
        capture_output=True, text=True,
    )
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def is_running(script: str) -> bool:
    return process_count(script) > 0


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

        if filled and not trade.get("trade_closed"):
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


def recent_pnl_line(trades: list[dict], days: int = 7) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    closed = [
        t for t in trades
        if t.get("trade_closed") and t.get("total_pnl_usdt") is not None
        and datetime.fromisoformat(t["closed_at"]) > cutoff
    ]
    if not closed:
        return ""
    pnls  = [t["total_pnl_usdt"] for t in closed]
    wins  = sum(1 for p in pnls if p > 0)
    total = sum(pnls)
    sign  = "+" if total >= 0 else ""
    return f"\n💰 *Last {days}d P&L:* `{sign}{total:.2f} USDT` — {wins}/{len(closed)} wins"


def build_message(pending: list, active: list) -> str:
    trader_n = process_count("trader.py")
    fill_n   = process_count("fill_monitor.py")
    dash_n   = process_count("dashboard.py")

    def status(n: int) -> str:
        if n == 0:   return "❌"
        if n == 1:   return "✅"
        return f"⚠️x{n}"

    lines = [
        "🌅 *TradingLuna* — 6:30 AM",
        "",
        f"{status(trader_n)} trader  {status(fill_n)} fill monitor  {status(dash_n)} dashboard",
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

    warnings = []
    if trader_n == 0 or fill_n == 0:
        warnings.append("Process down — check the server!")
    if trader_n > 1:
        warnings.append(f"Duplicate trader sessions ({trader_n}) — kill extras!")
    if fill_n > 1:
        warnings.append(f"Duplicate fill_monitor sessions ({fill_n}) — kill extras!")

    if warnings:
        lines.append("")
        for w in warnings:
            lines.append(f"⚠️ *{w}*")

    return "\n".join(lines)


def main():
    trades          = load_trades()
    pending, active = categorize_trades(trades)
    msg             = build_message(pending, active)
    msg            += recent_pnl_line(trades)
    print(msg)
    send_telegram(msg)


if __name__ == "__main__":
    main()
