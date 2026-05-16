"""
Phase 3 — Telegram Signal Auto-Trader

Listens to the private signal group via Telethon userbot, parses signals,
and places limit buy orders on Binance US (spot).

NOTE: Binance US is spot-only — no leverage, no futures.
  LONG  → 3 limit BUY orders at top/mid/bottom of entry range
  SHORT → logged and alerted, not executed (can't short on spot)
  SL/TP auto-management is Phase 3.5

Usage:
    python trader.py            # live trading
    python trader.py --dry-run  # parse + log only, no orders placed
"""
import os
import re
import sys
import asyncio
import logging
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from dotenv import load_dotenv
import requests
from telethon import TelegramClient, events
from binance.client import Client as BinanceClient
from binance.exceptions import BinanceAPIException
import binance_data

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

DRY_RUN    = "--dry-run" in sys.argv
TRADES_LOG = Path(__file__).parent / "trades.json"


# ── Telegram helper ───────────────────────────────────────────────────────────

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


# ── Signal parser ─────────────────────────────────────────────────────────────
# Handles variations like:
#   $ETH - I like 2,352 - 2,328 for a LIMIT LONG.
#   $HIGH - I am trying a LONG here 0.2082 - 0.2060
#   TP1: 2,376 / TP2: 2,399.50 / TP3: 2,423 / TP4: 2,446
#   SL: 2,248 or manual

_P         = r"[\d,]+\.?\d*"
_SYMBOL_RE = re.compile(r"\$([A-Z]+)", re.I)
_DIR_RE    = re.compile(r"\b(LONG|SHORT)\b", re.I)
_RANGE_RE  = re.compile(rf"({_P})\s*[-–]\s*({_P})")   # generic X - Y
_TP_RE     = re.compile(rf"TP(\d)\s*[:/]\s*({_P})", re.I)
_SL_RE     = re.compile(rf"SL\s*[:/]\s*({_P})", re.I)


def _p(s: str) -> float:
    return float(s.replace(",", ""))


def parse_signal(text: str) -> dict | None:
    sym  = _SYMBOL_RE.search(text)
    dir_ = _DIR_RE.search(text)
    sl   = _SL_RE.search(text)
    if not sym or not dir_ or not sl:
        return None

    symbol    = sym.group(1).upper() + "USDT"
    direction = dir_.group(1).upper()
    sl_price  = _p(sl.group(1))

    # Find entry range on the same line as the direction word (avoids TP/SL lines)
    dir_line = next(
        (line for line in text.splitlines() if _DIR_RE.search(line)),
        "",
    )
    range_m = _RANGE_RE.search(dir_line)

    if range_m:
        a, b    = _p(range_m.group(1)), _p(range_m.group(2))
        top     = max(a, b)
        bottom  = min(a, b)
        mid     = round((top + bottom) / 2, 8)
        entries = [top, mid, bottom]
    else:
        # Single-price signal: take the first number after the direction word
        after_dir = dir_line[dir_.end():]
        single = re.search(rf"({_P})", after_dir)
        if not single:
            return None
        entries = [_p(single.group(1))]

    tps = {f"tp{m.group(1)}": _p(m.group(2)) for m in _TP_RE.finditer(text)}

    return {
        "symbol":    symbol,
        "direction": direction,
        "entries":   entries,
        "sl":        sl_price,
        "tps":       tps,
        "raw":       text.strip(),
    }


# ── Exchange helpers ──────────────────────────────────────────────────────────

def _round_to(value: float, step: str) -> float:
    step_dec = Decimal(step)
    return float((Decimal(str(value)) // step_dec) * step_dec)


def _fmt(value) -> str:
    """Format a number for Binance API as plain decimal — avoids scientific notation."""
    return format(Decimal(str(value)), 'f')


def _get_filter(sym_info: dict, filter_type: str, field: str) -> str:
    for f in sym_info.get("filters", []):
        if f["filterType"] == filter_type:
            return f[field]
    return "0.00001"


def _get_min_notional(sym_info: dict) -> float:
    # Binance renamed MIN_NOTIONAL → NOTIONAL in newer API versions; check both
    for name in ("NOTIONAL", "MIN_NOTIONAL"):
        for f in sym_info.get("filters", []):
            if f["filterType"] == name:
                return float(f.get("minNotional", 10))
    return 10.0


def get_usdt_balance(client: BinanceClient) -> float:
    for asset in client.get_account()["balances"]:
        if asset["asset"] == "USDT":
            return float(asset["free"])
    return 0.0


# ── Order execution ───────────────────────────────────────────────────────────

def execute_signal(client: BinanceClient | None, signal: dict) -> dict:
    symbol    = signal["symbol"]
    direction = signal["direction"]
    entries   = signal["entries"]

    if direction == "SHORT":
        msg = "SHORT skipped — Binance US is spot-only (no shorting)"
        logging.warning(f"⚠️  {msg}")
        tps = signal.get("tps", {})
        tp_str = "  ".join(
            f"TP{i}: `${tps[f'tp{i}']:,.2f}`"
            for i in range(1, 5) if f"tp{i}" in tps
        )
        sl_str = f"`${signal['sl']:,.2f}`" if signal.get("sl") else "?"
        send_telegram(
            f"⏭️ *{symbol} SHORT skipped* (spot-only)\n"
            f"{tp_str}\n"
            f"SL: {sl_str}"
        )
        return {
            "symbol": symbol, "direction": direction,
            "placed_orders": [], "usdt_balance": 0,
            "errors": [msg],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    placed, errors = [], []
    usdt_balance = 0.0

    if DRY_RUN or client is None:
        usdt_balance = 10_000.0
        per_order    = usdt_balance * 0.01
        for entry in entries:
            qty = round(per_order / entry, 6)
            logging.info(f"[DRY-RUN] BUY limit  {qty} {symbol} @ {entry:,.4f}")
            placed.append({"price": entry, "qty": qty, "orderId": "DRY_RUN"})
    else:
        sym_info     = client.get_symbol_info(symbol)
        lot_step     = _get_filter(sym_info, "LOT_SIZE", "stepSize")
        min_qty      = float(_get_filter(sym_info, "LOT_SIZE", "minQty"))
        tick_size    = _get_filter(sym_info, "PRICE_FILTER", "tickSize")
        min_notional = _get_min_notional(sym_info)
        usdt_balance = get_usdt_balance(client)
        per_order    = float(os.getenv("ORDER_SIZE_USD", str(usdt_balance * 0.01)))
        logging.info(
            f"{symbol} filters — lot_step={lot_step}  min_qty={min_qty}  "
            f"tick_size={tick_size}  min_notional=${min_notional}  "
            f"per_order=${per_order:.2f}  balance=${usdt_balance:.2f}"
        )

        if per_order < min_notional:
            msg = (
                f"ORDER_SIZE_USD (${per_order}) is below Binance minimum "
                f"notional (${min_notional}). Increase ORDER_SIZE_USD in .env."
            )
            logging.error(f"⚠️  {msg}")
            errors.append(msg)
            send_telegram(f"⚠️ *{symbol} skipped*\n{msg}")
            return {
                "symbol": symbol, "direction": direction,
                "placed_orders": [], "usdt_balance": usdt_balance,
                "errors": errors,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        if usdt_balance < per_order:
            msg = f"Insufficient balance — need ${per_order} but have ${usdt_balance:.2f} USDT"
            logging.error(f"⚠️  {msg}")
            errors.append(msg)
            send_telegram(f"⚠️ *{symbol} skipped*\n{msg}")
            return {
                "symbol": symbol, "direction": direction,
                "placed_orders": [], "usdt_balance": usdt_balance,
                "errors": errors,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        for entry in entries:
            qty   = _round_to(per_order / entry, lot_step)
            price = _round_to(entry, tick_size)
            if qty <= 0 or qty < min_qty:
                errors.append(f"Qty {qty} below minQty {min_qty} at entry {entry}")
                continue
            try:
                order = client.order_limit_buy(
                    symbol=symbol,
                    quantity=_fmt(qty),
                    price=_fmt(price),
                    timeInForce="GTC",
                )
                placed.append({"price": price, "qty": qty, "orderId": order["orderId"]})
                logging.info(f"✅ BUY limit  {qty} {symbol} @ {price}  (id={order['orderId']})")
            except BinanceAPIException as e:
                logging.error(f"Order failed @ {entry}: {e.message}")
                errors.append(f"Entry {entry}: {e.message}")

    return {
        "symbol":        symbol,
        "direction":     direction,
        "placed_orders": placed,
        "usdt_balance":  usdt_balance,
        "errors":        errors,
        "timestamp":     datetime.now(timezone.utc).isoformat(),
    }


# ── Confluence snapshot ───────────────────────────────────────────────────────

def snapshot_confluence(symbol: str) -> dict:
    snapshot = {"symbol": symbol, "interval": "4h", "timestamp": datetime.now(timezone.utc).isoformat()}
    try:
        candles = binance_data.get_candles(symbol, "4h", 250)  # 250 needed for EMA200
        snapshot["indicators"] = binance_data.compute_indicators(candles)
        snapshot["fibonacci"]  = binance_data.compute_fibonacci(candles)
    except Exception as e:
        snapshot["indicators_error"] = str(e)
    try:
        stats = binance_data.get_24hr_stats(symbol)
        snapshot["volume_24h_usdt"] = round(float(stats["quoteVolume"]), 2)
    except Exception as e:
        snapshot["volume_24h_error"] = str(e)
    try:
        snapshot["sentiment"] = binance_data.get_full_sentiment(symbol)
    except Exception as e:
        snapshot["sentiment_error"] = str(e)
    return snapshot


# ── Trade log ─────────────────────────────────────────────────────────────────

def log_trade(signal: dict, result: dict, confluence: dict = None, source: str = "telegram", notes: str = None):
    trades = json.loads(TRADES_LOG.read_text()) if TRADES_LOG.exists() else []
    entry = {"signal": signal, "result": result, "source": source}
    if confluence:
        entry["confluence"] = confluence
    if notes:
        entry["notes"] = notes
    trades.append(entry)
    TRADES_LOG.write_text(json.dumps(trades, indent=2))


# ── Confirmation message ──────────────────────────────────────────────────────

def build_confirmation(signal: dict, result: dict) -> str:
    sym    = signal["symbol"]
    dir_   = signal["direction"]
    tps    = signal["tps"]
    sl     = signal["sl"]
    placed = result["placed_orders"]
    errors = result.get("errors", [])

    prefix = "🔵 *[DRY RUN]* " if DRY_RUN else "✅ "
    msg    = f"{prefix}*{sym} {dir_}*\n\n"

    if placed:
        entries_str = " / ".join(f"`${o['price']:,.4f}`" for o in placed)
        msg += f"📥 Entries: {entries_str}\n"

    if tps:
        tp_str = "  " + "\n  ".join(
            f"TP{k[-1]}: `${v:,.2f}`" for k, v in sorted(tps.items())
        )
        msg += f"🎯 TPs:\n{tp_str}\n"
    elif signal.get("tp_mode") == "auto_pct":
        pcts   = signal.get("tp_pcts",   [0.05, 0.10])
        splits = signal.get("tp_splits", [0.50, 0.50])
        tp_lines = "\n  ".join(
            f"TP{i+1}: `+{p*100:.0f}%` — close `{s*100:.0f}%` of position"
            for i, (p, s) in enumerate(zip(pcts, splits))
        )
        msg += f"🎯 TPs (auto at fill):\n  {tp_lines}\n"

    msg += f"🛑 SL: `${sl:,.2f}`\n"
    order_size = float(os.getenv("ORDER_SIZE_USD", str(result['usdt_balance'] * 0.01)))
    total_used = order_size * len(placed)
    msg += f"💼 Total deployed: `${total_used:,.2f}` (`${order_size:.0f}` × {len(placed)} orders)\n"

    if errors:
        msg += "\n⚠️ " + "\n⚠️ ".join(errors)

    return msg


# ── Main listener ─────────────────────────────────────────────────────────────

async def main():
    api_id   = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    phone    = os.getenv("TELEGRAM_PHONE", "")
    group    = os.getenv("SIGNAL_GROUP_NAME", "Crypto Chiefs - Premium")
    b_key    = os.getenv("BINANCE_API_KEY", "")
    b_secret = os.getenv("BINANCE_API_SECRET", "")

    if not api_id or not api_hash:
        logging.error("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env")
        return

    binance_client = None
    if not DRY_RUN:
        if not b_key or not b_secret:
            logging.error("Set BINANCE_API_KEY and BINANCE_API_SECRET in .env")
            return
        binance_client = BinanceClient(b_key, b_secret, tld="us", requests_params={"timeout": 15})
        try:
            binance_client.ping()
            logging.info("Binance US connected ✓")
        except Exception as e:
            logging.error(f"Binance connection failed: {e}")
            return

    session = str(Path(__file__).parent / "trader_session")
    tg = TelegramClient(session, api_id, api_hash)
    await tg.start(phone=phone)

    me = await tg.get_me()
    logging.info(f"Telegram connected as @{me.username or me.first_name} ✓")
    logging.info(f"Listening to: '{group}'")
    logging.info(f"Mode: {'DRY RUN' if DRY_RUN else '🔴 LIVE'}")

    mode = "DRY RUN" if DRY_RUN else "🔴 LIVE"
    send_telegram(f"🤖 Trader started — *{mode}*\nListening: _{group}_")

    @tg.on(events.NewMessage)
    async def on_message(event):
        chat  = await event.get_chat()
        title = getattr(chat, "title", "") or ""
        if group.lower() not in title.lower():
            return

        logging.info(f"📩 Message from '{title}':\n{event.raw_text[:300]}")

        signal = parse_signal(event.raw_text)
        if not signal:
            logging.warning("⚠️  Message not parsed as signal — skipped")
            return

        logging.info(
            f"📨 Signal: {signal['symbol']} {signal['direction']}  "
            f"entries={signal['entries']}  sl={signal['sl']}"
        )

        confluence = await asyncio.to_thread(snapshot_confluence, signal["symbol"])
        rsi = confluence.get("indicators", {}).get("rsi_14", "?")
        fg  = confluence.get("sentiment", {}).get("fear_greed", {}).get("value", "?")
        logging.info(f"📊 Confluence: RSI={rsi}  Fear&Greed={fg}")

        result = execute_signal(binance_client, signal)
        log_trade(signal, result, confluence)
        send_telegram(build_confirmation(signal, result))

    HEARTBEAT = Path(__file__).parent / "trader.heartbeat"

    async def write_heartbeat():
        while True:
            HEARTBEAT.write_text(str(datetime.now(timezone.utc).timestamp()))
            await asyncio.sleep(60)

    asyncio.ensure_future(write_heartbeat())
    await tg.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
