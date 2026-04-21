"""
Confluence alert monitor.
Checks every 15 minutes for: RSI(14) <= 30 AND price in Fibonacci golden zone (0.618-0.786).
Sends a Windows desktop notification when both conditions are met.

Usage:
    python monitor.py                        # monitor BTCUSDT 4h (default)
    python monitor.py ETHUSDT 4h             # custom symbol and interval
    python monitor.py BTCUSDT 1h 10          # custom check interval (minutes)
"""
import os
import sys
import time
import logging
import requests
from dotenv import load_dotenv
import binance

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

SYMBOL   = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
INTERVAL = sys.argv[2] if len(sys.argv) > 2 else "4h"
CHECK_EVERY_MINUTES = int(sys.argv[3]) if len(sys.argv) > 3 else 15


def send_telegram(message: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logging.warning("Telegram credentials not set in .env")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        logging.warning(f"Telegram notification failed: {e}")


def send_notification(title: str, message: str):
    try:
        from plyer import notification
        notification.notify(
            title=title,
            message=message,
            app_name="Binance MCP Monitor",
            timeout=30,
        )
    except Exception as e:
        logging.warning(f"Desktop notification failed: {e}")


def check_confluence(symbol: str, interval: str) -> dict | None:
    candles   = binance.get_candles(symbol, interval, limit=100)
    indicators = binance.compute_indicators(candles)
    fib        = binance.compute_fibonacci(candles)

    price  = indicators["current_price"]
    rsi    = indicators["rsi_14"]
    bottom = fib["fib_0618"]   # 61.8% retracement — golden zone lower bound
    top    = fib["fib_0786"]   # 78.6% retracement — golden zone upper bound

    in_golden_zone = bottom >= price >= top
    rsi_oversold   = rsi is not None and rsi <= 30

    return {
        "symbol": symbol,
        "interval": interval,
        "price": price,
        "rsi": rsi,
        "fib_0618": bottom,
        "fib_0786": top,
        "in_golden_zone": in_golden_zone,
        "rsi_oversold": rsi_oversold,
        "confluence": in_golden_zone and rsi_oversold,
    }


def run():
    logging.info(f"Monitoring {SYMBOL} on {INTERVAL} chart — checking every {CHECK_EVERY_MINUTES} min")
    logging.info("Conditions: RSI(14) ≤ 30  AND  price in Fibonacci golden zone (0.618–0.786)")
    logging.info("Press Ctrl+C to stop.\n")

    while True:
        try:
            result = check_confluence(SYMBOL, INTERVAL)
            status = (
                f"{result['symbol']} | Price: {result['price']:,.2f} | "
                f"RSI: {result['rsi']} | "
                f"Golden zone: {result['fib_0786']:,.2f} – {result['fib_0618']:,.2f} | "
                f"In zone: {result['in_golden_zone']} | RSI oversold: {result['rsi_oversold']}"
            )
            logging.info(status)

            if result["confluence"]:
                msg = (
                    f"Price: ${result['price']:,.2f}\n"
                    f"RSI: {result['rsi']}\n"
                    f"Golden zone: ${result['fib_0786']:,.2f} – ${result['fib_0618']:,.2f}"
                )
                telegram_msg = (
                    f"🚨 *Confluence Alert — {result['symbol']} {result['interval']}*\n\n"
                    f"💰 Price: `${result['price']:,.2f}`\n"
                    f"📉 RSI(14): `{result['rsi']}` ← oversold\n"
                    f"📊 Fib Golden Zone: `${result['fib_0786']:,.2f} – ${result['fib_0618']:,.2f}`\n\n"
                    f"⚡ RSI + Fibonacci confluence detected — potential reversal zone."
                )
                logging.info(f"🚨 CONFLUENCE ALERT: {SYMBOL} {INTERVAL}")
                send_notification(
                    title=f"🚨 Confluence Alert — {result['symbol']} {result['interval']}",
                    message=msg,
                )
                send_telegram(telegram_msg)

        except Exception as e:
            logging.error(f"Check failed: {e}")

        time.sleep(CHECK_EVERY_MINUTES * 60)


if __name__ == "__main__":
    run()
