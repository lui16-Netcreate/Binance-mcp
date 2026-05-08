"""
Watchdog — monitors all TradingLuna processes and alerts via Telegram.

Runs every 5 minutes via cron. Uses a state file to avoid spamming —
alerts once when a process goes down, once when it recovers.

── Cron setup ────────────────────────────────────────────────────────────────
  crontab -e   (add below the existing TZ and morning check lines)

  */5 * * * * /root/Binance-mcp/venv/bin/python /root/Binance-mcp/watchdog.py >> /root/Binance-mcp/logs/watchdog.log 2>&1
──────────────────────────────────────────────────────────────────────────────
"""
import os
import json
import subprocess
import logging
from pathlib import Path
from dotenv import load_dotenv
import requests

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

STATE_FILE = Path(__file__).parent / "watchdog_state.json"

PROCESSES = {
    "trader":       "trader.py",
    "fill_monitor": "fill_monitor.py",
    "dashboard":    "dashboard.py",
    "bot":          "bot.py",
}


def send_telegram(msg: str):
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        logging.warning(f"Telegram failed: {e}")


def is_running(script: str) -> bool:
    result = subprocess.run(
        ["pgrep", "-f", f"python.*{script}"],
        capture_output=True,
    )
    return result.returncode == 0


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {name: True for name in PROCESSES}  # assume all up on first run
    return json.loads(STATE_FILE.read_text())


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state))


def main():
    state = load_state()

    for name, script in PROCESSES.items():
        currently_up = is_running(script)
        was_up       = state.get(name, True)

        if was_up and not currently_up:
            logging.warning(f"DOWN: {name}")
            send_telegram(f"🔴 *{name} is DOWN*\nTradingLuna lost a process — check the server.")

        elif not was_up and currently_up:
            logging.info(f"RECOVERED: {name}")
            send_telegram(f"🟢 *{name} recovered*\nProcess is back online.")

        state[name] = currently_up

    save_state(state)


if __name__ == "__main__":
    main()
