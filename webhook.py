"""
Webhook receiver — runs as a separate process.
TradingView alerts POST JSON to http://161.35.12.225/webhook
"""
from fastapi import FastAPI, Request, HTTPException
from db import init_db, insert_signal
import uvicorn

init_db()
app = FastAPI(title="TradingView Webhook Receiver")


@app.post("/webhook")
async def receive_signal(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    insert_signal(data)
    print(f"[signal] {data.get('symbol')} {data.get('action')} @ {data.get('price')}")
    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "running"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=80)
