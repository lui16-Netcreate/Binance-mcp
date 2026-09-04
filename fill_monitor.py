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
from decimal import Decimal
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
SL_SLIP     = 0.003  # 0.3% below SL price for the limit leg (ensures fill)
TP_SPLIT    = [0.40, 0.30, 0.20, 0.10]
SL_LOSS_PCT = float(os.getenv("SL_LOSS_PCT", "0.50"))  # max loss per order as fraction of order value


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
    step_dec = Decimal(step)
    return float((Decimal(str(value)) // step_dec) * step_dec)


def _fmt(value) -> str:
    """Format a number for Binance API as plain decimal — avoids scientific notation (e.g. 7e-05)."""
    return format(Decimal(str(value)), 'f')


def _calc_pnl(entry_price: float, exit_price: float, qty: float) -> tuple[float, float]:
    """Returns (pnl_usdt, pnl_pct) for a LONG spot trade."""
    pnl_usdt = (exit_price - entry_price) * qty
    pnl_pct  = ((exit_price - entry_price) / entry_price * 100) if entry_price else 0
    return round(pnl_usdt, 4), round(pnl_pct, 2)


def _avg_fill_price_from_status(status: dict, fallback: float) -> tuple[float, float]:
    """Extracts actual avg fill price and qty from a Binance order status dict."""
    exec_qty  = float(status.get("executedQty", 0))
    quote_qty = float(status.get("cummulativeQuoteQty", 0))
    avg_price = (quote_qty / exec_qty) if exec_qty > 0 else fallback
    return avg_price, exec_qty


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
    avg_fill_price: float = 0.0,
    tp_splits: list = None,
) -> dict:
    lot_step  = _get_filter(sym_info, "LOT_SIZE", "stepSize")
    tick_size = _get_filter(sym_info, "PRICE_FILTER", "tickSize")

    tp_prices = [tps.get(f"tp{i}") for i in range(1, 5)]
    tp_prices = [p for p in tp_prices if p]  # drop missing TPs

    splits = tp_splits if tp_splits is not None else TP_SPLIT

    # Distribute qty across TPs; last TP gets remainder to avoid rounding loss
    tp_qtys = []
    remaining = filled_qty
    for idx, split in enumerate(splits[:len(tp_prices)]):
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
                quantity=_fmt(qty),
                price=_fmt(price),
                timeInForce="GTC",
            )
            tp_orders.append({"price": price, "qty": qty, "orderId": order["orderId"], "type": "TP"})
            logging.info(f"🎯 TP sell  {qty:.8f} {symbol} @ {price}  (id={order['orderId']})")
        except BinanceAPIException as e:
            logging.error(f"TP order failed @ {tp_price}: {e.message}")
            tp_orders.append({"price": price, "qty": qty, "orderId": None, "error": e.message})

    # SL is handled as a software SL (check_software_sl polls price every 60s).
    # Binance spot locks the asset per sell order, so we can't place a stop-limit
    # when TP orders already lock the full position qty.
    sl_price = max(sl, avg_fill_price - (float(os.getenv("ORDER_SIZE_USD", "11")) * SL_LOSS_PCT / filled_qty)) if avg_fill_price > 0 and filled_qty > 0 else sl
    logging.info(f"{symbol} SL level: ${sl_price:,.4f} (monitored via software SL)")

    return {"tp_orders": tp_orders, "sl_order": {"software_sl": True, "sl_price": sl_price, "orderId": "SOFTWARE_SL"}}


# ── Confirmation message ──────────────────────────────────────────────────────

def build_fill_notification(signal: dict, order: dict, tp_sl: dict) -> str:
    sym    = signal["symbol"]
    prefix = "🔵 *[DRY RUN]* " if DRY_RUN else "✅ "
    msg    = f"{prefix}*{sym} entry filled!*\n\n"
    filled_qty = order.get('filled_qty', order['qty'])
    msg   += f"📥 Filled: `{filled_qty:.8f}` @ `${order.get('avg_fill_price', order['price']):,.4f}`\n\n"

    if tp_sl.get("tp_orders"):
        tp_lines = "\n".join(
            f"  TP{i+1}: `${o['price']:,.4f}` × `{o['qty']:.8f}`"
            if o.get("orderId") else
            f"  TP{i+1}: ❌ FAILED — `{o.get('error', 'unknown error')}`"
            for i, o in enumerate(tp_sl["tp_orders"])
        )
        failed_tps = any(not o.get("orderId") for o in tp_sl["tp_orders"])
        header = "⚠️ TP orders (some failed):" if failed_tps else "🎯 TP sells placed:"
        msg += f"{header}\n{tp_lines}\n\n"

    sl = tp_sl.get("sl_order", {})
    if sl:
        if sl.get("software_sl"):
            msg += f"🛑 SL: `${sl['sl_price']:,.4f}` _(software — monitored every 60s)_\n"
        elif sl.get("orderId"):
            msg += f"🛑 SL placed: stop=`${sl['stopPrice']:,.4f}` limit=`${sl['price']:,.4f}`\n"
        else:
            msg += f"⚠️ *SL FAILED* — `{sl.get('error', 'unknown error')}`\n"

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
                # Skip only if TP/SL orders were actually placed successfully.
                # If all order IDs are None (placement failed), fall through and retry.
                valid_id = lambda oid: oid and str(oid) not in ("None", "", "DRY_RUN", "SOFTWARE_SL")
                tp_orders_stored = order.get("tp_orders", [])
                has_any_tp = any(valid_id(o.get("orderId")) for o in tp_orders_stored)
                sl_info    = order.get("sl_order", {})
                has_sl     = valid_id(sl_info.get("orderId")) or sl_info.get("software_sl") is True
                if has_any_tp and has_sl:
                    continue  # fully protected — skip
                # SL or TPs missing — cancel any stale orders on Binance then retry
                if client:
                    for tp in tp_orders_stored:
                        oid = tp.get("orderId")
                        if valid_id(oid):
                            try:
                                client.cancel_order(symbol=symbol, orderId=oid)
                                logging.info(f"♻️  Cancelled stale TP {oid} before retry")
                            except BinanceAPIException:
                                pass
                    sl_oid = order.get("sl_order", {}).get("orderId")
                    if valid_id(sl_oid):
                        try:
                            client.cancel_order(symbol=symbol, orderId=sl_oid)
                            logging.info(f"♻️  Cancelled stale SL {sl_oid} before retry")
                        except BinanceAPIException:
                            pass
                order["tp_sl_placed"] = False
                order.pop("tp_orders", None)
                order.pop("sl_order", None)
                logging.info(f"♻️  Retrying TP/SL for {symbol} order {order.get('orderId')}")
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

            if status_data["status"] in ("CANCELED", "EXPIRED", "REJECTED"):
                order["tp_sl_placed"] = True  # skip — entry will never fill
                logging.info(f"Order {order['orderId']} was {status_data['status']} — skipping TP/SL")
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

            # Auto-calculate TP prices from avg fill price if tp_mode is auto_pct
            tps       = signal["tps"]
            tp_splits = None
            if signal.get("tp_mode") == "auto_pct" and avg_price > 0:
                pcts      = signal.get("tp_pcts",   [0.05, 0.10, 0.15])
                tp_splits = signal.get("tp_splits", [0.50, 0.25, 0.25])
                tps = {f"tp{i+1}": round(avg_price * (1 + p), 8) for i, p in enumerate(pcts)}
                logging.info(
                    f"📐 Auto TPs from {avg_price:.4f}: "
                    + "  ".join(f"TP{i+1}=${v:,.4f} (+{pcts[i]*100:.0f}%)" for i, v in enumerate(tps.values()))
                )

            sym_info = None if (DRY_RUN or client is None) else client.get_symbol_info(symbol)
            tp_sl    = place_tp_sl(client, symbol, filled_qty, tps, signal["sl"], sym_info or {}, avg_fill_price=avg_price, tp_splits=tp_splits)

            order["tp_orders"]   = tp_sl["tp_orders"]
            order["sl_order"]    = tp_sl["sl_order"]
            order["tp_sl_placed"] = True

            changed = True
            send_telegram(build_fill_notification(signal, order, tp_sl))

    if changed:
        save_trades(trades)


# ── Exit fill monitor (TP + SL) with P&L tracking ────────────────────────────

def _trail_sl_to_tp1(client: BinanceClient | None, symbol: str, entry: dict, signal: dict, sym_info: dict,
                      sibling_orders: list = None):
    """TP1 hit: cancel all remaining TPs + old SL, re-place TP2 for full remaining qty,
    place new SL at TP1 price (breakeven protection). Acts as an OCO pair.
    Also cancels any still-unfilled sibling entry orders from the same multi-entry
    range — once part of the position is being taken profit on, stop adding more
    exposure to the rest of the range."""
    if entry.get("sl_trailed"):
        return

    lot_step  = _get_filter(sym_info, "LOT_SIZE", "stepSize")
    tick_size = _get_filter(sym_info, "PRICE_FILTER", "tickSize")
    tp_orders = entry.get("tp_orders", [])
    sl_order  = entry.get("sl_order", {})

    total_filled = entry.get("filled_qty", 0)
    tp1_qty      = tp_orders[0].get("filled_qty", tp_orders[0].get("qty", 0)) if tp_orders else 0
    remaining    = _round_to(max(total_filled - tp1_qty, 0), lot_step)

    if remaining <= 0:
        entry["sl_trailed"] = True
        return

    # Cancel any still-unfilled sibling entries from the same range (e.g. the $79k
    # leg of an $80k/$79.5k/$79k range when only the top two fill and TP1 hits)
    for sib in sibling_orders or []:
        if sib is entry or sib.get("filled_qty"):
            continue
        if sib.get("binance_status") in ("CANCELED", "CANCELLED_AFTER_TP"):
            continue
        oid = sib.get("orderId")
        if oid and str(oid) not in ("DRY_RUN", "", "None") and client:
            try:
                client.cancel_order(symbol=symbol, orderId=oid)
                logging.info(f"❌ Cancelled unfilled sibling entry {oid} ({symbol}) — TP1 hit on range")
            except BinanceAPIException as e:
                logging.warning(f"Could not cancel sibling entry {oid}: {e.message}")
                continue
        sib["binance_status"] = "CANCELLED_AFTER_TP"
        sib["tp_sl_placed"]   = True

    # Cancel all remaining TP orders (TP2, TP3, TP4) — we'll re-place TP2 with full remaining qty
    for i, tp in enumerate(tp_orders[1:], start=2):
        if tp.get("status") in ("FILLED", "CANCELED"):
            continue
        oid = tp.get("orderId")
        cancel_ok = True
        if oid and str(oid) not in ("DRY_RUN", "", "None") and client:
            try:
                client.cancel_order(symbol=symbol, orderId=oid)
                logging.info(f"❌ Cancelled TP{i} {oid} ({symbol})")
            except BinanceAPIException as e:
                logging.warning(f"Could not cancel TP{i} {oid}: {e.message}")
                cancel_ok = False
        if cancel_ok:
            tp["status"] = "CANCELED"
            tp["pnl_notified"] = True

    # Cancel old SL
    sl_oid = sl_order.get("orderId")
    if sl_oid and str(sl_oid) not in ("DRY_RUN", "", "None") and not sl_order.get("status") and client:
        try:
            client.cancel_order(symbol=symbol, orderId=sl_oid)
            logging.info(f"❌ Cancelled old SL {sl_oid} ({symbol})")
        except BinanceAPIException as e:
            logging.warning(f"Could not cancel SL {sl_oid}: {e.message}")

    # Determine TP2 price (original signal TP2, or auto_pct calculation)
    tp2_price = tp_orders[1].get("price") if len(tp_orders) >= 2 else None
    if not tp2_price and signal.get("tp_mode") == "auto_pct":
        pcts     = signal.get("tp_pcts", [0.05, 0.10])
        avg_fill = entry.get("avg_fill_price", 0)
        if len(pcts) >= 2 and avg_fill:
            tp2_price = round(avg_fill * (1 + pcts[1]), 8)

    # Place new TP2 limit sell for the FULL remaining qty
    tp2_rounded   = _round_to(tp2_price, tick_size) if tp2_price else 0
    tp2_order_id  = None
    if tp2_rounded > 0:
        if DRY_RUN or not client:
            tp2_order_id = "DRY_RUN"
            logging.info(f"[DRY-RUN] SELL limit  {remaining} {symbol} @ {tp2_rounded}  (revised TP2)")
        else:
            try:
                order = client.order_limit_sell(
                    symbol=symbol, quantity=_fmt(remaining),
                    price=_fmt(tp2_rounded), timeInForce="GTC",
                )
                tp2_order_id = order["orderId"]
                logging.info(f"🎯 Revised TP2: {remaining} {symbol} @ {tp2_rounded}  (id={tp2_order_id})")
            except BinanceAPIException as e:
                logging.error(f"Revised TP2 failed: {e.message}")

        new_tp2 = {"price": tp2_rounded, "qty": remaining, "orderId": tp2_order_id,
                   "type": "TP", "pnl_notified": False}
        if len(tp_orders) >= 2:
            # Full replace, not update() — the old TP2 dict may carry a stale
            # "status": "CANCELED" from the cancel loop above, which must not
            # leak onto this freshly-placed order.
            tp_orders[1] = new_tp2
        else:
            tp_orders.append(new_tp2)

    # Place new SL at TP1 price (breakeven)
    tp1_price = tp_orders[0].get("price", 0) if tp_orders else 0
    sl_stop   = _round_to(tp1_price, tick_size)
    sl_limit  = _round_to(tp1_price * (1 - SL_SLIP), tick_size)
    sl_oid_new = None

    if DRY_RUN or not client:
        sl_oid_new = "DRY_RUN"
        logging.info(f"[DRY-RUN] STOP_LOSS_LIMIT  {remaining} {symbol} stop={sl_stop} limit={sl_limit}  (trail SL)")
    else:
        try:
            order = client.create_order(
                symbol=symbol, side="SELL", type="STOP_LOSS_LIMIT",
                quantity=_fmt(remaining), price=_fmt(sl_limit), stopPrice=_fmt(sl_stop),
                timeInForce="GTC",
            )
            sl_oid_new = order["orderId"]
            logging.info(f"🛡️ Trail SL: {remaining:.8f} {symbol} stop={sl_stop} limit={sl_limit}  (id={sl_oid_new})")
        except BinanceAPIException as e:
            logging.error(f"Trail SL failed: {e.message}")

    sl_order.update({
        "stopPrice": sl_stop, "price": sl_limit,
        "qty": remaining, "orderId": sl_oid_new,
        "status": None, "pnl_notified": False,
    })

    entry["sl_trailed"] = True

    send_telegram(
        f"🛡️ *{symbol} TP1 hit — SL moved to breakeven*\n"
        f"SL at TP1 price: `${sl_stop:,.4f}`\n"
        f"Remaining `{remaining}` rides to TP2 @ `${tp2_rounded:,.4f}`"
    )


def _cancel_open_entries(client: BinanceClient | None, trade: dict, symbol: str, orders: list):
    cancelled = []
    for order in orders:
        if order.get("tp_sl_placed") or str(order.get("orderId")) == "DRY_RUN":
            continue
        try:
            if client:
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
        send_telegram(
            f"🧹 *{symbol}* — TP hit!\n"
            f"Cancelled `{len(cancelled)}` unfilled entr{'y' if len(cancelled)==1 else 'ies'}."
        )


def _check_trade_closed(trade: dict):
    """Sum P&L and mark trade closed when all exit orders are resolved."""
    result  = trade.get("result", {})
    orders  = result.get("placed_orders", [])
    symbol  = trade["signal"]["symbol"]

    filled_entries = [o for o in orders if o.get("tp_sl_placed") and o.get("filled_qty")]
    if not filled_entries:
        # No entry ever filled — close the trade once every entry order has
        # reached a terminal non-fill state (no more chance of filling), so
        # it doesn't sit in limbo (never re-checked, never shown as resolved).
        terminal = {"CANCELED", "CANCELLED_AFTER_TP", "EXPIRED", "REJECTED"}
        if orders and all(o.get("binance_status") in terminal for o in orders) and not trade.get("trade_closed"):
            trade["trade_closed"]   = True
            trade["closed_at"]      = datetime.now(timezone.utc).isoformat()
            trade["total_pnl_usdt"] = 0.0
            logging.info(f"Trade closed (no fills — all entries terminal): {symbol}")
        return

    total_pnl  = 0.0
    all_closed = True

    for entry in filled_entries:
        sl  = entry.get("sl_order", {})
        tps = entry.get("tp_orders", [])

        if sl.get("status") == "FILLED":
            total_pnl += sl.get("pnl_usdt", 0)
            continue  # entry fully resolved via SL

        if tps:
            total_pnl += sum(tp.get("pnl_usdt", 0) for tp in tps if tp.get("status") == "FILLED")
            if not all(tp.get("status") in ("FILLED", "CANCELED") for tp in tps):
                all_closed = False
        else:
            all_closed = False

    if all_closed and filled_entries and not trade.get("trade_closed"):
        sign = "+" if total_pnl >= 0 else ""
        trade["trade_closed"]   = True
        trade["closed_at"]      = datetime.now(timezone.utc).isoformat()
        trade["total_pnl_usdt"] = round(total_pnl, 4)
        emoji = "✅" if total_pnl >= 0 else "❌"
        send_telegram(
            f"{emoji} *{symbol} trade closed*\n"
            f"Total P&L: `{sign}{total_pnl:.2f} USDT`"
        )
        logging.info(f"Trade closed: {symbol}  P&L={sign}{total_pnl:.2f} USDT")


def check_exit_fills(client: BinanceClient | None):
    """Polls Binance for TP and SL fills, records P&L, notifies via Telegram."""
    trades  = load_trades()
    changed = False

    for trade in trades:
        if trade.get("trade_closed"):
            continue

        signal  = trade["signal"]
        result  = trade.get("result", {})
        symbol  = signal["symbol"]
        orders  = result.get("placed_orders", [])

        filled_entries = [o for o in orders if o.get("tp_sl_placed") and o.get("filled_qty")]
        if not filled_entries:
            # Give _check_trade_closed() a chance to close out a trade whose
            # entries all expired/were cancelled without ever filling.
            _check_trade_closed(trade)
            if trade.get("trade_closed"):
                changed = True
            continue

        any_new_tp_hit = False
        sym_info       = None  # lazy-loaded on first TP1 hit

        for entry in filled_entries:
            entry_price = entry.get("avg_fill_price", 0)

            # ── Check each TP order ───────────────────────────────────────────
            for idx, tp in enumerate(entry.get("tp_orders", []), start=1):
                if tp.get("pnl_notified") or str(tp.get("orderId")) in ("DRY_RUN", "", "None", None):
                    continue
                try:
                    status = client.get_order(symbol=symbol, orderId=tp["orderId"]) if client else None
                except BinanceAPIException as e:
                    logging.warning(f"TP order {tp['orderId']} query failed: {e.message}")
                    continue

                if not status or status["status"] != "FILLED":
                    continue

                avg_price, fill_qty = _avg_fill_price_from_status(status, tp["price"])
                pnl_usdt, pnl_pct   = _calc_pnl(entry_price, avg_price, fill_qty)

                tp.update({
                    "status":         "FILLED",
                    "avg_fill_price": round(avg_price, 8),
                    "filled_qty":     fill_qty,
                    "filled_at":      datetime.now(timezone.utc).isoformat(),
                    "pnl_usdt":       pnl_usdt,
                    "pnl_pct":        pnl_pct,
                    "pnl_notified":   True,
                })
                changed        = True
                any_new_tp_hit = True

                sign = "+" if pnl_usdt >= 0 else ""
                send_telegram(
                    f"🎯 *{symbol} TP{idx} hit!*\n"
                    f"Sold `{fill_qty}` @ `${avg_price:,.4f}`\n"
                    f"Entry: `${entry_price:,.4f}`\n"
                    f"P&L: `{sign}{pnl_usdt:.2f} USDT` (`{sign}{pnl_pct:.2f}%`)"
                )
                logging.info(f"🎯 {symbol} TP{idx}: {sign}{pnl_usdt:.2f} USDT ({sign}{pnl_pct:.2f}%)")

                # TP1 hit → trail SL to breakeven, re-place TP2 for full remaining qty
                if idx == 1:
                    if sym_info is None and client:
                        sym_info = client.get_symbol_info(symbol)
                    _trail_sl_to_tp1(client, symbol, entry, signal, sym_info or {}, sibling_orders=orders)

                # TP2+ hit while SL was trailed → cancel the trail SL (OCO)
                elif idx >= 2 and entry.get("sl_trailed"):
                    sl = entry.get("sl_order", {})
                    sl_oid = sl.get("orderId")
                    if sl_oid and str(sl_oid) not in ("DRY_RUN", "", "None") and not sl.get("status") and client:
                        try:
                            client.cancel_order(symbol=symbol, orderId=sl_oid)
                            sl["status"] = "CANCELED"
                            sl["pnl_notified"] = True
                            logging.info(f"❌ Cancelled trail SL {sl_oid} (TP{idx} filled)")
                        except BinanceAPIException as e:
                            logging.warning(f"Could not cancel trail SL {sl_oid}: {e.message}")

            # ── Check SL order ────────────────────────────────────────────────
            sl = entry.get("sl_order", {})
            if sl and not sl.get("pnl_notified") and str(sl.get("orderId")) not in ("DRY_RUN", "", "None", None):
                try:
                    status = client.get_order(symbol=symbol, orderId=sl["orderId"]) if client else None
                except BinanceAPIException as e:
                    logging.warning(f"SL order {sl['orderId']} query failed: {e.message}")
                    status = None

                if status and status["status"] == "FILLED":
                    avg_price, fill_qty = _avg_fill_price_from_status(status, sl.get("price", 0))
                    pnl_usdt, pnl_pct   = _calc_pnl(entry_price, avg_price, fill_qty)

                    sl.update({
                        "status":         "FILLED",
                        "avg_fill_price": round(avg_price, 8),
                        "filled_qty":     fill_qty,
                        "filled_at":      datetime.now(timezone.utc).isoformat(),
                        "pnl_usdt":       pnl_usdt,
                        "pnl_pct":        pnl_pct,
                        "pnl_notified":   True,
                    })
                    changed = True

                    sign = "+" if pnl_usdt >= 0 else ""
                    label = "Trail SL hit (breakeven)" if entry.get("sl_trailed") else "SL hit"
                    send_telegram(
                        f"🛑 *{symbol} {label}*\n"
                        f"Sold `{fill_qty}` @ `${avg_price:,.4f}`\n"
                        f"Entry: `${entry_price:,.4f}`\n"
                        f"P&L: `{sign}{pnl_usdt:.2f} USDT` (`{sign}{pnl_pct:.2f}%`)"
                    )
                    logging.info(f"🛑 {symbol} SL: {sign}{pnl_usdt:.2f} USDT ({sign}{pnl_pct:.2f}%)")

                    # Trail SL filled → cancel the revised TP2 (OCO)
                    if entry.get("sl_trailed") and client:
                        for i, tp in enumerate(entry.get("tp_orders", [])[1:], start=2):
                            if tp.get("status") in ("FILLED", "CANCELED"):
                                continue
                            oid = tp.get("orderId")
                            if oid and str(oid) not in ("DRY_RUN", "", "None"):
                                try:
                                    client.cancel_order(symbol=symbol, orderId=oid)
                                    tp["status"] = "CANCELED"
                                    tp["pnl_notified"] = True
                                    logging.info(f"❌ Cancelled TP{i} {oid} (trail SL filled)")
                                except BinanceAPIException as e:
                                    logging.warning(f"Could not cancel TP{i} {oid}: {e.message}")

        # Cancel unfilled entries on first TP hit (only once)
        if any_new_tp_hit and not trade.get("entries_cancelled"):
            _cancel_open_entries(client, trade, symbol, orders)
            changed = True

        # Check if trade is fully resolved
        _check_trade_closed(trade)
        if trade.get("trade_closed"):
            changed = True

    if changed:
        save_trades(trades)


def check_software_sl(client: BinanceClient | None):
    """Software SL: poll live price; if below signal SL, cancel all open TPs and market-sell.
    Used instead of a Binance stop-limit order because spot trading locks the asset per order,
    making it impossible to have TP limit sells AND a stop-limit for the same qty simultaneously."""
    trades  = load_trades()
    changed = False

    for trade in trades:
        if trade.get("trade_closed") or trade.get("software_sl_triggered"):
            continue

        signal = trade["signal"]
        sl     = signal.get("sl")
        if not sl:
            continue

        result  = trade.get("result", {})
        symbol  = signal["symbol"]
        orders  = result.get("placed_orders", [])

        filled_entries = [o for o in orders if o.get("tp_sl_placed") and o.get("filled_qty")]
        if not filled_entries:
            continue

        # Fetch live price
        try:
            r = requests.get(
                f"https://api.binance.us/api/v3/ticker/price",
                params={"symbol": symbol}, timeout=5,
            )
            current_price = float(r.json()["price"])
        except Exception as e:
            logging.warning(f"Software SL: could not fetch price for {symbol}: {e}")
            continue

        if current_price > sl:
            continue  # price still above SL — nothing to do

        logging.warning(f"🛑 Software SL triggered: {symbol} price={current_price} <= SL={sl}")

        # Cancel all open TP orders
        for entry in filled_entries:
            lot_step = None
            sym_info = None
            for tp in entry.get("tp_orders", []):
                if tp.get("status") in ("FILLED", "CANCELED"):
                    continue
                oid = tp.get("orderId")
                if oid and str(oid) not in ("DRY_RUN", "", "None") and client:
                    try:
                        client.cancel_order(symbol=symbol, orderId=oid)
                        logging.info(f"❌ Cancelled TP {oid} (software SL)")
                    except BinanceAPIException as e:
                        logging.warning(f"Could not cancel TP {oid}: {e.message}")
                tp["status"] = "CANCELED"
                tp["pnl_notified"] = True

            # Market-sell the remaining filled qty
            remaining = entry.get("filled_qty", 0)
            if remaining <= 0:
                continue

            if sym_info is None and client:
                sym_info = client.get_symbol_info(symbol)
            lot_step = _get_filter(sym_info or {}, "LOT_SIZE", "stepSize")
            sell_qty = _round_to(remaining, lot_step)

            entry_price = entry.get("avg_fill_price", 0)
            pnl_usdt, pnl_pct = 0.0, 0.0

            if DRY_RUN or not client:
                logging.info(f"[DRY-RUN] SOFTWARE SL MARKET SELL {sell_qty:.8f} {symbol}")
                entry["sl_order"] = {"orderId": "DRY_RUN", "status": "FILLED",
                                     "avg_fill_price": current_price, "pnl_usdt": 0.0, "pnl_notified": True}
            else:
                try:
                    order = client.order_market_sell(symbol=symbol, quantity=_fmt(sell_qty))
                    avg_price, fill_qty = _avg_fill_price_from_status(order, current_price)
                    pnl_usdt, pnl_pct   = _calc_pnl(entry_price, avg_price, fill_qty)
                    sign = "+" if pnl_usdt >= 0 else ""
                    entry["sl_order"] = {
                        "orderId": order["orderId"], "status": "FILLED",
                        "avg_fill_price": round(avg_price, 8), "filled_qty": fill_qty,
                        "pnl_usdt": pnl_usdt, "pnl_pct": pnl_pct, "pnl_notified": True,
                    }
                    logging.info(f"🛑 Software SL sold {fill_qty:.8f} {symbol} @ {avg_price:.4f}  {sign}{pnl_usdt:.2f} USDT")
                    send_telegram(
                        f"🛑 *{symbol} SL hit* (software)\n"
                        f"Price `${current_price:,.4f}` ≤ SL `${sl:,.4f}`\n"
                        f"Sold `{fill_qty:.8f}` @ `${avg_price:,.4f}`\n"
                        f"P&L: `{sign}{pnl_usdt:.2f} USDT` (`{sign}{pnl_pct:.2f}%`)"
                    )
                except BinanceAPIException as e:
                    logging.error(f"Software SL market sell failed: {e.message}")
                    send_telegram(f"⚠️ *{symbol}* Software SL market sell failed:\n`{e.message}`")
                    continue

            changed = True

        trade["software_sl_triggered"] = True
        _check_trade_closed(trade)

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
        client = BinanceClient(b_key, b_secret, tld="us", requests_params={"timeout": 15})
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
            check_software_sl(client)
            check_exit_fills(client)
        except Exception as e:
            logging.error(f"Poll error: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
