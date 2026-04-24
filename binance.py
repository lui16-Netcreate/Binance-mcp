"""
Binance public API helpers — no API key required.
"""
import requests

BASE = "https://api.binance.us/api/v3"


def get_price(symbol: str) -> dict:
    r = requests.get(f"{BASE}/ticker/price", params={"symbol": symbol.upper()}, timeout=10)
    r.raise_for_status()
    return r.json()


def get_candles(symbol: str, interval: str = "1h", limit: int = 50) -> list[dict]:
    r = requests.get(
        f"{BASE}/klines",
        params={"symbol": symbol.upper(), "interval": interval, "limit": limit},
        timeout=10,
    )
    r.raise_for_status()
    raw = r.json()
    return [
        {
            "open_time": c[0],
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[5]),
        }
        for c in raw
    ]


def get_24hr_stats(symbol: str = None) -> list[dict] | dict:
    params = {"symbol": symbol.upper()} if symbol else {}
    r = requests.get(f"{BASE}/ticker/24hr", params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def get_top_movers(quote: str = "USDT", top_n: int = 10) -> dict:
    all_tickers = get_24hr_stats()
    usdt_pairs = [
        t for t in all_tickers
        if t["symbol"].endswith(quote.upper()) and float(t["quoteVolume"]) > 0
    ]
    gainers = sorted(usdt_pairs, key=lambda t: float(t["priceChangePercent"]), reverse=True)[:top_n]
    losers = sorted(usdt_pairs, key=lambda t: float(t["priceChangePercent"]))[:top_n]
    return {"gainers": gainers, "losers": losers}


# ── Indicators ────────────────────────────────────────────────────────────────

def _sma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, period + 1):
        change = closes[-period - 1 + i] - closes[-period - 2 + i]
        (gains if change >= 0 else losses).append(abs(change))
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def compute_fibonacci(candles: list[dict]) -> dict:
    """
    Calculates Fibonacci retracement levels from the swing high/low
    across the provided candles. Golden zone = 0.618 to 0.786.
    """
    swing_high = max(c["high"] for c in candles)
    swing_low = min(c["low"] for c in candles)
    diff = swing_high - swing_low
    return {
        "swing_high": swing_high,
        "swing_low": swing_low,
        "fib_0500": round(swing_high - 0.500 * diff, 4),
        "fib_0618": round(swing_high - 0.618 * diff, 4),  # golden zone bottom
        "fib_0786": round(swing_high - 0.786 * diff, 4),  # golden zone top
    }


def get_fear_greed() -> dict:
    """Fetch the current Crypto Fear & Greed Index (0=Extreme Fear, 100=Extreme Greed)."""
    r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
    r.raise_for_status()
    d = r.json()["data"][0]
    return {
        "value": int(d["value"]),
        "classification": d["value_classification"],
    }


def get_funding_rate(symbol: str = "BTCUSDT") -> dict:
    """Fetch the current perpetual futures funding rate from OKX (positive=longs pay, negative=shorts pay)."""
    # Convert BTCUSDT → BTC-USDT-SWAP format for OKX
    base = symbol.upper().replace("USDT", "")
    inst_id = f"{base}-USDT-SWAP"
    r = requests.get(
        "https://www.okx.com/api/v5/public/funding-rate",
        params={"instId": inst_id},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()["data"][0]
    rate = float(data["fundingRate"])
    return {
        "symbol": symbol.upper(),
        "funding_rate": round(rate * 100, 4),
        "sentiment": "bearish" if rate > 0 else "bullish",
    }


def compute_indicators(candles: list[dict]) -> dict:
    closes = [c["close"] for c in candles]
    return {
        "rsi_14": _rsi(closes, 14),
        "sma_20": round(_sma(closes, 20), 4) if _sma(closes, 20) else None,
        "sma_50": round(_sma(closes, 50), 4) if _sma(closes, 50) else None,
        "current_price": closes[-1],
        "high_24h": max(c["high"] for c in candles[-24:]),
        "low_24h": min(c["low"] for c in candles[-24:]),
    }
