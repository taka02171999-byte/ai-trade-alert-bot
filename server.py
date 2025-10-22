# server.py
import os
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
ALLOWED_WEBHOOK_TOKEN = os.getenv("ALLOWED_WEBHOOK_TOKEN", "your_shared_secret")

app = FastAPI(title="AI-ringo Webhook")

# ---------- 基本ヘルス ----------
@app.get("/")
async def root():
    return {"ok": True, "service": "ai-ringo"}

@app.head("/")
async def head_root():
    return {}

@app.get("/health")
async def health():
    return {"ok": True}

@app.head("/health")
async def head_health():
    return {}

# ---------- keepalive（UptimeRobotなど） ----------
@app.get("/webhook")
async def webhook_get():
    return {"ok": True, "msg": "ping-keepalive"}

@app.head("/webhook")
async def webhook_head():
    return {}

# ---------- 診断・テスト ----------
@app.get("/diag")
async def diag():
    # secret は先頭だけマスク
    token = ALLOWED_WEBHOOK_TOKEN or ""
    return {
        "has_discord_webhook": bool(DISCORD_WEBHOOK),
        "token_prefix": (token[:2] + "***") if token else None,
    }

@app.get("/test/discord")
async def test_discord():
    if not DISCORD_WEBHOOK:
        return JSONResponse({"ok": False, "error": "DISCORD_WEBHOOK is empty"}, status_code=500)
    async with httpx.AsyncClient(timeout=10) as cli:
        await cli.post(DISCORD_WEBHOOK, json={"content": "✅ /test/discord OK"})
    return {"ok": True}

# ---------- TradingView 受信 ----------
@app.post("/webhook/tv")
async def webhook_tv(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    if data.get("secret") != (ALLOWED_WEBHOOK_TOKEN or ""):
        return JSONResponse({"ok": False, "error": "Forbidden"}, status_code=403)

    symbol = data.get("symbol", "UNKNOWN")
    direction = data.get("dir", "N/A")
    price = data.get("price", "N/A")
    ts = data.get("ts", "N/A")

    # まずDiscordに通知（切り分け用）
    if DISCORD_WEBHOOK:
        msg = f"📡 初動: {symbol} {direction} @ {price} (ts={ts})"
        async with httpx.AsyncClient(timeout=10) as cli:
            await cli.post(DISCORD_WEBHOOK, json={"content": msg})

    return {"ok": True}
