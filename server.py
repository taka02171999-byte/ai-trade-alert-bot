# server.py — Webhook受信→AI起動→Discord通知（診断付き）
import os, time, json, socket
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from orchestrator import handle_tv_signal, healthcheck as orch_health

load_dotenv()
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "").strip()
ALLOWED_WEBHOOK_TOKEN = os.getenv("ALLOWED_WEBHOOK_TOKEN", "your_shared_secret").strip()

app = FastAPI(title="AI-ringo Webhook", version="1.0")

async def send_discord(text: str):
    if not DISCORD_WEBHOOK:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            await cli.post(DISCORD_WEBHOOK, json={"content": text})
    except Exception as e:
        print(f"[discord] send failed: {e}")

# ---- root
@app.get("/")
async def root():
    return {"ok": True, "service": "ai-ringo"}

@app.head("/")
async def head_root():
    return {}

# ---- health
@app.get("/health")
async def health():
    ok, detail = await orch_health()
    return {"ok": ok, "detail": detail}

@app.head("/health")
async def head_health():
    return {}

# ---- keepalive
@app.get("/webhook")
async def webhook_get():
    return {"ok": True, "msg": "ping-keepalive"}

@app.head("/webhook")
async def webhook_head():
    return {}

# ---- diag & test
@app.get("/diag")
async def diag():
    token = ALLOWED_WEBHOOK_TOKEN or ""
    return {
        "ok": True,
        "has_discord_webhook": bool(DISCORD_WEBHOOK),
        "token_prefix": (token[:2] + "***") if token else None,
        "server_hostname": socket.gethostname(),
    }

@app.get("/test/discord")
async def test_discord():
    await send_discord("✅ Discord連携テスト成功！")
    return {"ok": True}

# ---- echo
@app.post("/webhook/echo")
async def webhook_echo(req: Request):
    try:
        data = await req.json()
    except Exception:
        data = {"raw": (await req.body()).decode("utf-8", errors="ignore")}
    await send_discord(f"🪞 ECHO: ```{json.dumps(data)[:1800]}```")
    return {"ok": True}

# ---- TradingView webhook
LAST_ALERT = {}

@app.post("/webhook/tv")
async def webhook_tv(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    if (data.get("secret") or "").strip() != ALLOWED_WEBHOOK_TOKEN:
        await send_discord("🔒 認証失敗（secret不一致）")
        return JSONResponse({"ok": False, "error": "Forbidden"}, status_code=403)

    symbol = str(data.get("symbol", "UNKNOWN"))
    direction = str(data.get("dir", "N/A")).upper()
    price = data.get("price", "N/A")
    ts = data.get("ts", 0)

    await send_discord(f"📡 初動: {symbol} {direction} @ {price} (ts={ts})")

    key, now = (symbol, direction), time.time()
    if key in LAST_ALERT and now - LAST_ALERT[key] < 8:
        return {"ok": True, "debounced": True}
    LAST_ALERT[key] = now

    try:
        p_val = float(price) if isinstance(price, (int, float, str)) else None
        t_val = int(ts) if ts else 0
        await handle_tv_signal(symbol=symbol, direction=direction, price=p_val, ts=t_val)
    except Exception as e:
        await send_discord(f"⚠️ orchestrator error: {e}")

    return {"ok": True}

@app.get("/webhook/tv")
async def webhook_tv_get():
    return {"ok": True, "msg": "Webhook endpoint OK"}

@app.head("/webhook/tv")
async def webhook_tv_head():
    return {}
