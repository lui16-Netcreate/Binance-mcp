"""
Phase 3.5 — Signal History Reader

Reads recent messages from the signal group via Telethon and parses
any signals found. No orders are placed — read-only review tool.

Usage:
    python history.py              # last 50 messages
    python history.py --limit 100  # last 100 messages
    python history.py --days 3     # messages from last 3 days
"""
import os
import sys
import asyncio
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import Message
from trader import parse_signal

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")


async def main():
    parser = argparse.ArgumentParser(description="Read signal history from Telegram group")
    parser.add_argument("--limit", type=int, default=50, help="Max messages to scan (default: 50)")
    parser.add_argument("--days",  type=int, default=None, help="Only show messages from last N days")
    args = parser.parse_args()

    api_id   = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    phone    = os.getenv("TELEGRAM_PHONE", "")
    group    = os.getenv("SIGNAL_GROUP_NAME", "Crypto Chiefs - Premium")

    if not api_id or not api_hash:
        print("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env")
        return

    session = str(Path(__file__).parent / "trader_session")
    client  = TelegramClient(session, api_id, api_hash)
    await client.start(phone=phone)

    print(f"Connected. Scanning last {args.limit} messages in '{group}'...\n")

    # Find the target group
    target = None
    async for dialog in client.iter_dialogs():
        if group.lower() in dialog.title.lower():
            target = dialog.entity
            break

    if not target:
        print(f"Group '{group}' not found. Make sure SIGNAL_GROUP_NAME matches exactly.")
        await client.disconnect()
        return

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=args.days)
        if args.days else None
    )

    signals_found = []
    messages_scanned = 0

    async for msg in client.iter_messages(target, limit=args.limit):
        if not isinstance(msg, Message) or not msg.text:
            continue

        if cutoff and msg.date.replace(tzinfo=timezone.utc) < cutoff:
            break

        messages_scanned += 1
        signal = parse_signal(msg.text)

        if signal:
            signals_found.append((msg.date, signal, msg.text))

    print(f"Scanned {messages_scanned} messages — found {len(signals_found)} signal(s).\n")
    print("=" * 60)

    if not signals_found:
        print("No signals found in this range.")
    else:
        for date, signal, raw in reversed(signals_found):
            print(f"📅 {date.strftime('%Y-%m-%d %H:%M UTC')}")
            print(f"📨 {signal['symbol']} {signal['direction']}")
            print(f"   Entries : {signal['entries']}")
            print(f"   TPs     : {signal['tps']}")
            print(f"   SL      : {signal['sl']}")
            print(f"   Raw     : {raw[:120].strip()}{'...' if len(raw) > 120 else ''}")
            print()

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
