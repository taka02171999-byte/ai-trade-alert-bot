# server.py (debug build)
import os, time, asyncio, json
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
import httpx

from orchestrator import handle_tv_signal, healthcheck as orch_health

load_dotenv()
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")          # ← DiscordのURL
ALLOWED_TOKEN   = os.getenv("ALLOWED_WEBHOOK_TOKEN")    # ← Pineのsecretと一致必須
DEBUG           = os.getenv("DEBUG", "1") == "1"        # デフォルトON（デプロイ後消してOK）

app = FastAPI(title="AI-ringo Webhook (debug)")

# ---- 共通Discord送信（エラー詳細をログに） ----
async def discord(text: str):
    if not DISCORD_WEBHOOK:
        print("[WARN] DISCORD_WEBHOOK is empty; skip discord send")
        return
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.post(DISCORD_WEBHOOK, json={"content": text})
            print(f"[DBG] discord resp {r.status_code}")
    except Exception as e:
        print(f"[ERR] discord send failed: {e}")

# ---- keepalive ----
@app.get("/")
async def root(): return {"ok": True}
@app.head("/"), app.head("/webhook"), app.head("/health")
async def head_ok(): return {}
@app.get("/webhook"), app.get("/webhook/ping")
async def ping(): return {"ok": True, "msg": "pong"}

# ---- 可視化ヘルス：現在の設定を丸わかり（secretは一部マスク）----
@app.get("/diag")
async def diag():
    masked = (ALLOWED_TOKEN[:2] + "***") if ALLOWED_TOKEN else None
    return {
        "has_discord_webhook": bool(DISCORD_WEBHOOK),
        "allowed_token_prefix": masked,
        "debug_mode": DEBUG,
    }

@app.get("/health")
async def health():
    ok, detail = await orch_health()
    return {"ok": ok, "detail": detail}

# ---- TradingView payload ----
class TVPayload(BaseModel):
    secret: str
    symbol: str
    dir: str
    price: float
    ts: int

# ---- 受信テスト用：手動pingでDiscordに必ず飛ぶ ----
@app.get("/test/discord")
async def test_discord():
    await discord("✅ /test/discord OK")
    return {"ok": True}

# ---- 受信テスト用：何でも受け取り＆Discordへエコー（secret無視）----
@app.post("/webhook/echo")
async def webhook_echo(req: Request):
    try:
        data = await req.json()
    except Exception:
        data = {"raw": await req.body()}
    print(f"[ECHO] got: {data}")
    await discord(f"🪞 ECHO: {json.dumps(data)[:1800]}")
    return {"ok": True}

# ---- 本番：/webhook/tv ----
LAST_ALERT = {}
@app.post("/webhook/tv")
async def webhook_tv(req: Request):
    # ①受信ログ
    try:
        raw = await req.body()
        print(f"[TV] raw body: {raw[:300]}")
        data = json.loads(raw or "{}")
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    # ②バリデーション
    try:
        p = TVPayload(**data)
    except Exception as e:
        print(f"[TV] bad payload: {e}")
        raise HTTPException(400, f"Bad payload: {e}")

    # ③secret照合
    if p.secret != (ALLOWED_TOKEN or ""):
        print(f"[TV] forbidden: got secret='{p.secret}', expected='{ALLOWED_TOKEN}'")
        raise HTTPException(403, "Forbidden")

    # ④Discordへ即通知（AI前）
    await discord(f"📡 初動: {p.symbol} {p.dir} @ {p.price:.2f}")

    # ⑤デバウンス
    key, now = (p.symbol, p.dir), time.time()
    if key in LAST_ALERT and now - LAST_ALERT[key] < 8:
        print("[TV] debounced")
        return {"ok": True, "debounced": True}
    LAST_ALERT[key] = now

    # ⑥BUY/SELL以外は無視（安全）
    if p.dir not in ("BUY", "SELL"):
        return {"ok": True, "ignored": p.dir}

    # ⑦AI起動
    try:
        await handle_tv_signal(symbol=p.symbol, direction=p.dir, price=p.price, ts=p.ts)
    except Exception as e:
        print(f"[ERR] orchestrator failed: {e}")
    return {"ok": True}
