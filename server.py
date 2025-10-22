# server.py
import os
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import asyncio

# ===== 基本設定 =====
load_dotenv()

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
ALLOWED_WEBHOOK_TOKEN = os.getenv("ALLOWED_WEBHOOK_TOKEN", "your_shared_secret")

app = FastAPI(title="AIりんご式Trading Webhook", version="1.0")

# ===== テスト用ルート =====
@app.get("/")
async def root():
    return {"ok": True, "msg": "AIりんご式TradingBot"}

@app.head("/")
async def head_root():
    return {}

@app.get("/diag")
async def diag():
    return {"ok": True, "detail": "Diagnostic endpoint OK"}

@app.head("/diag")
async def head_diag():
    return {}

@app.get("/test/discord")
async def test_discord():
    """Discord通知テスト"""
    if not DISCORD_WEBHOOK:
        return JSONResponse({"ok": False, "error": "DISCORD_WEBHOOK未設定"})
    async with httpx.AsyncClient() as client:
        await client.post(DISCORD_WEBHOOK, json={"content": "✅ Discord連携テスト成功！"})
    return {"ok": True, "msg": "Discordへ送信しました"}

# ===== TradingView Webhook =====
@app.post("/webhook/tv")
async def webhook_tv(request: Request):
    try:
        data = await request.json()
        token = data.get("secret")
        if token != ALLOWED_WEBHOOK_TOKEN:
            return JSONResponse({"ok": False, "error": "認証トークンが不正です"}, status_code=403)

        symbol = data.get("symbol", "Unknown")
        direction = data.get("dir", "N/A")
        price = data.get("price", "N/A")
        ts = data.get("ts", "N/A")

        message = f"📈 **{symbol}**\n方向: {direction}\n価格: {price}\n時刻: {ts}"

        async with httpx.AsyncClient() as client:
            await client.post(DISCORD_WEBHOOK, json={"content": message})

        return {"ok": True, "msg": "Discordへ送信完了", "symbol": symbol}

    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@app.get("/webhook/tv")
async def webhook_tv_get():
    return {"ok": True, "msg": "Webhook endpoint OK"}

@app.head("/webhook/tv")
async def webhook_tv_head():
    return {}
