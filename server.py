# server.py
import os, time, json, asyncio
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
import httpx

from orchestrator import handle_tv_signal, healthcheck as orch_health

load_dotenv()
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
ALLOWED_TOKEN   = os.getenv("ALLOWED_WEBHOOK_TOKEN")

app = FastAPI(title="AIりんご式 Webhook")
LAST_ALERT = {}  # (symbol, dir) -> ts（8秒デバウンス）

class TVPayload(BaseModel):
    secret: str
    symbol: str
    dir: str   # "BUY" / "SELL" / "TP" / "SL" も来てもOK
    price: float
    ts: int

async def discord(text: str):
    if not DISCORD_WEBHOOK: return
    async with httpx.AsyncClient(timeout=10) as cli:
        await cli.post(DISCORD_WEBHOOK, json={"content": text})

@app.get("/health")
async def health():
    ok, detail = await orch_health()
    return {"ok": ok, "detail": detail}

@app.post("/webhook/tv")
async def webhook_tv(req: Request):
    try:
        data = await req.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    try:
        payload = TVPayload(**data)
    except Exception as e:
        raise HTTPException(400, f"Bad payload: {e}")

    if payload.secret != (ALLOWED_TOKEN or ""):
        raise HTTPException(403, "Forbidden")

    # Pine側からTP/SLが来ても、AI側で最終判断するのでここでは初動のみ扱う
    if payload.dir not in ("BUY", "SELL"):
        return {"ok": True, "ignored": payload.dir}

    key = (payload.symbol, payload.dir)
    now = time.time()
    if key in LAST_ALERT and now - LAST_ALERT[key] < 8:
        return {"ok": True, "debounced": True}
    LAST_ALERT[key] = now

    # 入口 → オーケストレータへ
    await discord(f"📡 初動: {payload.symbol} {payload.dir} @ {payload.price:.2f}")
    await handle_tv_signal(symbol=payload.symbol, direction=payload.dir, price=payload.price, ts=payload.ts)
    return {"ok": True}
