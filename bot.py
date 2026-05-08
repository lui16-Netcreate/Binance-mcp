"""
Telegram command bot — on-demand status and reports.

Commands:
  /status  — health check + active trades (same as morning report)
  /report  — weekly P&L summary via Claude analysis

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
    from morning_check import load_trades, categorize_trades, build_message
    trades          = load_trades()
    pending, active = categorize_trades(trades)
    msg             = build_message(pending, active)
    msg             = msg.replace("🌅 *TradingLuna* — 6:30 AM", "📡 *TradingLuna Status*")
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


def main():
    if not TOKEN or not CHAT_ID:
        logging.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing in .env")
        return

    logging.info("Bot started — listening for commands")
    send("🤖 *TradingLuna Bot* online\n\n/status — health check\n/report — weekly summary\n/report30 — last 30 days")

    offset = 0
    while True:
        updates = get_updates(offset)
        for update in updates:
            offset = update["update_id"] + 1
            msg     = update.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text    = msg.get("text", "").strip().lower()

            if chat_id != CHAT_ID:
                continue

            logging.info(f"Command: {text}")

            if text == "/status":
                handle_status()
            elif text == "/report30":
                handle_report(days=30)
            elif text.startswith("/report"):
                handle_report(days=7)
            else:
                send("Commands: /status · /report · /report30")

        time.sleep(1)


if __name__ == "__main__":
    main()
