# server.py
import os, time, json, asyncio
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
import httpx

from orchestrator import handle_tv_signal, healthcheck as orch_health

# ---- 環境変数ロード ----
load_dotenv()
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
ALLOWED_TOKEN   = os.getenv("ALLOWED_WEBHOOK_TOKEN")

# ---- FastAPI アプリ定義 ----
app = FastAPI(title="AIりんご式 Webhook Server")

# ---- 通知（Discord送信） ----
async def discord(text: str):
    if not DISCORD_WEBHOOK:
        return
    async with httpx.AsyncClient(timeout=10) as cli:
        await cli.post(DISCORD_WEBHOOK, json={"content": text})

# ---- アラート連打防止（8秒以内は無視） ----
LAST_ALERT = {}  # key: (symbol, dir) -> timestamp

# ---- TradingView Webhook Payload ----
class TVPayload(BaseModel):
    secret: str
    symbol: str
    dir: str
    price: float
    ts: int

# ============================================================
# ✅ Keepalive & ヘルスチェック対応（Render/UptimeRobot用）
# ============================================================

@app.get("/")
async def root():
    return {"ok": True, "msg": "AIりんご式 Webhook is live", "hint": "POST /webhook/tv"}

@app.head("/")
async def root_head():
    return {}

@app.get("/health")
async def health():
    ok, detail = await orch_health()
    return {"ok": ok, "detail": detail}

@app.head("/health")
async def health_head():
    return {}

@app.get("/webhook")
async def webhook_get():
    return {"ok": True, "msg": "ping-keepalive"}

@app.head("/webhook")
async def webhook_head():
    return {}

@app.get("/webhook/ping")
async def webhook_ping():
    return {"ok": True, "msg": "pong"}

@app.head("/webhook/ping")
async def webhook_ping_head():
    return {}
# ============================================================


# ============================================================
# ✅ TradingView → Discord → AI 連携本体
# ============================================================
@app.post("/webhook/tv")
async def webhook_tv(req: Request):
    # JSON受信
    try:
        data = await req.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    # 型検証
    try:
        payload = TVPayload(**data)
    except Exception as e:
        raise HTTPException(400, f"Bad payload: {e}")

    # 認証（TradingViewの secret と照合）
    if payload.secret != (ALLOWED_TOKEN or ""):
        raise HTTPException(403, "Forbidden")

    # BUY/SELL 以外は無視
    if payload.dir not in ("BUY", "SELL"):
        return {"ok": True, "ignored": payload.dir}

    # 8秒デバウンス
    key = (payload.symbol, payload.dir)
    now = time.time()
    if key in LAST_ALERT and now - LAST_ALERT[key] < 8:
        return {"ok": True, "debounced": True}
    LAST_ALERT[key] = now

    # Discord通知
    await discord(f"📡 初動: {payload.symbol} {payload.dir} @ {payload.price:.2f}")

    # AIりんご式トレード処理へ渡す
    await handle_tv_signal(
        symbol=payload.symbol,
        direction=payload.dir,
        price=payload.price,
        ts=payload.ts
    )

    return {"ok": True, "msg": "AI process started"}
# ============================================================
