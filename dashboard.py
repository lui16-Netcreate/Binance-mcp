"""
Phase 5 — Web Dashboard

Serves a live trading dashboard at http://localhost:8080
Shows trade history, confluence data at entry, and live prices.

Usage:
    python dashboard.py         # runs on port 8080
    python dashboard.py 9090    # custom port
"""
import json
import sys
import secrets
import os
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, JSONResponse
from dotenv import load_dotenv
import uvicorn
import binance_data

load_dotenv()

TRADES_LOG = Path(__file__).parent / "trades.json"

app      = FastAPI(title="Crypto Auto-Trader Dashboard")
security = HTTPBasic()


def require_auth(credentials: HTTPBasicCredentials = Depends(security)):
    valid_user = secrets.compare_digest(
        credentials.username.encode(), os.getenv("DASHBOARD_USER", "admin").encode()
    )
    valid_pass = secrets.compare_digest(
        credentials.password.encode(), os.getenv("DASHBOARD_PASS", "changeme").encode()
    )
    if not (valid_user and valid_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


@app.get("/", response_class=HTMLResponse)
async def index(_: None = Depends(require_auth)):
    return (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")


@app.get("/api/trades")
async def api_trades(_: None = Depends(require_auth)):
    if not TRADES_LOG.exists():
        return JSONResponse([])
    trades = json.loads(TRADES_LOG.read_text())

    symbols = list({t["signal"]["symbol"] for t in trades})
    prices  = {}
    for sym in symbols:
        try:
            prices[sym] = float(binance_data.get_price(sym)["price"])
        except Exception:
            prices[sym] = None

    for t in trades:
        t["current_price"] = prices.get(t["signal"]["symbol"])

    return JSONResponse(list(reversed(trades)))


def _pnl_stats(trades: list, source: str = None) -> dict:
    closed = [
        t for t in trades
        if t.get("trade_closed") and t.get("total_pnl_usdt") is not None
        and (source is None or t.get("source", "telegram") == source)
    ]
    if not closed:
        return {"n": 0, "total": 0, "win_rate": 0}
    pnls = [t["total_pnl_usdt"] for t in closed]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "n":        len(closed),
        "total":    round(sum(pnls), 2),
        "win_rate": round(wins / len(pnls) * 100, 1),
    }


@app.get("/api/summary")
async def api_summary(_: None = Depends(require_auth)):
    if not TRADES_LOG.exists():
        return JSONResponse({"total": 0, "longs": 0, "shorts": 0, "errors": 0,
                             "symbols": [], "avg_rsi": None, "avg_fg": None,
                             "rsi_values": [], "fg_values": [],
                             "pnl": {"all": {}, "telegram": {}, "manual": {}}})

    trades = json.loads(TRADES_LOG.read_text())

    symbols: dict[str, int] = {}
    for t in trades:
        s = t["signal"]["symbol"]
        symbols[s] = symbols.get(s, 0) + 1

    longs  = sum(1 for t in trades if t["signal"]["direction"] == "LONG")
    shorts = sum(1 for t in trades if t["signal"]["direction"] == "SHORT")
    errors = sum(1 for t in trades if t["result"].get("errors"))

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

    return JSONResponse({
        "total":      len(trades),
        "longs":      longs,
        "shorts":     shorts,
        "errors":     errors,
        "symbols":    sorted(symbols.items(), key=lambda x: x[1], reverse=True),
        "avg_rsi":    round(sum(rsis) / len(rsis), 1) if rsis else None,
        "avg_fg":     round(sum(fgs) / len(fgs), 1) if fgs else None,
        "rsi_values": rsis,
        "fg_values":  fgs,
        "pnl": {
            "all":      _pnl_stats(trades),
            "telegram": _pnl_stats(trades, "telegram"),
            "manual":   _pnl_stats(trades, "manual"),
        },
    })


@app.get("/api/top-volume")
async def api_top_volume(_: None = Depends(require_auth)):
    try:
        return JSONResponse(binance_data.get_top_volume())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    print(f"Dashboard → http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
