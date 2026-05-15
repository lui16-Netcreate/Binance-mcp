"""
Phase 4 — Weekly Telegram Report

Reads trades.json, runs Claude pattern analysis, and sends a summary
to your Telegram. Run manually or schedule via Windows Task Scheduler.

Usage:
    python report.py            # last 7 days (default)
    python report.py --days 30  # last 30 days
"""
import os
import argparse
from datetime import datetime, timezone
from dotenv import load_dotenv
import requests
from analyzer import load_trades, analyze

load_dotenv()


def send_telegram(msg: str):
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram credentials missing — report printed above only.")
        return
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
        timeout=10,
    )


def _pnl_stats(trades: list[dict]) -> dict:
    closed = [t for t in trades if t.get("trade_closed") and t.get("total_pnl_usdt") is not None]
    if not closed:
        return {}
    pnls  = [t["total_pnl_usdt"] for t in closed]
    wins  = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    return {
        "n":          len(closed),
        "total":      round(sum(pnls), 2),
        "win_rate":   round(len(wins) / len(pnls) * 100, 1),
        "avg_win":    round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss":   round(sum(losses) / len(losses), 2) if losses else 0,
        "best":       round(max(pnls), 2),
        "worst":      round(min(pnls), 2),
    }


def build_report(trades: list[dict], days: int, analysis: str) -> str:
    now = datetime.now(timezone.utc)

    total  = len(trades)
    longs  = sum(1 for t in trades if t["signal"]["direction"] == "LONG")
    shorts = sum(1 for t in trades if t["signal"]["direction"] == "SHORT")
    errors = sum(1 for t in trades if t["result"].get("errors"))

    # Symbol breakdown
    symbols: dict[str, int] = {}
    for t in trades:
        s = t["signal"]["symbol"]
        symbols[s] = symbols.get(s, 0) + 1
    top_symbols = sorted(symbols.items(), key=lambda x: x[1], reverse=True)[:5]
    symbols_str = " · ".join(f"{s} ({n})" for s, n in top_symbols)

    # Avg confluence
    rsis = [
        t["confluence"]["indicators"]["rsi_14"]
        for t in trades
        if t.get("confluence", {}).get("indicators", {}).get("rsi_14")
    ]
    fgs = [
        t["confluence"]["sentiment"]["fear_greed"]["value"]
        for t in trades
        if t.get("confluence", {}).get("sentiment", {}).get("fear_greed", {}).get("value")
    ]

    avg_rsi = round(sum(rsis) / len(rsis), 1) if rsis else "N/A"
    avg_fg  = round(sum(fgs) / len(fgs), 1) if fgs else "N/A"

    # P&L section
    pnl = _pnl_stats(trades)
    if pnl:
        sign = "+" if pnl["total"] >= 0 else ""
        pnl_block = (
            f"*Realized P&L ({pnl['n']} closed trades):*\n"
            f"  Total: `{sign}{pnl['total']:.2f} USDT`\n"
            f"  Win rate: `{pnl['win_rate']}%`\n"
            f"  Avg win: `+{pnl['avg_win']:.2f} USDT`  |  Avg loss: `{pnl['avg_loss']:.2f} USDT`\n"
            f"  Best: `+{pnl['best']:.2f}`  |  Worst: `{pnl['worst']:.2f}`\n\n"
        )
    else:
        pnl_block = "*Realized P&L:* No closed trades yet.\n\n"

    report = (
        f"📊 *Trading Report — Last {days} Days*\n"
        f"_{now.strftime('%b %d, %Y  %H:%M UTC')}_\n\n"
        f"*Signals executed:* {total}\n"
        f"  • LONG: {longs}\n"
        f"  • SHORT skipped: {shorts}\n"
        f"  • With errors: {errors}\n\n"
        f"{pnl_block}"
        f"*Top symbols:*\n  {symbols_str}\n\n"
        f"*Avg confluence at entry:*\n"
        f"  RSI(14): `{avg_rsi}`\n"
        f"  Fear & Greed: `{avg_fg}/100`\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"*🤖 Claude Analysis:*\n\n{analysis}"
    )

    return report


def main():
    parser = argparse.ArgumentParser(description="Generate and send weekly trading report")
    parser.add_argument("--days", type=int, default=7, help="Days to include (default: 7)")
    args = parser.parse_args()

    trades = load_trades(days=args.days)

    if not trades:
        msg = f"📊 *Trading Report — Last {args.days} Days*\n\nNo trades executed in this period."
        print(msg)
        send_telegram(msg)
        return

    print(f"Generating report for {len(trades)} trades over the last {args.days} days...")
    analysis = analyze(trades)
    report   = build_report(trades, args.days, analysis)

    print("\n" + report)
    send_telegram(report)
    print("\n✅ Report sent to Telegram.")


if __name__ == "__main__":
    main()
