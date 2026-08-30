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
import json
import time
import logging
import requests
from pathlib import Path
from dotenv import load_dotenv

TRADES_LOG      = Path(__file__).parent / "trades.json"
PENDING_CONFIRM = Path(__file__).parent / "pending_confirm.json"
OFFSET_FILE     = Path(__file__).parent / "bot_offset.json"

INDICATOR_OPTIONS = [
    ("RSI Oversold",   "rsi_oversold"),
    ("RSI Overbought", "rsi_overbought"),
    ("Fib 0.618",      "fib_618"),
    ("Fib 0.786",      "fib_786"),
    ("EMA Support",    "ema_support"),
    ("EMA Resistance", "ema_resistance"),
    ("Volume Spike",   "volume_spike"),
    ("MACD Cross",     "macd_cross"),
    ("S/R Level",      "sr_level"),
    ("Bollinger Band", "bb_squeeze"),
    ("Divergence",     "divergence"),
    ("Order Block",    "order_block"),
]

PENDING_INDICATOR_STATE = Path(__file__).parent / "pending_indicator_state.json"

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


def send_with_keyboard(msg: str, keyboard: dict) -> int | None:
    try:
        r = requests.post(
            f"{BASE}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown", "reply_markup": keyboard},
            timeout=10,
        )
        return r.json().get("result", {}).get("message_id")
    except Exception as e:
        logging.warning(f"send_with_keyboard failed: {e}")
        return None


def edit_message_keyboard(message_id: int, keyboard: dict):
    try:
        requests.post(
            f"{BASE}/editMessageReplyMarkup",
            json={"chat_id": CHAT_ID, "message_id": message_id, "reply_markup": keyboard},
            timeout=10,
        )
    except Exception as e:
        logging.warning(f"editMessageReplyMarkup failed: {e}")


def edit_message_text(message_id: int, text: str):
    try:
        requests.post(
            f"{BASE}/editMessageText",
            json={"chat_id": CHAT_ID, "message_id": message_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        logging.warning(f"editMessageText failed: {e}")


def build_indicator_keyboard(selected: set) -> dict:
    rows = []
    for i in range(0, len(INDICATOR_OPTIONS), 2):
        row = []
        for label, key in INDICATOR_OPTIONS[i:i + 2]:
            prefix = "✅ " if key in selected else ""
            row.append({"text": prefix + label, "callback_data": f"ind_toggle_{key}"})
        rows.append(row)
    rows.append([
        {"text": "💾 Save", "callback_data": "ind_save"},
        {"text": "Skip",    "callback_data": "ind_skip"},
    ])
    return {"inline_keyboard": rows}


def send_indicator_prompt(symbol: str, source: str = "manual"):
    keyboard = build_indicator_keyboard(set())
    msg_id   = send_with_keyboard(
        f"📊 *What confluence did you see for {symbol}?*\nTap to select, then Save.",
        keyboard,
    )
    PENDING_INDICATOR_STATE.write_text(json.dumps({
        "symbol": symbol, "source": source, "message_id": msg_id, "selected": [],
    }))


def save_manual_confluences(symbol: str, indicators: list, source: str = "manual"):
    trades = json.loads(TRADES_LOG.read_text()) if TRADES_LOG.exists() else []
    for t in reversed(trades):
        if t.get("source", "telegram") == source and t["signal"]["symbol"] == symbol:
            t["manual_confluences"] = indicators
            break
    TRADES_LOG.write_text(json.dumps(trades, indent=2))


def handle_status():
    from morning_check import load_trades, categorize_trades, build_message, recent_pnl_line
    trades          = load_trades()
    pending, active = categorize_trades(trades)
    msg             = build_message(pending, active)
    msg             = msg.replace("🌅 *TradingLuna* — 6:30 AM", "📡 *TradingLuna Status*")
    msg            += recent_pnl_line(trades)
    send(msg)


def handle_pending(binance_client=None):
    import json
    from pathlib import Path
    import requests as req

    def _price(symbol):
        try:
            r = req.get(
                "https://api.binance.us/api/v3/ticker/price",
                params={"symbol": symbol}, timeout=5,
            )
            return float(r.json()["price"])
        except Exception:
            return None

    trades_path = Path(__file__).parent / "trades.json"
    trades = json.loads(trades_path.read_text()) if trades_path.exists() else []

    pending_lines = []
    active_lines  = []

    for trade in trades:
        if trade.get("trade_closed"):
            continue
        signal  = trade["signal"]
        result  = trade.get("result", {})
        orders  = result.get("placed_orders", [])
        symbol  = signal["symbol"]
        dir_    = signal.get("direction", "LONG")
        tps     = signal.get("tps", {})
        sl      = signal.get("sl")
        src     = trade.get("source", "telegram")
        src_tag = "✍️" if src == "manual" else "🤖"

        skip = {"CANCELED", "CANCELLED_AFTER_TP"}
        live = [o for o in orders if o.get("binance_status") not in skip
                and str(o.get("orderId", "")) not in ("", "DRY_RUN")]
        if not live:
            continue

        filled   = [o for o in live if o.get("filled_qty")]
        unfilled = [o for o in live if not o.get("filled_qty") and not o.get("tp_sl_placed")]

        now = _price(symbol)
        price_str = f"  Current: `${now:,.4f}`\n" if now else ""

        if filled:
            avg_prices = [o["avg_fill_price"] for o in filled if o.get("avg_fill_price")]
            avg_entry  = sum(avg_prices) / len(avg_prices) if avg_prices else None

            unreal = ""
            if now and avg_entry:
                pct = (now - avg_entry) / avg_entry * 100
                sign = "+" if pct >= 0 else ""
                unreal = f"  Unrealized: `{sign}{pct:.2f}%`\n"

            sl_line = f"  SL: `${sl:,.4f}`\n" if sl else ""

            # Collect remaining open TP orders
            open_tps = []
            for o in filled:
                for i, tp in enumerate(o.get("tp_orders", []), start=1):
                    if tp.get("status") not in ("FILLED", "CANCELED") and tp.get("price"):
                        open_tps.append((i, tp["price"]))

            tp_str = ""
            if open_tps:
                tp_str = "  Open TPs: " + "  ".join(f"TP{i}: `${p:,.4f}`" for i, p in open_tps) + "\n"

            trail = "  🛡️ SL trailed to breakeven\n" if any(o.get("sl_trailed") for o in filled) else ""
            entry_str = f"`${avg_entry:,.4f}`" if avg_entry else "?"
            active_lines.append(
                f"{src_tag} *{symbol} {dir_}* — avg entry {entry_str}\n"
                f"{price_str}{unreal}{sl_line}{tp_str}{trail}"
            )

        elif unfilled:
            prices = sorted(set(o["price"] for o in unfilled))
            prices_str = " / ".join(f"`${p:,.4f}`" for p in prices)
            sl_line = f"  SL: `${sl:,.4f}`\n" if sl else ""
            tp_str  = ""
            if tps:
                tp_str = "  TPs: " + "  ".join(f"TP{k[-1]}: `${v:,.4f}`" for k, v in sorted(tps.items())) + "\n"
            pending_lines.append(
                f"{src_tag} *{symbol} {dir_}* — {len(unfilled)} orders @ {prices_str}\n"
                f"{price_str}{sl_line}{tp_str}"
            )

    # Cross-check against live open orders on Binance — a trade marked
    # trade_closed can still leave TP/SL orders open on the exchange, and
    # orders placed outside the bot are never in trades.json at all. Both
    # cases are invisible to the trade-log scan above, so flag them separately.
    known_order_closed = {}  # orderId(str) -> trade_closed(bool)
    for trade in trades:
        closed = bool(trade.get("trade_closed"))
        for o in trade.get("result", {}).get("placed_orders", []):
            oid = str(o.get("orderId", ""))
            if oid and oid not in ("None", "DRY_RUN"):
                known_order_closed[oid] = closed
            for tp in o.get("tp_orders", []):
                toid = str(tp.get("orderId", ""))
                if toid and toid not in ("None", "DRY_RUN"):
                    known_order_closed[toid] = closed
            sl = o.get("sl_order") or {}
            soid = str(sl.get("orderId", ""))
            if soid and soid not in ("None", "DRY_RUN", "SOFTWARE_SL"):
                known_order_closed[soid] = closed

    orphan_lines = []
    if binance_client:
        try:
            open_orders = binance_client.get_open_orders()
        except Exception as e:
            open_orders = []
            logging.warning(f"/pending: could not fetch live open orders: {e}")
        for o in open_orders:
            oid    = str(o.get("orderId", ""))
            closed = known_order_closed.get(oid)
            if oid not in known_order_closed:
                reason = "not in trade log"
            elif closed:
                reason = "trade marked closed but order still open"
            else:
                continue  # already covered by pending_lines/active_lines above
            orphan_lines.append(
                f"❓ *{o.get('symbol')} {o.get('side')}* {o.get('type')} — "
                f"qty `{o.get('origQty')}` @ `${float(o.get('price', 0)):,.4f}`\n"
                f"  orderId `{oid}` — _{reason}_"
            )

    if not pending_lines and not active_lines and not orphan_lines:
        send("📋 *Pending Trades* — No open or pending trades.")
        return

    msg = "📋 *Pending Trades*\n"
    if pending_lines:
        msg += f"\n⏳ *Waiting to fill ({len(pending_lines)}):*\n"
        msg += "\n".join(pending_lines)
    if active_lines:
        msg += f"\n📈 *Active positions ({len(active_lines)}):*\n"
        msg += "\n".join(active_lines)
    if orphan_lines:
        msg += f"\n⚠️ *Untracked open orders on Binance ({len(orphan_lines)}):*\n"
        msg += "\n".join(orphan_lines)
        msg += "\n_Not part of any open tracked trade — verify and cancel on Binance if unwanted._"

    send(msg)


def handle_cancel(args: str, binance_client):
    """Manually cancel open order(s) for a symbol.
    Usage:
      /cancel BTC              — list open orders for BTCUSDT
      /cancel BTC <orderId>    — cancel one specific order
      /cancel BTC ALL          — cancel every open order for BTCUSDT
    """
    import json
    from pathlib import Path

    parts = args.strip().split()
    if not parts:
        send(
            "Usage:\n"
            "`/cancel BTC` — list open orders\n"
            "`/cancel BTC <orderId>` — cancel one order\n"
            "`/cancel BTC ALL` — cancel every open order for that symbol"
        )
        return

    raw_symbol = parts[0].upper().lstrip("$")
    symbol     = raw_symbol if raw_symbol.endswith("USDT") else raw_symbol + "USDT"
    target     = parts[1].upper() if len(parts) > 1 else None

    trades_path = Path(__file__).parent / "trades.json"
    trades = json.loads(trades_path.read_text()) if trades_path.exists() else []

    # Collect every open order (tracked in the log) for this symbol
    open_orders = []
    for trade in trades:
        if trade.get("trade_closed"):
            continue
        if trade.get("signal", {}).get("symbol") != symbol:
            continue
        for o in trade.get("result", {}).get("placed_orders", []):
            oid = str(o.get("orderId", ""))
            if oid and oid not in ("None", "DRY_RUN") and not o.get("filled_qty") \
                    and o.get("binance_status") not in ("CANCELED", "CANCELLED_AFTER_TP"):
                open_orders.append({"order": o, "field": "entry", "orderId": oid,
                                     "side": "BUY", "price": o.get("price"), "qty": o.get("qty")})
            for tp in o.get("tp_orders", []):
                toid = str(tp.get("orderId", ""))
                if toid and toid not in ("None", "DRY_RUN") and tp.get("status") not in ("FILLED", "CANCELED"):
                    open_orders.append({"order": tp, "field": "TP", "orderId": toid,
                                         "side": "SELL", "price": tp.get("price"), "qty": tp.get("qty")})
            sl = o.get("sl_order") or {}
            soid = str(sl.get("orderId", ""))
            if soid and soid not in ("None", "DRY_RUN", "SOFTWARE_SL") and sl.get("status") not in ("FILLED", "CANCELED"):
                open_orders.append({"order": sl, "field": "SL", "orderId": soid,
                                     "side": "SELL", "price": sl.get("price"), "qty": sl.get("qty")})

    if not target:
        # ── List mode ──────────────────────────────────────────────────────
        lines = [f"📋 *Open orders for {symbol}:*\n"]
        for o in open_orders:
            price = f"${o['price']:,.4f}" if o.get("price") else "?"
            lines.append(f"  `{o['orderId']}`  {o['side']} {o['field']}  qty `{o['qty']}`  @ {price}")

        tracked_ids = {o["orderId"] for o in open_orders}
        if binance_client:
            try:
                for lo in binance_client.get_open_orders(symbol=symbol):
                    loid = str(lo["orderId"])
                    if loid not in tracked_ids:
                        lines.append(f"  `{loid}`  {lo['side']} (untracked)  qty `{lo['origQty']}`  @ ${float(lo['price']):,.4f}")
            except Exception as e:
                logging.warning(f"/cancel list: could not fetch live orders for {symbol}: {e}")

        if len(lines) == 1:
            send(f"📋 No open orders found for {symbol}.")
            return
        lines.append(f"\nUse `/cancel {raw_symbol} <orderId>` to cancel one, or `/cancel {raw_symbol} ALL` to cancel everything.")
        send("\n".join(lines))
        return

    # ── Cancel mode ───────────────────────────────────────────────────────────
    if target == "ALL":
        ids_to_cancel = [o["orderId"] for o in open_orders]
        if binance_client:
            try:
                tracked_ids = set(ids_to_cancel)
                for lo in binance_client.get_open_orders(symbol=symbol):
                    loid = str(lo["orderId"])
                    if loid not in tracked_ids:
                        ids_to_cancel.append(loid)
            except Exception as e:
                logging.warning(f"/cancel ALL: could not fetch live orders for {symbol}: {e}")
    else:
        ids_to_cancel = [target]

    if not ids_to_cancel:
        send(f"📋 Nothing to cancel for {symbol}.")
        return

    canceled, failed = [], []
    for oid in ids_to_cancel:
        try:
            if binance_client:
                binance_client.cancel_order(symbol=symbol, orderId=int(oid))
            canceled.append(oid)
        except Exception as e:
            msg = getattr(e, "message", str(e))
            if "unknown order" in msg.lower() or "does not exist" in msg.lower():
                canceled.append(oid)  # already gone — treat as success
            else:
                failed.append((oid, msg))

    canceled_set = set(canceled)
    changed = False
    for o in open_orders:
        if o["orderId"] in canceled_set:
            if o["field"] == "entry":
                o["order"]["binance_status"] = "CANCELED"
                o["order"]["tp_sl_placed"]   = True
            else:
                o["order"]["status"]       = "CANCELED"
                o["order"]["pnl_notified"] = True
            changed = True

    if changed:
        trades_path.write_text(json.dumps(trades, indent=2))

    lines = []
    if canceled:
        lines.append(f"✅ Cancelled {len(canceled)} order(s) for {symbol}: " + ", ".join(f"`{i}`" for i in canceled))
    if failed:
        lines.append("⚠️ Failed to cancel: " + ", ".join(f"`{i}` ({m})" for i, m in failed))
    send("\n".join(lines) if lines else f"Nothing to cancel for {symbol}.")


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


def answer_callback(callback_query_id: str, text: str = ""):
    try:
        requests.post(
            f"{BASE}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=10,
        )
    except Exception as e:
        logging.warning(f"answerCallbackQuery failed: {e}")


def handle_callback(callback_query: dict, binance_client):
    cq_id = callback_query["id"]
    data  = callback_query.get("data", "")

    if data == "confirm_trade":
        if not PENDING_CONFIRM.exists():
            answer_callback(cq_id, "No pending signal found.")
            send("⚠️ No pending signal found — it may have expired.")
            return
        signal = json.loads(PENDING_CONFIRM.read_text())
        PENDING_CONFIRM.unlink()
        answer_callback(cq_id, "Executing trade...")
        send(f"✅ Executing *{signal['symbol']} {signal['direction']}*...")
        try:
            from trader import execute_signal, log_trade, snapshot_confluence, build_confirmation
            result     = execute_signal(binance_client, signal)
            confluence = snapshot_confluence(signal["symbol"])
            log_trade(signal, result, confluence, source="telegram")
            send(build_confirmation(signal, result))
        except Exception as e:
            logging.error(f"confirm_trade execution failed: {e}")
            send(f"❌ Execution failed: `{e}`")

    elif data == "skip_trade":
        if PENDING_CONFIRM.exists():
            signal = json.loads(PENDING_CONFIRM.read_text())
            PENDING_CONFIRM.unlink()
            answer_callback(cq_id, "Trade skipped.")
            send(f"⏭️ *{signal['symbol']} {signal['direction']}* skipped.")
        else:
            answer_callback(cq_id, "No pending signal.")

    elif data.startswith("ind_toggle_"):
        if not PENDING_INDICATOR_STATE.exists():
            answer_callback(cq_id, "Session expired — re-submit the signal.")
            return
        state    = json.loads(PENDING_INDICATOR_STATE.read_text())
        key      = data[len("ind_toggle_"):]
        selected = set(state["selected"])
        if key in selected:
            selected.discard(key)
        else:
            selected.add(key)
        state["selected"] = list(selected)
        PENDING_INDICATOR_STATE.write_text(json.dumps(state))
        answer_callback(cq_id)
        if state.get("message_id"):
            edit_message_keyboard(state["message_id"], build_indicator_keyboard(selected))

    elif data == "ind_save":
        if not PENDING_INDICATOR_STATE.exists():
            answer_callback(cq_id, "Session expired.")
            return
        state      = json.loads(PENDING_INDICATOR_STATE.read_text())
        indicators = [key for _, key in INDICATOR_OPTIONS if key in state["selected"]]
        if len(indicators) < 2:
            answer_callback(cq_id, "Select at least 2 indicators before saving.")
            return
        PENDING_INDICATOR_STATE.unlink()
        try:
            save_manual_confluences(state["symbol"], indicators, source=state.get("source", "manual"))
            labels = [lbl for lbl, key in INDICATOR_OPTIONS if key in state["selected"]]
            answer_callback(cq_id, "Saved!")
            if state.get("message_id"):
                edit_message_text(state["message_id"], f"📊 *Confluence saved for {state['symbol']}:*\n" + ", ".join(labels))
        except Exception as e:
            logging.warning(f"save_manual_confluences failed: {e}")
            answer_callback(cq_id, "Save failed.")

    elif data == "ind_skip":
        if PENDING_INDICATOR_STATE.exists():
            PENDING_INDICATOR_STATE.unlink()
        answer_callback(cq_id, "Skipped.")
        cq_msg_id = callback_query.get("message", {}).get("message_id")
        if cq_msg_id:
            edit_message_text(cq_msg_id, "📊 No confluence recorded.")


def handle_balance(binance_client):
    if not binance_client:
        send("⚠️ No Binance API keys configured.")
        return
    try:
        from trader import get_usdt_balance
        balance = get_usdt_balance(binance_client)
        send(f"💵 *Available USDT:* `${balance:,.2f}`")
    except Exception as e:
        send(f"⚠️ Could not fetch balance: `{e}`")


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
    import threading
    from trader import parse_signal, execute_signal, log_trade, snapshot_confluence, build_confirmation

    # Extract Note: line before passing to signal parser
    note_lines  = [l.strip() for l in raw_text.splitlines() if l.strip().lower().startswith("note:")]
    notes       = note_lines[0][5:].strip() if note_lines else None
    signal_text = "\n".join(l for l in raw_text.splitlines() if not l.strip().lower().startswith("note:"))

    signal = parse_signal(signal_text)
    if not signal:
        send(
            "❌ Could not parse signal. Use this format:\n\n"
            "`$BTC LONG 95000 - 94000`\n"
            "`TP1: 96500 TP2: 97000 TP3: 97500`\n"
            "`SL: 93500`\n"
            "`Note: EMA 200 bounce, high volume`"
        )
        return

    if signal["sl"] is None:
        sl_pct_env = os.getenv("DEFAULT_SL_PCT")
        if not sl_pct_env:
            send(
                f"⚠️ *{signal['symbol']} signal rejected — no SL found*\n"
                f"_Add an `SL: ...` line, or set `DEFAULT_SL_PCT` in .env to auto-calculate one._"
            )
            return
        sl_pct  = float(sl_pct_env)
        bottom  = min(signal["entries"])
        auto_sl = round(bottom * (1 - sl_pct), 8)
        signal["sl"]      = auto_sl
        signal["sl_auto"] = True
        signal["sl_pct"]  = sl_pct
        logging.info(
            f"⚠️  /signal {signal['symbol']} had no SL — auto SL: ${auto_sl:.6f} "
            f"({sl_pct*100:.0f}% below ${bottom:.6f})"
        )

    note_line = f"\n📝 Note: _{notes}_" if notes else ""
    send(
        f"📨 *Parsed signal:* {signal['symbol']} {signal['direction']}\n"
        f"Entries: {signal['entries']}\n"
        f"TPs: {signal['tps']}\n"
        f"SL: {signal['sl']}"
        f"{note_line}\n\n"
        "⏳ Executing..."
    )

    # If no TPs specified, auto-calculate at fill time from avg entry price
    if not signal["tps"]:
        signal["tp_mode"]   = "auto_pct"
        signal["tp_pcts"]   = [0.07, 0.15]
        signal["tp_splits"] = [0.50, 0.50]

    # Execute order immediately — don't wait for confluence snapshot
    try:
        result = execute_signal(binance_client, signal)
    except Exception as e:
        logging.error(f"execute_signal failed: {e}")
        send(f"❌ *Order failed:* `{e}`")
        return
    log_trade(signal, result, confluence=None, source="manual", notes=notes)
    send(build_confirmation(signal, result))
    send_indicator_prompt(signal["symbol"])

    # Fetch confluence in background and update the trade log
    def fetch_and_update():
        try:
            confluence = snapshot_confluence(signal["symbol"])
            trades = json.loads(TRADES_LOG.read_text()) if TRADES_LOG.exists() else []
            # Find the trade we just logged (last manual entry for this symbol)
            for t in reversed(trades):
                if t.get("source") == "manual" and t["signal"]["symbol"] == signal["symbol"] and not t.get("confluence"):
                    t["confluence"] = confluence
                    break
            TRADES_LOG.write_text(json.dumps(trades, indent=2))
            logging.info(f"Confluence snapshot saved for {signal['symbol']}")
        except Exception as e:
            logging.warning(f"Confluence snapshot failed: {e}")

    threading.Thread(target=fetch_and_update, daemon=True).start()


HELP = (
    "Commands:\n"
    "/status — health check + positions\n"
    "/pending — open & pending trades with live price\n"
    "/report — last 7 days (Claude analysis)\n"
    "/balance — available USDT balance\n"
    "/pnl — your signals vs Telegram signals\n"
    "/signal — submit your own trade\n"
    "/cancel — cancel open order(s) manually\n\n"
    "Signal format:\n"
    "`/signal $BTC LONG 95000 - 94000`\n"
    "`TP1: 96500 TP2: 97000`\n"
    "`SL: 93500`\n\n"
    "Cancel format:\n"
    "`/cancel BTC` — list open orders\n"
    "`/cancel BTC <orderId>` — cancel one\n"
    "`/cancel BTC ALL` — cancel all"
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
            binance_client = BinanceClient(b_key, b_secret, tld="us", requests_params={"timeout": 15})
            binance_client.ping()
            logging.info("Binance US connected ✓")
        except Exception as e:
            logging.warning(f"Binance connection failed — /signal will run in dry-run: {e}")
    else:
        logging.warning("No Binance API keys — /signal will run in dry-run mode")

    logging.info("Bot started — listening for commands")
    send(f"🤖 *TradingLuna Bot* online\n\n{HELP}")

    # Load saved offset so restarts don't reprocess old messages.
    # If no saved offset, skip all pending updates by fast-forwarding to latest.
    if OFFSET_FILE.exists():
        offset = json.loads(OFFSET_FILE.read_text()).get("offset", 0)
    else:
        latest = get_updates(offset=-1)  # Telegram returns last update with offset=-1
        offset = (latest[-1]["update_id"] + 1) if latest else 0
        OFFSET_FILE.write_text(json.dumps({"offset": offset}))
    while True:
        updates = get_updates(offset)
        for update in updates:
            offset = update["update_id"] + 1
            OFFSET_FILE.write_text(json.dumps({"offset": offset}))

            # Handle inline button taps
            if "callback_query" in update:
                cq      = update["callback_query"]
                cq_chat = str(cq.get("message", {}).get("chat", {}).get("id", ""))
                if cq_chat == CHAT_ID:
                    handle_callback(cq, binance_client)
                continue

            msg     = update.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text    = msg.get("text", "").strip()

            if chat_id != CHAT_ID:
                continue

            logging.info(f"Command: {text[:80]}")
            lower = text.lower()

            if lower.startswith("/status"):
                handle_status()
            elif lower.startswith("/pending"):
                handle_pending(binance_client)
            elif lower.startswith("/cancel"):
                handle_cancel(text[len("/cancel"):].strip(), binance_client)
            elif lower.startswith("/balance"):
                handle_balance(binance_client)
            elif lower.startswith("/pnl"):
                handle_pnl()
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
