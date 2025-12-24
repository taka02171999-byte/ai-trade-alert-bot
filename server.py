# ==========================================
# TradingView Webhook -> Discord通知 + 取引ログ保存（AI判断なし）
#
# Pine(JSON) から来るイベント:
#   ENTRY / HALF_TP / FULL_TP / STOP / TIMEOUT
#
# 受け取る主なキー:
#   secret, event, session(AM/PM), side(LONG/SHORT), ticker, name, price, pct_from_entry(任意)
#
# ルール:
# - サーバは一切判断しない（AIなし）
# - ENTRYでポジ開始
# - HALF_TPでhalf_pctを保存
# - FULL_TP/STOP/TIMEOUTでクローズし、合算pnl%を trade_log.csv に1行保存
#     realized_pct = 0.5*half_pct + 0.5*final_pct （halfが無いなら final_pct）
# ==========================================

from flask import Flask, request, jsonify
from datetime import datetime, timezone, timedelta
import os
import json
import csv
import requests

JST = timezone(timedelta(hours=9))
app = Flask(__name__)

# ---- ENV
SECRET_TOKEN = os.getenv("TV_SHARED_SECRET", "super_secret_token_please_match")
DISCORD_WEBHOOK_MAIN = os.getenv("DISCORD_WEBHOOK_MAIN", "")
TRADE_LOG_PATH = "data/trade_log.csv"
STATE_PATH = "data/positions_state.json"

# 起動時に最低限だけ状態をログ（Webhook全文は出さない）
def _safe_url(u: str) -> str:
    if not u:
        return ""
    return u[:45] + "..." if len(u) > 45 else u

print("[boot] TV_SHARED_SECRET set =", bool(SECRET_TOKEN))
print("[boot] DISCORD_WEBHOOK_MAIN =", _safe_url(DISCORD_WEBHOOK_MAIN))

# --------------------
# utils
# --------------------
def jst_now() -> datetime:
    return datetime.now(JST)

def jst_now_str() -> str:
    return jst_now().strftime("%Y/%m/%d %H:%M:%S")

def _safe_float(x, default=None):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default

def _side_to_buy_sell(side_str: str) -> str:
    # Pine: LONG/SHORT
    s = (side_str or "").upper()
    if s == "LONG":
        return "BUY"
    if s == "SHORT":
        return "SELL"
    # 互換: BUY/SELL が来てもそのまま
    if s in ("BUY", "SELL"):
        return s
    return s or "BUY"

def _calc_pct_from_entry(entry_price: float, now_price: float, side_buy_sell: str):
    if not entry_price or entry_price == 0 or now_price is None:
        return None
    raw = (now_price / entry_price - 1.0) * 100.0
    # BUY:そのまま、SELL:符号反転
    if side_buy_sell == "SELL":
        raw *= -1.0
    return raw

def _load_state():
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_state(state: dict):
    os.makedirs("data", exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def _pos_key(session: str, ticker: str) -> str:
    # 同一銘柄がAM/PMで別ポジになり得るので分ける
    return f"{(session or '').upper()}::{(ticker or '').upper()}"

def send_discord(msg: str, color: int = 0x00ccff):
    """
    ここは挙動を変えず、失敗理由が分かるログだけ強化。
    Discord webhook成功は 204 が多い。失敗時は本文を出す。
    """
    if not DISCORD_WEBHOOK_MAIN:
        print("⚠ [Discord] DISCORD_WEBHOOK_MAIN 未設定\n", msg)
        return

    data = {
        "embeds": [
            {
                "title": "AIりんご式トレード通知",
                "description": msg,
                "color": color,
                "footer": {"text": "AIりんご式 | " + jst_now_str()},
            }
        ]
    }

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ai-ringo-bot/1.0",
    }

    try:
        resp = requests.post(DISCORD_WEBHOOK_MAIN, json=data, headers=headers, timeout=10)

        ok = (200 <= resp.status_code < 300)
        print(f"[Discord] status={resp.status_code} ok={ok}")

        if not ok:
            # 失敗時にDiscordの返答をログに出す（ここが原因）
            try:
                print("[Discord] response_text =", resp.text)
            except Exception:
                print("[Discord] response_text = <no text>")

    except Exception as e:
        print(f"[Discord] 送信エラー: {type(e).__name__}: {e}\nFAILED MSG >>> {msg}")

def append_trade_log(row: dict):
    """
    report_*.py が読んでる data/trade_log.csv に「1トレード=1行」で保存
    既存レポート互換のため fieldnames は固定（増やさない）
    """
    os.makedirs("data", exist_ok=True)
    file_exists = os.path.exists(TRADE_LOG_PATH)

    fieldnames = ["timestamp", "symbol", "side", "entry_price", "exit_price", "pnl_pct", "reason"]

    with open(TRADE_LOG_PATH, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "timestamp": row.get("timestamp", ""),
            "symbol": row.get("symbol", ""),
            "side": row.get("side", ""),
            "entry_price": row.get("entry_price", ""),
            "exit_price": row.get("exit_price", ""),
            "pnl_pct": row.get("pnl_pct", ""),
            "reason": row.get("reason", ""),
        })


# --------------------
# main webhook
# --------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "error", "reason": "no data"}), 400

    # secret check
    if payload.get("secret") != SECRET_TOKEN:
        return jsonify({"status": "error", "reason": "invalid secret"}), 403

    # Pine形式優先（event/ticker/side=LONG|SHORT/priceは文字列でもOK）
    event_type = (payload.get("event") or payload.get("type") or "").upper()
    session = (payload.get("session") or "").upper()
    ticker = payload.get("ticker") or payload.get("symbol") or ""
    name = payload.get("name") or ""
    side_raw = payload.get("side") or ""
    side = _side_to_buy_sell(side_raw)

    price_now = _safe_float(payload.get("price"), default=0.0)
    pct_now = _safe_float(payload.get("pct_from_entry"), default=None)

    if not ticker or not event_type:
        return jsonify({"status": "error", "reason": "missing ticker/event"}), 400

    key = _pos_key(session, ticker)
    state = _load_state()
    pos = state.get(key)

    print(f"[WEBHOOK] {event_type} {session} {ticker} {side_raw}->{side} price={price_now} pct={pct_now} at {jst_now_str()}")

    # --------------------
    # ENTRY
    # --------------------
    if event_type == "ENTRY":
        # すでに未クローズがあるなら二重ENTRY防止（何もしない）
        if pos and not pos.get("closed", False):
            return jsonify({"status": "ok", "note": "already in position"})

        state[key] = {
            "session": session,
            "ticker": ticker,
            "name": name,
            "side": side,  # BUY/SELL
            "entry_price": price_now,
            "entry_time": jst_now().isoformat(timespec="seconds"),
            "half_done": False,
            "half_pct": None,
            "closed": False,
            "close_time": None,
            "close_reason": None,
            "exit_price": None,
            "final_pct": None,
        }
        _save_state(state)

        color = 0x00ff00 if side == "BUY" else 0xff3333
        msg = (
            f"🟢 ENTRY\n"
            f"Session: {session}\n"
            f"銘柄: {ticker} {name}\n"
            f"方向: {'買い(LONG)' if side=='BUY' else '売り(SHORT)'}\n"
            f"価格: {price_now}\n"
            f"時刻: {jst_now_str()}"
        )
        send_discord(msg, color)
        return jsonify({"status": "ok"})

    # --------------------
    # HALF_TP
    # --------------------
    if event_type == "HALF_TP":
        if not pos or pos.get("closed"):
            return jsonify({"status": "ok", "note": "no active position"})

        if pos.get("half_done"):
            return jsonify({"status": "ok", "note": "half already done"})

        # pctが無いなら計算
        if pct_now is None:
            pct_now = _calc_pct_from_entry(_safe_float(pos.get("entry_price"), 0.0), price_now, pos.get("side"))

        pos["half_done"] = True
        pos["half_pct"] = pct_now
        state[key] = pos
        _save_state(state)

        msg = (
            f"🟠 HALF_TP（半分利確）\n"
            f"Session: {pos.get('session','')}\n"
            f"銘柄: {pos.get('ticker','')} {pos.get('name','')}\n"
            f"価格: {price_now}\n"
            f"半利確時点%: {round(pct_now,2) if pct_now is not None else '---'}%\n"
            f"時刻: {jst_now_str()}"
        )
        send_discord(msg, 0xffaa33)
        return jsonify({"status": "ok"})

    # --------------------
    # CLOSE EVENTS: FULL_TP / STOP / TIMEOUT
    # --------------------
    if event_type in ("FULL_TP", "STOP", "TIMEOUT"):
        if not pos or pos.get("closed"):
            return jsonify({"status": "ok", "note": "no active position"})

        # pctが無いなら計算
        if pct_now is None:
            pct_now = _calc_pct_from_entry(_safe_float(pos.get("entry_price"), 0.0), price_now, pos.get("side"))

        half_done = bool(pos.get("half_done", False))
        half_pct = _safe_float(pos.get("half_pct"), default=None)
        final_pct = pct_now

        # 合算
        if half_done and (half_pct is not None) and (final_pct is not None):
            realized = 0.5 * half_pct + 0.5 * final_pct
        else:
            realized = final_pct

        pos["closed"] = True
        pos["close_time"] = jst_now().isoformat(timespec="seconds")
        pos["close_reason"] = event_type
        pos["exit_price"] = price_now
        pos["final_pct"] = final_pct
        state[key] = pos
        _save_state(state)

        # Discord
        if event_type == "FULL_TP":
            label, color = "🟦 FULL_TP（全利確）", 0x33ccff
        elif event_type == "STOP":
            label, color = "🟥 STOP（損切り）", 0xff6666
        else:
            label, color = "🟨 TIMEOUT（時間切れ）", 0xcccc00

        msg = (
            f"{label}\n"
            f"Session: {pos.get('session','')}\n"
            f"銘柄: {pos.get('ticker','')} {pos.get('name','')}\n"
            f"決済価格: {price_now}\n"
            f"最終%: {round(final_pct,2) if final_pct is not None else '---'}%\n"
            f"合算%（半+全）: {round(realized,2) if realized is not None else '---'}%\n"
            f"時刻: {jst_now_str()}"
        )
        send_discord(msg, color)

        # CSV（既存レポ互換のため 1行=1トレード）
        append_trade_log({
            "timestamp": jst_now().isoformat(timespec="seconds"),
            "symbol": pos.get("ticker", ""),
            "side": pos.get("side", ""),
            "entry_price": pos.get("entry_price", ""),
            "exit_price": price_now,
            "pnl_pct": round(realized, 2) if realized is not None else "",
            "reason": f"{pos.get('session','')}_{event_type}",
        })

        return jsonify({"status": "ok"})

    # --------------------
    # unhandled
    # --------------------
    return jsonify({"status": "ok", "note": "unhandled event"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
