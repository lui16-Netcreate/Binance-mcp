# Crypto Auto-Trader System — Project Diagram

```
╔══════════════════════════════════════════════════════════════════╗
║                    CRYPTO AUTO-TRADER SYSTEM                     ║
╚══════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PHASE 1 — FOUNDATION                  ✅ COMPLETE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ┌─────────────┐    ┌─────────────┐    ┌──────────────────┐
  │  binance.py │    │  server.py  │    │ confluence_       │
  │             │    │             │    │ backtest.pine     │
  │ • Price     │    │ MCP tools   │    │                   │
  │ • Candles   │    │ for Claude  │    │ TradingView       │
  │ • RSI/Fib   │    │ Desktop     │    │ backtesting       │
  │ • Fear&Greed│    │             │    │                   │
  │ • Funding   │    │             │    │                   │
  │ • L/S Ratio │    │             │    │                   │
  │ • OI        │    │             │    │                   │
  │ • News      │    │             │    │                   │
  └─────────────┘    └─────────────┘    └──────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PHASE 2 — CONFLUENCE MONITOR          ✅ COMPLETE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ┌──────────────────────────────────────────────┐
  │                  monitor.py                  │
  │                                              │
  │  Runs every 15 min per symbol                │
  │                                              │
  │  Checks: RSI · Fib · Fear&Greed ·            │
  │          Funding · L/S Ratio · OI · News     │
  │                                              │
  │  If N conditions met:                        │
  │         ↓                                    │
  │  📱 Telegram alert → YOU                     │
  └──────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PHASE 3 — SIGNAL AUTO-EXECUTOR        🔴 BLOCKED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  BLOCKER 1: my.telegram.org cooldown
  BLOCKER 2: Binance US MFA reset pending

  ┌─────────────────────────┐
  │  "Crypto Chiefs Premium" │  ← Private Telegram group
  │   trading signals group  │
  └────────────┬────────────┘
               │ Telethon userbot
               ▼
  ┌─────────────────────────┐
  │       trader.py         │
  │                         │
  │  1. Parse signal        │
  │     • Symbol            │
  │     • Entry range → 3   │
  │       limit orders      │
  │     • SL (fixed)        │
  │     • TP1/2/3/4         │
  │                         │
  │  2. Execute on exchange │
  │     • 1% portfolio each │
  │     • 5x leverage       │
  │     • TP split:         │
  │       40/30/20/10%      │
  │                         │
  │  3. Snapshot sentiment  │
  │     at signal time      │
  │                         │
  │  4. Log to trades.json  │
  └────────────┬────────────┘
               │
               ▼
  📱 Telegram confirmation → YOU
  "✅ ETH LONG opened: 3 orders placed"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PHASE 4 — TRADE LOG & INTELLIGENCE    ⬜ PLANNED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ┌──────────────────────────────────────────────┐
  │                 trades.json                  │
  │                                              │
  │  Per trade:                                  │
  │  • Signal received + timestamp               │
  │  • Orders placed (entry, DCA fills)          │
  │  • TP/SL status (hit / pending / cancelled)  │
  │  • P&L                                       │
  │  • Confluence snapshot at signal time:       │
  │    RSI · Fib · Fear&Greed · Funding ·        │
  │    L/S Ratio · OI · News · Volume spike      │
  └──────────────────────────────────────────────┘
               │
               ▼
  Claude analyzes log → finds winning patterns
  "RSI<30 + Fib + Fear&Greed<25 = 80% win rate"
               │
               ▼
  📱 Weekly Telegram summary report → YOU

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 PHASE 5 — WEB DASHBOARD               ⬜ PLANNED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ┌──────────────────────────────────────────────┐
  │              dashboard (HTML/JS)             │
  │                                              │
  │  • Open trades + live P&L                   │
  │  • Closed trades history                    │
  │  • Equity curve chart                       │
  │  • Win rate by confluence combination       │
  │  • Best performing symbols                  │
  └──────────────────────────────────────────────┘
```

## Status Summary

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Foundation — binance.py, server.py, Pine Script | ✅ Complete |
| 2 | Confluence monitor — Telegram alerts on RSI+Fib+sentiment | ✅ Complete |
| 3 | Signal auto-executor — Telethon + trader.py + exchange | 🔴 Blocked |
| 4 | Trade log + Claude intelligence + weekly reports | ⬜ Planned |
| 5 | Web dashboard | ⬜ Planned |

## Blockers (Phase 3)
- **my.telegram.org** — locked out from too many login attempts (retry later)
- **Binance US API keys** — MFA reset in progress, waiting for confirmation

## Trading Ruleset
- Entry: 3 limit orders at top / mid / bottom of signal range
- Position size: 3% of USDT/USDC portfolio (1% per entry)
- Leverage: 5x
- SL: always use fixed price from signal
- TP split: TP1 40% / TP2 30% / TP3 20% / TP4 10%
