# server.py
import os, time, json, asyncio
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
import httpx

from orchestrator import handle_tv_signal, healthcheck as orch_health

# ---- env ----
load_dotenv()
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
ALLOWED_TOKEN   = os.getenv("ALLOWED_WEBHOOK_TOKEN")

# ---- app ----
app = FastAPI(title="AIりんご式 Webhook")

# 同一(symbol, dir)の連打を抑止（8秒）
LAST_ALERT = {}  # key: (symbol, dir) -> ts

class TVPayload(BaseModel):
    secret: str
    symbol: str          # 例: "7203.T"
    dir: str             # "BUY" / "SELL" （TP/SLが来ても無視する方針）
    price: float
    ts: int              # Unix ms or s（Pineのtimeをそのまま）

async def discord(text: str):
    if not DISCORD_WEBHOOK:
        return
    async with httpx.AsyncClient(timeout=10) as cli:
        await cli.post(DISCORD_WEBHOOK, json={"content": text})

# ---------- Keepalive/Health（RenderやUptimeRobot対策） ----------
@app.get("/")
async def root():
    return {"ok": True, "service": "ai-ringo", "hint": "POST /webhook/tv"}

@app.get("/webhook")
async def webhook_get():
    return {"ok": True, "msg": "ping-keepalive"}

@app.head("/webhook")
async def webhook_head():
    return {}  # 200だけ返す
# ------------------------------------------------------------------

@app.get("/health")
async def health():
    ok, detail = await orch_health()
    return {"ok": ok, "detail": detail}

@app.post("/webhook/tv")
async def webhook_tv(req: Request):
    # JSON必須（Pineのalert()が送るJSON）
    try:
        data = await req.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    # バリデーション
    try:
        payload = TVPayload(**data)
    except Exception as e:
        raise HTTPException(400, f"Bad payload: {e}")

    # 認証
    if payload.secret != (ALLOWED_TOKEN or ""):
        raise HTTPException(403, "Forbidden")

    # 初動のみ扱う（TP/SLが来てもAI側で最終判断するので無視）
    if payload.dir not in ("BUY", "SELL"):
        return {"ok": True, "ignored": payload.dir}

    # 8秒デバウンス
    key = (payload.symbol, payload.dir)
    now = time.time()
    if key in LAST_ALERT and now - LAST_ALERT[key] < 8:
        return {"ok": True, "debounced": True}
    LAST_ALERT[key] = now

    # 通知＆AI起動
    await discord(f"📡 初動: {payload.symbol} {payload.dir} @ {payload.price:.2f}")
    await handle_tv_signal(
        symbol=payload.symbol,
        direction=payload.dir,
        price=payload.price,
        ts=payload.ts
    )
    return {"ok": True}
