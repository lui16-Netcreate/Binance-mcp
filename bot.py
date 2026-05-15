"""
Telegram command bot — on-demand status, reports, and manual signals.

Commands:
  /status      — health check + active trades
  /report      — weekly P&L summary via Claude analysis
  /report30    — last 30 days
  /pnl         — side-by-side P&L: your signals vs Telegram signals
  /signal ...  — submit your own trade signal for execution

  /signal format (same as the Telegram signal group):
    /signal $BTC LONG 95000 - 94000
    TP1: 96500 TP2: 97000 TP3: 97500
    SL: 93500

Run in its own screen session on the server:
  screen -S bot
  cd /root/Binance-mcp && source venv/bin/activate && python bot.py
  Ctrl+A, D to detach
"""
import os
import time
import logging
import requests
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID", ""))
BASE    = f"https://api.telegram.org/bot{TOKEN}"


def send(msg: str):
    try:
        requests.post(
            f"{BASE}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        logging.warning(f"Telegram send failed: {e}")


def get_updates(offset: int) -> list:
    try:
        r = requests.get(
            f"{BASE}/getUpdates",
            params={"timeout": 30, "offset": offset},
            timeout=35,
        )
        return r.json().get("result", [])
    except Exception as e:
        logging.warning(f"getUpdates failed: {e}")
        return []


def handle_status():
    from morning_check import load_trades, categorize_trades, build_message, recent_pnl_line
    trades          = load_trades()
    pending, active = categorize_trades(trades)
    msg             = build_message(pending, active)
    msg             = msg.replace("🌅 *TradingLuna* — 6:30 AM", "📡 *TradingLuna Status*")
    msg            += recent_pnl_line(trades)
    send(msg)


def handle_report(days: int = 7):
    from analyzer import load_trades, analyze
    from report import build_report
    trades = load_trades(days=days)
    if not trades:
        send(f"📊 *Report* — No trades in the last {days} days.")
        return
    send("⏳ Generating report...")
    analysis = analyze(trades)
    send(build_report(trades, days, analysis))


def handle_pnl():
    """Side-by-side P&L breakdown: Telegram signals vs your manual signals."""
    from analyzer import load_trades
    trades = load_trades()

    def _stats(source_filter):
        closed = [
            t for t in trades
            if t.get("trade_closed") and t.get("total_pnl_usdt") is not None
            and t.get("source", "telegram") == source_filter
        ]
        if not closed:
            return None
        pnls  = [t["total_pnl_usdt"] for t in closed]
        wins  = sum(1 for p in pnls if p > 0)
        total = sum(pnls)
        sign  = "+" if total >= 0 else ""
        return (
            f"`{len(closed)}` trades · `{sign}{total:.2f} USDT` · "
            f"`{wins}/{len(closed)}` wins (`{wins/len(closed)*100:.0f}%`)"
        )

    tg_line     = _stats("telegram") or "No closed trades yet"
    manual_line = _stats("manual")   or "No closed trades yet"

    send(
        "📊 *P&L by Source*\n\n"
        f"🤖 *Telegram signals:*\n  {tg_line}\n\n"
        f"✍️ *Your signals:*\n  {manual_line}"
    )


def handle_signal(raw_text: str, binance_client):
    """Parse and execute a manual signal, tagged source='manual'."""
    from trader import parse_signal, execute_signal, log_trade, snapshot_confluence, build_confirmation

    signal = parse_signal(raw_text)
    if not signal:
        send(
            "❌ Could not parse signal. Use this format:\n\n"
            "`$BTC LONG 95000 - 94000`\n"
            "`TP1: 96500 TP2: 97000 TP3: 97500`\n"
            "`SL: 93500`"
        )
        return

    send(
        f"📨 *Parsed signal:* {signal['symbol']} {signal['direction']}\n"
        f"Entries: {signal['entries']}\n"
        f"TPs: {signal['tps']}\n"
        f"SL: {signal['sl']}\n\n"
        "⏳ Executing..."
    )

    confluence = snapshot_confluence(signal["symbol"])
    result     = execute_signal(binance_client, signal)
    log_trade(signal, result, confluence, source="manual")
    send(build_confirmation(signal, result))


HELP = (
    "Commands:\n"
    "/status — health check + positions\n"
    "/report — last 7 days (Claude analysis)\n"
    "/report30 — last 30 days\n"
    "/pnl — your signals vs Telegram signals\n"
    "/signal — submit your own trade\n\n"
    "Signal format:\n"
    "`/signal $BTC LONG 95000 - 94000`\n"
    "`TP1: 96500 TP2: 97000`\n"
    "`SL: 93500`"
)


def main():
    if not TOKEN or not CHAT_ID:
        logging.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing in .env")
        return

    # Set up Binance client for manual signal execution
    binance_client = None
    b_key    = os.getenv("BINANCE_API_KEY", "")
    b_secret = os.getenv("BINANCE_API_SECRET", "")
    if b_key and b_secret:
        try:
            from binance.client import Client as BinanceClient
            binance_client = BinanceClient(b_key, b_secret, tld="us")
            binance_client.ping()
            logging.info("Binance US connected ✓")
        except Exception as e:
            logging.warning(f"Binance connection failed — /signal will run in dry-run: {e}")
    else:
        logging.warning("No Binance API keys — /signal will run in dry-run mode")

    logging.info("Bot started — listening for commands")
    send(f"🤖 *TradingLuna Bot* online\n\n{HELP}")

    offset = 0
    while True:
        updates = get_updates(offset)
        for update in updates:
            offset  = update["update_id"] + 1
            msg     = update.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text    = msg.get("text", "").strip()

            if chat_id != CHAT_ID:
                continue

            logging.info(f"Command: {text[:80]}")
            lower = text.lower()

            if lower == "/status":
                handle_status()
            elif lower == "/pnl":
                handle_pnl()
            elif lower == "/report30":
                handle_report(days=30)
            elif lower.startswith("/report"):
                handle_report(days=7)
            elif lower.startswith("/signal"):
                # Everything after /signal (supports multiline messages)
                signal_text = text[len("/signal"):].strip()
                if not signal_text:
                    send(f"Usage:\n{HELP}")
                else:
                    handle_signal(signal_text, binance_client)
            else:
                send(HELP)

        time.sleep(1)


if __name__ == "__main__":
    main()
