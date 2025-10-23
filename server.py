# server.py
import os, json, uuid, threading, time, asyncio, hashlib
from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

from orchestrator import (
    now_jst, fetch_last_close_bulk, is_entry_confirmed,
    atr_based_tp_sl, get_display_name,
    load_today_top10, is_in_today_top10
)

load_dotenv()

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "").strip()
ALLOWED_WEBHOOK_TOKEN = os.getenv("ALLOWED_WEBHOOK_TOKEN", "your_shared_secret").strip()

AFTERCHASE_SCHEDULE = [int(x) for x in os.getenv("AFTERCHASE_SCHEDULE", "1,2,3").split(",") if x.strip().isdigit()]
TPSL_WINDOW_MIN = int(os.getenv("TPSL_WINDOW_MIN", "30"))
MONITOR_SEC     = int(os.getenv("MONITOR_SEC", "30"))
COOLDOWN_SEC    = int(os.getenv("COOLDOWN_SEC", "8"))

DATA_DIR = Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"

# ----- Discord Colors -----
COLOR_BUY       = 0x2ECC71  # 緑
COLOR_SELL      = 0xE74C3C  # 赤
COLOR_TP        = 0xF1C40F  # ゴールド
COLOR_SL        = 0x95A5A6  # グレー
COLOR_TIMEOUT   = 0xE67E22  # オレンジ
COLOR_INFO      = 0x3498DB  # 青

app = FastAPI(title="AI-Trade Webhook", version="3.0")

# ----- 状態 -----
_state_lock = threading.Lock()
def _load_state() -> Dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"positions": {}, "cooldown": {}}

def _save_state(s: Dict[str, Any]):
    try:
        STATE_FILE.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def _cooldown_key(kind: str, symbol: str, extra: str = "") -> str:
    base = f"{kind}|{symbol}|{extra}"
    return hashlib.sha1(base.encode()).hexdigest()[:16]

def in_cooldown(kind: str, symbol: str, extra: str = "") -> bool:
    with _state_lock:
        s = _load_state()
        key = _cooldown_key(kind, symbol, extra)
        ts = s["cooldown"].get(key, 0)
        now = time.time()
        if now - ts < COOLDOWN_SEC:
            return True
        s["cooldown"][key] = now
        _save_state(s)
    return False

# ----- Discord -----
async def post_discord_embed(title: str, desc: str, color: int):
    if not DISCORD_WEBHOOK:
        return
    payload = {"embeds": [{"title": title, "description": desc, "color": color}]}
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            await cli.post(DISCORD_WEBHOOK, json=payload)
    except Exception as e:
        print(f"[discord] send failed: {e}")

@app.get("/diag")
async def diag():
    return {"ok": True, "top10": load_today_top10()}

@app.get("/test/discord")
async def test_discord():
    await post_discord_embed("✅ テスト", "Discord連携テスト成功！", COLOR_INFO)
    return {"ok": True}

# ----- TV payload -----
def _parse_tv_payload(raw: str | dict):
    if isinstance(raw, dict):
        d = raw
    else:
        try:
            d = json.loads(raw)
        except Exception:
            d = {}
            for seg in str(raw).replace("\n", " ").split():
                if ":" in seg:
                    k, v = seg.split(":", 1)
                    d[k.strip()] = v.strip().strip('",')
    secret = (d.get("secret") or d.get("token") or "").strip()
    sym = (d.get("symbol") or d.get("ticker") or d.get("s") or "").strip().upper()
    direction = (d.get("dir") or d.get("side") or "").strip().upper()  # BUY/SELL
    try:
        price = float(d.get("price") or d.get("p") or 0.0)
    except Exception:
        price = 0.0
    ts = d.get("ts") or d.get("time") or ""
    return {"secret": secret, "symbol": sym, "dir": direction, "price": price, "ts": ts}

# ----- 価格 & 確定 -----
def _schedule_afterchase(symbol: str, direction: str, trigger_price: float, display: str):
    """ブレイク未達 → 1,2,3分で追う。確定した瞬間に通知＆監視開始。"""
    def one_shot(delay_min: int):
        def job():
            try:
                prices = fetch_last_close_bulk([symbol])
                live = prices.get(symbol)
                if live is None:
                    return
                if is_entry_confirmed(trigger_price, live):
                    _open_and_notify(symbol, direction, trigger_price, f"後追い{delay_min}分で確定", display)
            except Exception as e:
                print(f"[afterchase] {symbol}: {e}")
        t = threading.Timer(delay_min * 60, job)
        t.daemon = True
        t.start()

    for m in AFTERCHASE_SCHEDULE:
        one_shot(int(m))

def _open_and_notify(symbol: str, direction: str, entry_price: float, method: str, display: str):
    pos_id = _open_position(symbol, direction, entry_price, method)
    if in_cooldown("ENTRY", symbol, direction):
        return
    title = f"✅ エントリー確定（{direction}）"
    desc = (
        f"銘柄: **{display}**\n"
        f"価格: **{entry_price:.2f}**\n"
        f"TP: **{_positions_get(pos_id)['tp']:.2f}** / SL: **{_positions_get(pos_id)['sl']:.2f}**\n"
        f"方式: {method}\n"
        f"pos_id: `{pos_id}`"
    )
    color = COLOR_BUY if direction == "BUY" else COLOR_SELL
    asyncio.run(post_discord_embed(title, desc, color))
    _schedule_monitor(symbol, pos_id)

# ----- ポジション管理 -----
def _positions_get(pos_id: str):
    with _state_lock:
        s = _load_state()
        return s["positions"].get(pos_id)

def _open_position(symbol: str, direction: str, entry_price: float, reason: str) -> str:
    """銘柄×方向につき“同時に1つ”だけOPEN許可（重複を防止）。"""
    with _state_lock:
        s = _load_state()
        # 既存OPENがあるか？
        for pid, pos in s["positions"].items():
            if pos.get("status") == "OPEN" and pos.get("symbol") == symbol and pos.get("direction") == direction:
                return pid  # 既存を使う（重複防止）
        # 新規
        tp, sl = atr_based_tp_sl(symbol, entry_price)
        pos_id = uuid.uuid4().hex[:12]
        s["positions"][pos_id] = {
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "tp": float(tp),
            "sl": float(sl),
            "opened_at": now_jst().isoformat(),
            "status": "OPEN",
            "reason": reason,
        }
        _save_state(s)
        return pos_id

def _close_position(pos_id: str, status: str):
    with _state_lock:
        s = _load_state()
        pos = s["positions"].get(pos_id)
        if not pos: return None
        if pos.get("status") != "OPEN":
            return pos
        pos["status"] = status
        pos["closed_at"] = now_jst().isoformat()
        _save_state(s)
        return pos

def _schedule_monitor(symbol: str, pos_id: str):
    """TP/SL/タイムアウト（30分）をサーバ側で監視。"""
    def loop():
        start = time.time()
        while True:
            with _state_lock:
                s = _load_state()
                pos = s["positions"].get(pos_id)
            if not pos or pos.get("status") != "OPEN":
                return

            prices = fetch_last_close_bulk([symbol])
            live = prices.get(symbol)
            if live is not None:
                direction = pos["direction"]; tp = float(pos["tp"]); sl = float(pos["sl"]); entry = float(pos["entry_price"])
                if direction == "BUY":
                    if live >= tp:
                        if not in_cooldown("TP", symbol, direction):
                            _close_position(pos_id, "TAKE_PROFIT")
                            asyncio.run(post_discord_embed(
                                "🎉 利確（BUY）",
                                f"銘柄: **{get_display_name(symbol)}**\n約定: **{entry:.2f}** → 現在: **{live:.2f}**\nTP: **{tp:.2f}**\npos_id: `{pos_id}`",
                                COLOR_TP
                            ))
                        return
                    if live <= sl:
                        if not in_cooldown("SL", symbol, direction):
                            _close_position(pos_id, "STOP_LOSS")
                            asyncio.run(post_discord_embed(
                                "🛑 損切り（BUY）",
                                f"銘柄: **{get_display_name(symbol)}**\n約定: **{entry:.2f}** → 現在: **{live:.2f}**\nSL: **{sl:.2f}**\npos_id: `{pos_id}`",
                                COLOR_SL
                            ))
                        return
                else:
                    if live <= tp:
                        if not in_cooldown("TP", symbol, direction):
                            _close_position(pos_id, "TAKE_PROFIT")
                            asyncio.run(post_discord_embed(
                                "🎉 利確（SELL）",
                                f"銘柄: **{get_display_name(symbol)}**\n約定: **{entry:.2f}** → 現在: **{live:.2f}**\nTP: **{tp:.2f}**\npos_id: `{pos_id}`",
                                COLOR_TP
                            ))
                        return
                    if live >= sl:
                        if not in_cooldown("SL", symbol, direction):
                            _close_position(pos_id, "STOP_LOSS")
                            asyncio.run(post_discord_embed(
                                "🛑 損切り（SELL）",
                                f"銘柄: **{get_display_name(symbol)}**\n約定: **{entry:.2f}** → 現在: **{live:.2f}**\nSL: **{sl:.2f}**\npos_id: `{pos_id}`",
                                COLOR_SL
                            ))
                        return

            if time.time() - start >= TPSL_WINDOW_MIN * 60:
                pos = _close_position(pos_id, "TIMEOUT_CLOSE")
                if pos and not in_cooldown("TIMEOUT", symbol, pos["direction"]):
                    asyncio.run(post_discord_embed(
                        "⏱️ 30分経過：ポジション解消",
                        f"銘柄: **{get_display_name(symbol)}**\n方向: **{pos['direction']}**\n約定: **{float(pos['entry_price']):.2f}**\npos_id: `{pos_id}`",
                        COLOR_TIMEOUT
                    ))
                return

            time.sleep(MONITOR_SEC)

    t = threading.Thread(target=loop, daemon=True)
    t.start()

# ----- Webhook -----
@app.post("/webhook/tv")
async def webhook_tv(request: Request):
    try:
        raw = (await request.body()).decode("utf-8", errors="ignore")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = raw

        p = _parse_tv_payload(payload)
        if ALLOWED_WEBHOOK_TOKEN and p["secret"] != ALLOWED_WEBHOOK_TOKEN:
            return JSONResponse({"ok": False, "detail": "unauthorized"}, status_code=401)

        symbol = p["symbol"]; direction = p["dir"]; trigger_price = float(p["price"]); ts = p["ts"]
        if not symbol or direction not in ("BUY","SELL") or trigger_price <= 0:
            return {"ok": True, "filtered": True}

        # “選定10銘柄のみ通知”のフィルタ
        notify_ok = is_in_today_top10(symbol)

        # 表示名の自動取得（失敗時はコード）
        display = get_display_name(symbol)

        # 即時確定チェック
        prices = fetch_last_close_bulk([symbol])
        live = prices.get(symbol)

        if live is not None and is_entry_confirmed(trigger_price, live):
            if notify_ok:
                _open_and_notify(symbol, direction, trigger_price, "即時確定", display)
            else:
                # 通知はしないが、裏でポジション開いて学習用に監視（要求どおり）
                _open_and_notify(symbol, direction, trigger_price, "即時確定（学習のみ・非通知）", display)
        else:
            # ブレイク未達 → 1/2/3分で後追い
            _schedule_afterchase(symbol, direction, trigger_price, display)

        return {"ok": True}
    except Exception as e:
        print(f"[webhook] error: {e}")
        return JSONResponse({"ok": False, "detail": str(e)}, status_code=500)

# ----- Render起動用 -----
# gunicorn -k uvicorn.workers.UvicornWorker -w 1 -b 0.0.0.0:$PORT server:app --timeout 180 --graceful-timeout 30
