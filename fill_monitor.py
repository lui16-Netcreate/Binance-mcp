"""
Phase 3.5 — Fill Monitor

Polls Binance US every 60 seconds for filled entry orders.
When a buy order fills, automatically places:
  - 4 limit SELL orders at TP1/2/3/4 (40 / 30 / 20 / 10% split)
  - 1 STOP_LOSS_LIMIT sell at SL price

Updates trades.json with fill status and TP/SL order IDs.
Sends Telegram notification on each fill.

Usage:
    python fill_monitor.py            # live
    python fill_monitor.py --dry-run  # simulate, no orders placed
"""
import os
import sys
import json
import time
import logging
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
import requests
from binance.client import Client as BinanceClient
from binance.exceptions import BinanceAPIException

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

DRY_RUN       = "--dry-run" in sys.argv
TRADES_LOG    = Path(__file__).parent / "trades.json"
POLL_INTERVAL = 60   # seconds between polls
SL_SLIP       = 0.003  # 0.3% below SL price for the limit leg (ensures fill)
TP_SPLIT      = [0.40, 0.30, 0.20, 0.10]


# ── Telegram ──────────────────────────────────────────────────────────────────

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _round_to(value: float, step: str) -> float:
    return float(Decimal(str(value)).quantize(Decimal(step), rounding=ROUND_DOWN))


def _get_filter(sym_info: dict, filter_type: str, field: str) -> str:
    for f in sym_info.get("filters", []):
        if f["filterType"] == filter_type:
            return f[field]
    return "0.00001"


# ── Trade log ─────────────────────────────────────────────────────────────────

def load_trades() -> list[dict]:
    if not TRADES_LOG.exists():
        return []
    return json.loads(TRADES_LOG.read_text())


def save_trades(trades: list[dict]):
    TRADES_LOG.write_text(json.dumps(trades, indent=2))


# ── TP / SL order placement ───────────────────────────────────────────────────

def place_tp_sl(
    client: BinanceClient,
    symbol: str,
    filled_qty: float,
    tps: dict,
    sl: float,
    sym_info: dict,
) -> dict:
    lot_step  = _get_filter(sym_info, "LOT_SIZE", "stepSize")
    tick_size = _get_filter(sym_info, "PRICE_FILTER", "tickSize")

    tp_prices = [tps.get(f"tp{i}") for i in range(1, 5)]
    tp_prices = [p for p in tp_prices if p]  # drop missing TPs

    # Distribute qty across TPs; last TP gets remainder to avoid rounding loss
    tp_qtys = []
    remaining = filled_qty
    for idx, split in enumerate(TP_SPLIT[:len(tp_prices)]):
        if idx == len(tp_prices) - 1:
            qty = _round_to(remaining, lot_step)
        else:
            qty = _round_to(filled_qty * split, lot_step)
            remaining -= qty
        tp_qtys.append(qty)

    tp_orders = []
    for tp_price, qty in zip(tp_prices, tp_qtys):
        price = _round_to(tp_price, tick_size)
        if qty <= 0:
            continue
        if DRY_RUN:
            logging.info(f"[DRY-RUN] SELL limit  {qty} {symbol} @ {price}  (TP)")
            tp_orders.append({"price": price, "qty": qty, "orderId": "DRY_RUN", "type": "TP"})
            continue
        try:
            order = client.order_limit_sell(
                symbol=symbol,
                quantity=qty,
                price=str(price),
                timeInForce="GTC",
            )
            tp_orders.append({"price": price, "qty": qty, "orderId": order["orderId"], "type": "TP"})
            logging.info(f"🎯 TP sell  {qty} {symbol} @ {price}  (id={order['orderId']})")
        except BinanceAPIException as e:
            logging.error(f"TP order failed @ {tp_price}: {e.message}")
            tp_orders.append({"price": price, "qty": qty, "orderId": None, "error": e.message})

    # SL stop-limit sell — limit leg slightly below stop to ensure fill
    sl_stop  = _round_to(sl, tick_size)
    sl_limit = _round_to(sl * (1 - SL_SLIP), tick_size)
    sl_qty   = _round_to(filled_qty, lot_step)
    sl_order = {}

    if sl_qty > 0:
        if DRY_RUN:
            logging.info(f"[DRY-RUN] STOP_LOSS_LIMIT  {sl_qty} {symbol} stop={sl_stop} limit={sl_limit}")
            sl_order = {"stopPrice": sl_stop, "price": sl_limit, "qty": sl_qty, "orderId": "DRY_RUN"}
        else:
            try:
                order = client.create_order(
                    symbol=symbol,
                    side="SELL",
                    type="STOP_LOSS_LIMIT",
                    quantity=sl_qty,
                    price=str(sl_limit),
                    stopPrice=str(sl_stop),
                    timeInForce="GTC",
                )
                sl_order = {
                    "stopPrice": sl_stop, "price": sl_limit,
                    "qty": sl_qty, "orderId": order["orderId"],
                }
                logging.info(f"🛡️  SL placed  {sl_qty} {symbol} stop={sl_stop} limit={sl_limit}  (id={order['orderId']})")
            except BinanceAPIException as e:
                logging.error(f"SL order failed: {e.message}")
                sl_order = {"stopPrice": sl_stop, "price": sl_limit, "qty": sl_qty, "orderId": None, "error": e.message}

    return {"tp_orders": tp_orders, "sl_order": sl_order}


# ── Confirmation message ──────────────────────────────────────────────────────

def build_fill_notification(signal: dict, order: dict, tp_sl: dict) -> str:
    sym    = signal["symbol"]
    prefix = "🔵 *[DRY RUN]* " if DRY_RUN else "✅ "
    msg    = f"{prefix}*{sym} entry filled!*\n\n"
    msg   += f"📥 Filled: `{order.get('filled_qty', order['qty'])}` @ `${order.get('avg_fill_price', order['price']):,.4f}`\n\n"

    if tp_sl.get("tp_orders"):
        tp_lines = "\n".join(
            f"  TP{i+1}: `${o['price']:,.4f}` × `{o['qty']}`"
            for i, o in enumerate(tp_sl["tp_orders"])
        )
        msg += f"🎯 TP sells placed:\n{tp_lines}\n\n"

    sl = tp_sl.get("sl_order", {})
    if sl:
        msg += f"🛑 SL placed: stop=`${sl['stopPrice']:,.4f}` limit=`${sl['price']:,.4f}`\n"

    return msg


# ── Main poll loop ────────────────────────────────────────────────────────────

def check_fills(client: BinanceClient | None):
    trades  = load_trades()
    changed = False

    for trade in trades:
        signal = trade["signal"]
        result = trade["result"]
        symbol = signal["symbol"]

        for order in result.get("placed_orders", []):
            if order.get("tp_sl_placed"):
                continue
            if str(order.get("orderId")) == "DRY_RUN":
                continue

            try:
                if DRY_RUN or client is None:
                    # In dry-run, simulate a fill for any pending order
                    status_data = {"status": "FILLED", "executedQty": str(order["qty"]),
                                   "cummulativeQuoteQty": str(order["qty"] * order["price"])}
                else:
                    status_data = client.get_order(symbol=symbol, orderId=order["orderId"])
            except BinanceAPIException as e:
                logging.warning(f"Could not query order {order['orderId']}: {e.message}")
                continue

            order["binance_status"] = status_data["status"]

            if status_data["status"] == "CANCELED":
                order["tp_sl_placed"] = True  # skip — entry was canceled
                logging.info(f"Order {order['orderId']} was CANCELED — skipping TP/SL")
                changed = True
                continue

            if status_data["status"] != "FILLED":
                continue

            # Entry filled — place TP and SL
            filled_qty = float(status_data["executedQty"])
            quote_qty  = float(status_data["cummulativeQuoteQty"])
            avg_price  = quote_qty / filled_qty if filled_qty > 0 else order["price"]

            order["filled_qty"]      = filled_qty
            order["avg_fill_price"]  = round(avg_price, 8)
            order["filled_at"]       = datetime.now(timezone.utc).isoformat()

            logging.info(f"✅ Fill detected: {filled_qty} {symbol} @ {avg_price:.4f}")

            sym_info = None if (DRY_RUN or client is None) else client.get_symbol_info(symbol)
            tp_sl    = place_tp_sl(client, symbol, filled_qty, signal["tps"], signal["sl"], sym_info or {})

            order["tp_orders"]   = tp_sl["tp_orders"]
            order["sl_order"]    = tp_sl["sl_order"]
            order["tp_sl_placed"] = True

            changed = True
            send_telegram(build_fill_notification(signal, order, tp_sl))

    if changed:
        save_trades(trades)


# ── TP fill check → cancel remaining open entries ─────────────────────────────

def check_tp_fills(client: BinanceClient | None):
    trades  = load_trades()
    changed = False

    for trade in trades:
        signal  = trade["signal"]
        result  = trade["result"]
        symbol  = signal["symbol"]
        orders  = result.get("placed_orders", [])

        # Skip if entries already cleaned up
        if trade.get("entries_cancelled"):
            continue

        # Collect all TP order IDs across filled entries
        tp_order_ids = [
            tp["orderId"]
            for order in orders
            for tp in order.get("tp_orders", [])
            if tp.get("orderId") and str(tp["orderId"]) != "DRY_RUN"
        ]
        if not tp_order_ids:
            continue

        # Check if any TP has filled
        any_tp_filled = False
        for tp_id in tp_order_ids:
            try:
                if DRY_RUN or client is None:
                    any_tp_filled = True
                    break
                status = client.get_order(symbol=symbol, orderId=tp_id)
                if status["status"] == "FILLED":
                    any_tp_filled = True
                    logging.info(f"🎯 TP fill detected on {symbol} (order {tp_id})")
                    break
            except BinanceAPIException as e:
                logging.warning(f"Could not query TP order {tp_id}: {e.message}")

        if not any_tp_filled:
            continue

        # Cancel all remaining OPEN entry orders for this trade
        cancelled = []
        for order in orders:
            if order.get("tp_sl_placed") or str(order.get("orderId")) == "DRY_RUN":
                continue
            try:
                if DRY_RUN or client is None:
                    logging.info(f"[DRY-RUN] Would cancel entry order {order['orderId']}")
                    cancelled.append(order["orderId"])
                else:
                    status = client.get_order(symbol=symbol, orderId=order["orderId"])
                    if status["status"] in ("NEW", "PARTIALLY_FILLED"):
                        client.cancel_order(symbol=symbol, orderId=order["orderId"])
                        cancelled.append(order["orderId"])
                        logging.info(f"❌ Cancelled open entry {order['orderId']} ({symbol})")
                order["binance_status"] = "CANCELLED_AFTER_TP"
                order["tp_sl_placed"]   = True
            except BinanceAPIException as e:
                logging.warning(f"Could not cancel entry {order['orderId']}: {e.message}")

        if cancelled:
            trade["entries_cancelled"] = True
            changed = True
            send_telegram(
                f"🧹 *{symbol}* — TP hit!\n"
                f"Cancelled `{len(cancelled)}` unfilled entr{'y' if len(cancelled)==1 else 'ies'}."
            )

    if changed:
        save_trades(trades)


def main():
    b_key    = os.getenv("BINANCE_API_KEY", "")
    b_secret = os.getenv("BINANCE_API_SECRET", "")

    client = None
    if not DRY_RUN:
        if not b_key or not b_secret:
            logging.error("Set BINANCE_API_KEY and BINANCE_API_SECRET in .env")
            return
        client = BinanceClient(b_key, b_secret, tld="us")
        try:
            client.ping()
            logging.info("Binance US connected ✓")
        except Exception as e:
            logging.error(f"Binance connection failed: {e}")
            return

    mode = "DRY RUN" if DRY_RUN else "🔴 LIVE"
    logging.info(f"Fill monitor started — {mode}")
    logging.info(f"Polling every {POLL_INTERVAL}s for filled entry orders...")
    send_telegram(f"👁️ Fill monitor started — *{mode}*")

    while True:
        try:
            check_fills(client)
            check_tp_fills(client)
        except Exception as e:
            logging.error(f"Poll error: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
