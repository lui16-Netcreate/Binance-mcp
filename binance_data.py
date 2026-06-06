"""
Binance public API helpers — no API key required.
"""
import requests

BASE  = "https://api.binance.us/api/v3"
FAPI  = "https://fapi.binance.com"        # Binance global futures — public endpoints, no key needed


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


def get_top_volume(quote: str = "USDT", top_n: int = 10) -> list:
    all_tickers = get_24hr_stats()
    usdt_pairs = [
        t for t in all_tickers
        if t["symbol"].endswith(quote.upper()) and float(t["quoteVolume"]) > 0
    ]
    top = sorted(usdt_pairs, key=lambda t: float(t["quoteVolume"]), reverse=True)[:top_n]
    return [
        {
            "symbol":       t["symbol"],
            "price":        float(t["lastPrice"]),
            "change_pct":   round(float(t["priceChangePercent"]), 2),
            "quote_volume": round(float(t["quoteVolume"])),
        }
        for t in top
    ]


# ── Indicators ────────────────────────────────────────────────────────────────

def _sma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _ema(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    k   = 2 / (period + 1)
    ema = sum(closes[:period]) / period  # seed with SMA of first N candles
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return round(ema, 4)


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    # Seed with simple average of first `period` changes
    avg_gain = sum(max(c, 0) for c in changes[:period]) / period
    avg_loss = sum(abs(min(c, 0)) for c in changes[:period]) / period
    # Wilder's smoothing over remaining candles
    for change in changes[period:]:
        avg_gain = (avg_gain * (period - 1) + max(change, 0)) / period
        avg_loss = (avg_loss * (period - 1) + abs(min(change, 0))) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 2)


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
    """Fetch the current perpetual futures funding rate from Binance (positive=longs pay, negative=shorts pay)."""
    r = requests.get(
        f"{FAPI}/fapi/v1/premiumIndex",
        params={"symbol": symbol.upper()},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    rate = float(data["lastFundingRate"])
    return {
        "symbol": symbol.upper(),
        "funding_rate": round(rate * 100, 4),
        "sentiment": "bearish" if rate > 0 else "bullish",
    }


def get_long_short_ratio(symbol: str = "BTCUSDT", period: str = "1h") -> dict:
    """Long/short ratio from Binance futures — ratio > 1 means more longs, < 1 means more shorts."""
    r = requests.get(
        f"{FAPI}/futures/data/globalLongShortAccountRatio",
        params={"symbol": symbol.upper(), "period": period, "limit": 1},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    if not data:
        return {"symbol": symbol.upper(), "long_short_ratio": None, "sentiment": "unknown"}
    ratio = float(data[0]["longShortRatio"])
    return {
        "symbol": symbol.upper(),
        "long_short_ratio": round(ratio, 4),
        "sentiment": "more longs" if ratio > 1 else "more shorts",
    }


def get_open_interest(symbol: str = "BTCUSDT") -> dict:
    """Open interest from Binance futures — rising OI with falling price = bearish pressure."""
    r = requests.get(
        f"{FAPI}/futures/data/openInterestHist",
        params={"symbol": symbol.upper(), "period": "1h", "limit": 1},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    if not data:
        return {"symbol": symbol.upper(), "open_interest": None}
    latest = data[0]
    return {
        "symbol": symbol.upper(),
        "open_interest": float(latest["sumOpenInterest"]),
        "open_interest_usd": float(latest["sumOpenInterestValue"]),
    }


def get_news_sentiment(symbol: str = "BTC") -> dict:
    """Fetch latest crypto news sentiment from CryptoPanic (no API key for basic feed)."""
    base = symbol.upper().replace("USDT", "")
    r = requests.get(
        "https://cryptopanic.com/api/free/v1/posts/",
        params={"auth_token": "free", "currencies": base, "filter": "hot"},
        timeout=10,
    )
    r.raise_for_status()
    results = r.json().get("results", [])[:10]
    bullish = sum(1 for p in results if p.get("votes", {}).get("positive", 0) > p.get("votes", {}).get("negative", 0))
    bearish = len(results) - bullish
    return {
        "symbol": base,
        "articles_checked": len(results),
        "bullish_articles": bullish,
        "bearish_articles": bearish,
        "sentiment": "bullish" if bullish > bearish else "bearish" if bearish > bullish else "neutral",
    }


def get_full_sentiment(symbol: str = "BTCUSDT") -> dict:
    """Snapshot of all sentiment indicators for a symbol at a given moment."""
    result = {"symbol": symbol.upper()}

    try:
        result["fear_greed"] = get_fear_greed()
    except Exception as e:
        result["fear_greed"] = {"error": str(e)}

    try:
        result["funding_rate"] = get_funding_rate(symbol)
    except Exception as e:
        result["funding_rate"] = {"error": str(e)}

    try:
        result["long_short_ratio"] = get_long_short_ratio(symbol)
    except Exception as e:
        result["long_short_ratio"] = {"error": str(e)}

    try:
        result["open_interest"] = get_open_interest(symbol)
    except Exception as e:
        result["open_interest"] = {"error": str(e)}

    try:
        base = symbol.upper().replace("USDT", "")
        result["news_sentiment"] = get_news_sentiment(base)
    except Exception as e:
        result["news_sentiment"] = {"error": str(e)}

    return result


def compute_indicators(candles: list[dict]) -> dict:
    closes  = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]
    avg_volume = sum(volumes[:-1]) / max(len(volumes) - 1, 1)
    return {
        "rsi_14":        _rsi(closes, 14),
        "sma_20":        round(_sma(closes, 20), 4) if _sma(closes, 20) else None,
        "sma_50":        round(_sma(closes, 50), 4) if _sma(closes, 50) else None,
        "ema_20":        _ema(closes, 20),
        "ema_50":        _ema(closes, 50),
        "ema_200":       _ema(closes, 200),
        "current_price": closes[-1],
        "high_24h":      max(c["high"] for c in candles[-24:]),
        "low_24h":       min(c["low"] for c in candles[-24:]),
        "volume_current":  round(volumes[-1], 2),
        "volume_avg":      round(avg_volume, 2),
        "volume_spike":    round(volumes[-1] / avg_volume, 2) if avg_volume > 0 else None,
        "volume_total_24h": round(sum(volumes[-24:]), 2),
    }
