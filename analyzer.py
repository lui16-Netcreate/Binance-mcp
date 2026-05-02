"""
Phase 4 — Trade Pattern Analyzer

Reads trades.json and uses Claude to identify winning patterns
across confluence indicators (RSI, Fear & Greed, funding rate, etc.)

Usage:
    python analyzer.py              # analyze all trades
    python analyzer.py --days 7     # last 7 days only
"""
import os
import json
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
import anthropic

load_dotenv()

TRADES_LOG = Path(__file__).parent / "trades.json"


def load_trades(days: int = None) -> list[dict]:
    if not TRADES_LOG.exists():
        return []
    trades = json.loads(TRADES_LOG.read_text())
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        trades = [
            t for t in trades
            if datetime.fromisoformat(t["result"]["timestamp"]) > cutoff
        ]
    return trades


def format_trades_for_claude(trades: list[dict]) -> str:
    lines = []
    for i, t in enumerate(trades, 1):
        sig  = t["signal"]
        res  = t["result"]
        conf = t.get("confluence", {})
        ind  = conf.get("indicators", {})
        sent = conf.get("sentiment", {})
        fg   = sent.get("fear_greed", {})
        fund = sent.get("funding_rate", {})
        ls   = sent.get("long_short_ratio", {})

        lines.append(f"Trade {i}: {sig['symbol']} {sig['direction']}")
        lines.append(f"  Time: {res.get('timestamp', 'unknown')}")
        lines.append(f"  Entries: {sig['entries']}  SL: {sig['sl']}  TPs: {sig['tps']}")
        lines.append(f"  Orders placed: {len(res['placed_orders'])}  Errors: {res.get('errors', [])}")

        if ind:
            lines.append(
                f"  RSI(14): {ind.get('rsi_14')}  "
                f"SMA20: {ind.get('sma_20')}  SMA50: {ind.get('sma_50')}  "
                f"Volume spike: {ind.get('volume_spike')}x"
            )
        if fg:
            lines.append(f"  Fear&Greed: {fg.get('value')} — {fg.get('classification')}")
        if fund:
            lines.append(f"  Funding rate: {fund.get('funding_rate')}% ({fund.get('sentiment')})")
        if ls:
            lines.append(f"  Long/Short ratio: {ls.get('long_short_ratio')} ({ls.get('sentiment')})")

        fib = conf.get("fibonacci", {})
        if fib:
            price = ind.get("current_price")
            in_zone = fib.get("fib_0786", 0) <= (price or 0) <= fib.get("fib_0618", 0)
            lines.append(
                f"  Fib golden zone: {fib.get('fib_0786')} – {fib.get('fib_0618')}  "
                f"In zone: {in_zone}"
            )
        lines.append("")

    return "\n".join(lines)


def analyze(trades: list[dict]) -> str:
    if not trades:
        return "No trades to analyze."

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return "ANTHROPIC_API_KEY not set in .env"

    client = anthropic.Anthropic(api_key=api_key)
    trades_text = format_trades_for_claude(trades)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=[
            {
                "type": "text",
                "text": (
                    "You are a quantitative crypto trading analyst. You analyze trade logs "
                    "and identify patterns between market conditions and trade setups. "
                    "Be concise, specific, and actionable. Use numbers where possible. "
                    "Note: P&L data is not available yet — focus on entry confluence quality."
                ),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"Analyze these {len(trades)} trades and provide:\n"
                    "1. Most common confluence conditions at entry (RSI range, Fear&Greed range, funding bias)\n"
                    "2. Most traded symbols and any patterns there\n"
                    "3. Quality of setups — were entries in strong confluence zones?\n"
                    "4. Any red flags or weak setups to watch for\n"
                    "5. One sentence recommendation for what conditions to prioritize\n\n"
                    f"Trade data:\n{trades_text}"
                ),
            }
        ],
    )

    return response.content[0].text


def main():
    parser = argparse.ArgumentParser(description="Analyze trade patterns using Claude")
    parser.add_argument("--days", type=int, default=None, help="Analyze last N days only")
    args = parser.parse_args()

    trades = load_trades(days=args.days)
    if not trades:
        label = f"last {args.days} days" if args.days else "all time"
        print(f"No trades found ({label}).")
        return

    label = f"last {args.days} days" if args.days else "all time"
    print(f"Analyzing {len(trades)} trades ({label})...\n")
    print(analyze(trades))


if __name__ == "__main__":
    main()
