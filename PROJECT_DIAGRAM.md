# Crypto Auto-Trader System — Project Diagram

```
╔══════════════════════════════════════════════════════════════════╗
║                    CRYPTO AUTO-TRADER SYSTEM                     ║
╚══════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PHASE 1 — FOUNDATION                  ✅ COMPLETE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ┌─────────────────┐  ┌─────────────┐  ┌──────────────────┐
  │ binance_data.py  │  │  server.py  │  │ *.pine backtests  │
  │                  │  │             │  │                   │
  │ • Price          │  │ MCP tools   │  │ confluence_       │
  │ • Candles        │  │ for Claude  │  │   backtest.pine   │
  │ • RSI/Fib        │  │ Desktop —   │  │ confluence_       │
  │ • Fear&Greed     │  │ also reads/ │  │   strategy.pine   │
  │ • Funding        │  │ clears the  │  │ sma_crossover_    │
  │ • L/S Ratio      │  │ webhook     │  │   backtest.pine   │
  │ • OI             │  │ signal      │  │                   │
  │ • News           │  │ inbox (db)  │  │                   │
  └─────────────────┘  └──────┬──────┘  └──────────────────┘
                               │
                    ┌──────────┴──────────┐
                    │   webhook.py + db.py │
                    │  FastAPI receiver —  │
                    │  TradingView alerts  │
                    │  POST → sqlite inbox │
                    │  (passive; not auto- │
                    │   executed)          │
                    └───────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PHASE 2 — CONFLUENCE MONITOR          ✅ COMPLETE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ┌──────────────────────────────────────────────┐
  │                  monitor.py                  │
  │                                              │
  │  Runs every 15 min per symbol                │
  │                                              │
  │  Checks: RSI · Fib golden zone · Fear&Greed ·│
  │          Funding · L/S Ratio · OI · News     │
  │                                              │
  │  If N conditions met:                        │
  │         ↓                                    │
  │  📱 Telegram alert → YOU                     │
  └──────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PHASE 3 — SIGNAL AUTO-EXECUTOR        ✅ COMPLETE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ┌─────────────────────────┐   ┌──────────────────────────┐
  │ "Crypto Chiefs Premium" │   │   Telegram command bot   │
  │  Telegram signal group  │   │        (bot.py)          │
  └────────────┬────────────┘   │                          │
               │ Telethon        │  /status   /pending      │
               │ userbot         │  /balance  /pnl          │
               ▼                 │  /report   /signal       │
  ┌─────────────────────────┐   │  /cancel  ← NEW           │
  │       trader.py         │   └────────────┬─────────────┘
  │                         │                │ manual signal
  │  1. Parse signal        │◄───────────────┘ ("/signal ...")
  │     • Symbol            │
  │     • Entry range → up  │
  │       to 3 limit BUYs   │   ┌─────────────────────────┐
  │       (top/mid/bottom)  │   │  Binance US — SPOT ONLY │
  │     • SL — required, or │   │  No leverage, no        │
  │       DEFAULT_SL_PCT    │   │  shorting. SHORT         │
  │       auto-calc, else   │   │  signals are logged +    │
  │       signal is skipped │   │  alerted, never executed │
  │     • TP1–4 (optional — │   └─────────────────────────┘
  │       else auto 50/50   │
  │       split at +7%/+15%)│
  │  2. Reject entries >5%  │
  │     (ENTRY_PRICE_MAX_   │
  │      DEVIATION) from    │
  │      live price          │
  │  3. Size each order:    │
  │     ORDER_SIZE_USD if   │
  │     set, else 1% of     │
  │     USDT balance        │
  │  4. Log to trades.json  │
  │     (source: telegram   │
  │      or manual)          │
  └────────────┬────────────┘
               │
               ▼
  📱 Telegram confirmation → YOU
  "📨 Parsed signal: BTCUSDT LONG ... ⏳ Executing..."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PHASE 3.5 — FILL MONITOR              ✅ COMPLETE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  fill_monitor.py polls every 60s (POLL_INTERVAL).

  Entry fill  → places TP1–4 limit sells on Binance.
                SL is a SOFTWARE stop (not a resting Binance
                order — spot locks qty per order, so a TP sell
                and a stop order can't coexist for the same
                qty). check_software_sl() polls live price
                every 60s; if breached, cancels open TPs and
                market-sells the remaining position.

  TP1 fill    → _trail_sl_to_tp1():
                • cancels old TP2, re-places TP2 for the FULL
                  remaining qty
                • cancels any still-UNFILLED sibling entries
                  from the same range (e.g. the $79k leg of an
                  $80k/$79.5k/$79k range) — stops adding more
                  exposure to a range already being exited
                • places a REAL STOP_LOSS_LIMIT order on
                  Binance at breakeven (TP1's fill price) —
                  acts as an OCO pair with the revised TP2

  TP2+ fill while trailed → cancels the breakeven SL (OCO).

  _check_trade_closed()     marks a trade closed once every
                             exit order is FILLED/CANCELED and
                             sums total P&L.

  Updates trades.json + sends Telegram confirmation on every
  TP hit, SL hit, and trade close.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PHASE 4 — TRADE LOG & INTELLIGENCE    ✅ COMPLETE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ┌──────────────────────────────────────────────┐
  │                 trades.json                  │
  │                                              │
  │  Per entry order:                            │
  │  • Signal received + timestamp + source      │
  │    (telegram / manual)                       │
  │  • Fill status, avg fill price               │
  │  • TP/SL orders + status (filled/pending/    │
  │    cancelled/cancelled_after_tp)             │
  │  • P&L (USDT + %)                            │
  │  • manual_confluences tags (bot.py asks      │
  │    "what confluence did you see?" after      │
  │    every trade — min 2 of 12 indicators)     │
  └──────────────────────────────────────────────┘
               │
               ▼
  analyzer.py (Claude/anthropic SDK) finds winning patterns
  report.py builds the summary
               │
               ▼
  📱 /report (7-day) and weekly Telegram summary → YOU

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PHASE 5 — WEB DASHBOARD               ✅ COMPLETE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ┌──────────────────────────────────────────────┐
  │         dashboard.py + dashboard.html         │
  │         (password-protected: DASHBOARD_USER/  │
  │          DASHBOARD_PASS)                      │
  │                                              │
  │  • Trade history: Executed/Skipped +          │
  │    Source filters (combinable)                │
  │  • P&L column — USDT and % (cost basis from   │
  │    filled orders)                             │
  │  • Confluence chips per trade                 │
  │  • Top 10 coins by 24h volume (live, 30s)     │
  │  • "With Errors" card + ✕ Clear button         │
  └──────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 OPS & RELIABILITY LAYER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  watchdog.py       cron every 5 min — Telegram alert if any
                     process is down/recovered (uses
                     trader.heartbeat for trader liveness)

  morning_check.py  cron 6:30 AM ET daily — process health +
                     pending fills + active positions +
                     unrealized P&L% + RSI(14) 4h per position

  restart.sh         stops all 5 screen sessions, git pull,
                     restarts all 5 wrapped in an auto-restart-
                     on-crash loop (trader, fill_monitor, bot,
                     dashboard, webhook)

  setup.py           interactive first-time .env setup with
                     API key validation

  history.py         read-only Telethon scan of past signal
                     group messages

  test_parse_signal.py  regression check for trader.parse_signal()
                         — run after any parser change
```

## Status Summary

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Foundation — binance_data.py, server.py MCP tools, Pine Script backtests | ✅ Complete |
| 2 | Confluence monitor — Telegram alerts on RSI+Fib+sentiment | ✅ Complete |
| 3 | Signal auto-executor — Telethon + trader.py + Binance US spot | ✅ Complete |
| 3.5 | Fill monitor — TP/software-SL, breakeven trail, sibling-range cancel | ✅ Complete |
| 4 | Trade log + Claude intelligence + reports | ✅ Complete |
| 5 | Web dashboard | ✅ Complete |
| — | Telegram command bot (bot.py) — /status /pending /balance /pnl /report /signal /cancel | ✅ Complete |
| — | Ops tooling — watchdog, morning_check, restart.sh, setup.py, history.py | ✅ Complete |

## Trading Ruleset

- **Binance US — spot only.** No leverage, no shorting. SHORT signals are logged and Telegram-alerted, never executed.
- Entry: up to 3 limit BUY orders at top/mid/bottom of the signal's price range (or a single order for a single-price signal).
- Position size per order: `ORDER_SIZE_USD` from `.env` if set, else 1% of USDT balance.
- Entries more than `ENTRY_PRICE_MAX_DEVIATION` (default 5%) from live price are rejected.
- SL: required. If a signal has no `SL:` line, falls back to `DEFAULT_SL_PCT` (below the lowest entry) if set in `.env`; otherwise the signal is skipped/rejected entirely (both the auto-listener and manual `/signal`).
- TP: uses the signal's TP1–4 if given; otherwise auto TP1 +7% / TP2 +15%, split 50/50 at avg fill price.
- **Breakeven trail:** when TP1 fills, the SL for that entry trails to breakeven (a real Binance stop-limit order), TP2 is re-placed for the full remaining qty, and any still-unfilled sibling entries from the same price range are cancelled.
- SL before TP1 is a **software stop** — checked every 60s against live price, not a resting exchange order.
