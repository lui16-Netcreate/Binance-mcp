"""
MCP server — registered in Claude Desktop / Claude Code.
Tools:
  - TradingView alert signals (stored by webhook.py)
  - Live Binance market data (no API key needed)
"""
import json
from mcp.server.fastmcp import FastMCP
from db import init_db, get_signals, clear_signals
import binance_data as binance

init_db()
mcp = FastMCP("TradingView Signals")


# ── TradingView alert tools ───────────────────────────────────────────────────

@mcp.tool()
def get_latest_signals(limit: int = 10) -> str:
    """Return the most recent TradingView signals across all symbols."""
    signals = get_signals(limit=limit)
    if not signals:
        return "No signals received yet. Make sure the webhook server is running and TradingView alerts are configured."
    return json.dumps(signals, indent=2)


@mcp.tool()
def get_signals_for_symbol(symbol: str, limit: int = 10) -> str:
    """Return recent TradingView signals for a specific ticker, e.g. BTCUSDT."""
    signals = get_signals(symbol=symbol, limit=limit)
    if not signals:
        return f"No signals found for {symbol.upper()}."
    return json.dumps(signals, indent=2)


@mcp.tool()
def get_signals_by_action(action: str, limit: int = 10) -> str:
    """Return TradingView signals filtered by action. Use 'BUY' or 'SELL'."""
    signals = get_signals(action=action, limit=limit)
    if not signals:
        return f"No {action.upper()} signals found."
    return json.dumps(signals, indent=2)


@mcp.tool()
def clear_all_signals() -> str:
    """Delete all stored TradingView signals from the database."""
    clear_signals()
    return "All signals cleared."


# ── Binance live market data tools ───────────────────────────────────────────

@mcp.tool()
def get_price(symbol: str) -> str:
    """Get the current live price for a crypto pair, e.g. BTCUSDT, ETHUSDT."""
    try:
        data = binance.get_price(symbol)
        return f"{data['symbol']}: ${float(data['price']):,.4f}"
    except Exception as e:
        return f"Error fetching price for {symbol}: {e}"


@mcp.tool()
def get_candles(symbol: str, interval: str = "1h", limit: int = 50) -> str:
    """
    Fetch OHLCV candle data for a symbol.
    Intervals: 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w
    """
    try:
        candles = binance.get_candles(symbol, interval, limit)
        return json.dumps(candles, indent=2)
    except Exception as e:
        return f"Error fetching candles for {symbol}: {e}"


@mcp.tool()
def get_top_movers(top_n: int = 10) -> str:
    """Return the top gaining and losing USDT pairs in the last 24 hours."""
    try:
        movers = binance.get_top_movers(top_n=top_n)
        gainers = [
            f"{t['symbol']}: +{float(t['priceChangePercent']):.2f}%"
            for t in movers["gainers"]
        ]
        losers = [
            f"{t['symbol']}: {float(t['priceChangePercent']):.2f}%"
            for t in movers["losers"]
        ]
        return "TOP GAINERS:\n" + "\n".join(gainers) + "\n\nTOP LOSERS:\n" + "\n".join(losers)
    except Exception as e:
        return f"Error fetching top movers: {e}"


@mcp.tool()
def analyze_symbol(symbol: str, interval: str = "1h") -> str:
    """
    Fetch candles for a symbol and compute RSI(14), SMA(20), SMA(50),
    24h high/low, and current price. Use this as the basis for analysis.
    Intervals: 1m, 5m, 15m, 30m, 1h, 4h, 1d
    """
    try:
        candles = binance.get_candles(symbol, interval, limit=100)
        indicators = binance.compute_indicators(candles)
        stats = binance.get_24hr_stats(symbol)
        result = {
            "symbol": symbol.upper(),
            "interval": interval,
            "indicators": indicators,
            "24h_change_pct": float(stats["priceChangePercent"]),
            "24h_volume_usdt": float(stats["quoteVolume"]),
        }
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error analyzing {symbol}: {e}"


@mcp.tool()
def get_fear_greed_index() -> str:
    """Get the current Crypto Fear & Greed Index (0=Extreme Fear, 100=Extreme Greed)."""
    try:
        data = binance.get_fear_greed()
        return f"Fear & Greed: {data['value']}/100 — {data['classification']}"
    except Exception as e:
        return f"Error fetching Fear & Greed index: {e}"


@mcp.tool()
def get_funding_rate(symbol: str = "BTCUSDT") -> str:
    """Get the current perpetual futures funding rate for a symbol from Bybit."""
    try:
        data = binance.get_funding_rate(symbol)
        direction = "longs paying shorts (bearish pressure)" if data["funding_rate"] > 0 else "shorts paying longs (bullish pressure)"
        return f"{data['symbol']} funding rate: {data['funding_rate']}% — {direction}"
    except Exception as e:
        return f"Error fetching funding rate for {symbol}: {e}"


@mcp.tool()
def get_long_short_ratio(symbol: str = "BTCUSDT", period: str = "1h") -> str:
    """Get the long/short ratio for a symbol. Ratio > 1 = more longs, < 1 = more shorts."""
    try:
        data = binance.get_long_short_ratio(symbol, period)
        return f"{data['symbol']} long/short ratio: {data['long_short_ratio']} — {data['sentiment']}"
    except Exception as e:
        return f"Error fetching long/short ratio for {symbol}: {e}"


@mcp.tool()
def get_open_interest(symbol: str = "BTCUSDT") -> str:
    """Get current open interest for a perpetual futures symbol."""
    try:
        data = binance.get_open_interest(symbol)
        return f"{data['symbol']} open interest: {data['open_interest']:,.0f} contracts (${data['open_interest_usd']:,.0f})"
    except Exception as e:
        return f"Error fetching open interest for {symbol}: {e}"


@mcp.tool()
def get_news_sentiment(symbol: str = "BTC") -> str:
    """Get news sentiment for a crypto asset from CryptoPanic (bullish/bearish/neutral)."""
    try:
        data = binance.get_news_sentiment(symbol)
        return (
            f"{data['symbol']} news sentiment: {data['sentiment'].upper()} "
            f"({data['bullish_articles']} bullish / {data['bearish_articles']} bearish "
            f"out of {data['articles_checked']} articles)"
        )
    except Exception as e:
        return f"Error fetching news sentiment for {symbol}: {e}"


@mcp.tool()
def get_market_sentiment(symbol: str = "BTCUSDT") -> str:
    """
    Full sentiment snapshot for a symbol: Fear & Greed, funding rate,
    long/short ratio, open interest, and news sentiment.
    """
    try:
        data = binance.get_full_sentiment(symbol)
        return json.dumps(data, indent=2)
    except Exception as e:
        return f"Error fetching sentiment: {e}"


if __name__ == "__main__":
    mcp.run()
