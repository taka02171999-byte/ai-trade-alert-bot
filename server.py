from flask import Flask, request, jsonify
import os
import json

from utils.discord import send_discord
from utils.time_utils import get_jst_now_str, jst_now, session_from_time
import trade_store

app = Flask(__name__)

SECRET_TOKEN = os.getenv("TV_SHARED_SECRET", "super_secret_token_please_match")
DISCORD_WEBHOOK_MAIN = os.getenv("DISCORD_WEBHOOK_MAIN", "")
DISCORD_USE_JP_NAMES = os.getenv("DISCORD_USE_JP_NAMES", "true").lower() == "true"

SYMBOL_NAMES_PATH = "data/symbol_names.json"
if os.path.exists(SYMBOL_NAMES_PATH):
    with open(SYMBOL_NAMES_PATH, "r", encoding="utf-8") as f:
        SYMBOL_NAMES = json.load(f)
else:
    SYMBOL_NAMES = {}

def jp_name(symbol: str) -> str:
    if not symbol:
        return symbol
    up = symbol.upper()
    cands = {symbol, up}
    if not up.endswith(".T"):
        cands.add(up + ".T")
    else:
        cands.add(up[:-2])
    digits = "".join(ch for ch in up if ch.isalnum())
    if digits:
        cands.add(digits)
    for k in cands:
        if k in SYMBOL_NAMES:
            return SYMBOL_NAMES[k]
    return symbol

def resolve_name(symbol: str) -> str:
    return jp_name(symbol) if DISCORD_USE_JP_NAMES else symbol

def _num(v):
    try:
        return float(v)
    except Exception:
        return None

def _color_for_event(event_type: str, side: str = "") -> int:
    if event_type.startswith("ENTRY"):
        return 0x00FF00 if side == "BUY" else 0xFF3333
    if event_type == "HALF_TP":
        return 0x33CCFF
    if event_type == "FULL_TP":
        return 0x33CCFF
    if event_type == "STOP":
        return 0xFF6666
    if event_type == "TIMEOUT":
        return 0xCCCC00
    return 0x00CCFF

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "error", "reason": "no json"}), 400

    if payload.get("secret") != SECRET_TOKEN:
        return jsonify({"status": "error", "reason": "invalid secret"}), 403

    event_type = payload.get("type", "")
    symbol = payload.get("symbol", "")
    side = payload.get("side", "")  # BUY / SELL
    price = _num(payload.get("price"))
    pct_from_entry = _num(payload.get("pct_from_entry"))

    # セッション：Pineが送ってくるならそれ優先。無ければJST時刻から推定
    session = payload.get("session")
    if not session:
        session = session_from_time(jst_now())
    session = str(session).upper()

    name = resolve_name(symbol)

    if price is None:
        return jsonify({"status": "error", "reason": "price missing"}), 400

    # --------------- ENTRY ---------------
    if event_type in ["ENTRY", "ENTRY_BUY", "ENTRY_SELL"]:
        # sideの補正（ENTRY_BUY/ENTRY_SELL も対応）
        if event_type == "ENTRY_BUY":
            side = "BUY"
        elif event_type == "ENTRY_SELL":
            side = "SELL"

        if side not in ["BUY", "SELL"]:
            return jsonify({"status": "error", "reason": "side missing"}), 400

        # すでにオープンがあるなら上書きしない（2重ENTRYを防ぐ）
        existing = trade_store.get_open(symbol)
        if existing:
            return jsonify({"status": "ok", "note": "already_open"})

        pos = trade_store.start_entry(
            symbol=symbol,
            name=name,
            side=side,
            session=session,
            price=price,
            pct_from_entry=pct_from_entry,
        )

        desc = (
            f"🟢 ENTRY\n"
            f"銘柄: {symbol} ({name})\n"
            f"方向: {'買い' if side=='BUY' else '売り'}\n"
            f"価格: {price}\n"
            f"セッション: {session}\n"
            f"時刻: {get_jst_now_str()}\n"
        )
        send_discord(DISCORD_WEBHOOK_MAIN, "AIりんご式トレード通知", desc, _color_for_event("ENTRY", side))
        return jsonify({"status": "ok", "trade_id": pos.get("trade_id")})

    # --------------- HALF_TP ---------------
    if event_type in ["HALF_TP", "TP_HALF"]:
        pos = trade_store.get_open(symbol)
        if not pos:
            return jsonify({"status": "ok", "note": "no_open"})

        updated = trade_store.mark_half_tp(symbol, price=price, pct_from_entry=pct_from_entry)

        desc = (
            f"🔷 HALF_TP（半分利確）\n"
            f"銘柄: {symbol} ({name})\n"
            f"価格: {price}\n"
            f"エントリー比: {pct_from_entry if pct_from_entry is not None else '---'}%\n"
            f"セッション: {pos.get('session','')}\n"
            f"時刻: {get_jst_now_str()}\n"
        )
        send_discord(DISCORD_WEBHOOK_MAIN, "AIりんご式トレード通知", desc, _color_for_event("HALF_TP", pos.get("side","")))
        return jsonify({"status": "ok", "note": "half_marked" if updated else "ignored"})

    # --------------- CLOSE系 ---------------
    if event_type in ["FULL_TP", "TP", "STOP", "SL", "TIMEOUT"]:
        # 正規化
        if event_type == "TP":
            event_type = "FULL_TP"
        if event_type == "SL":
            event_type = "STOP"

        pos = trade_store.get_open(symbol)
        if not pos:
            return jsonify({"status": "ok", "note": "no_open"})

        closed = trade_store.close_position(
            symbol=symbol,
            exit_reason=event_type,
            price=price,
            pct_from_entry=pct_from_entry,
        )
        if not closed:
            return jsonify({"status": "ok", "note": "already_closed"})

        desc = (
            f"{'🎯 FULL_TP' if event_type=='FULL_TP' else '⚡ STOP' if event_type=='STOP' else '⏱ TIMEOUT'}\n"
            f"銘柄: {symbol} ({name})\n"
            f"決済価格: {price}\n"
            f"エントリー比: {pct_from_entry if pct_from_entry is not None else '---'}%\n"
            f"実現損益(合算): {closed.get('realized_pct','---')}%\n"
            f"セッション: {pos.get('session','')}\n"
            f"時刻: {get_jst_now_str()}\n"
        )
        send_discord(DISCORD_WEBHOOK_MAIN, "AIりんご式トレード通知", desc, _color_for_event(event_type, pos.get("side","")))
        return jsonify({"status": "ok", "realized_pct": closed.get("realized_pct", "")})

    # --------------- 未対応 ---------------
    return jsonify({"status": "ok", "note": f"unhandled:{event_type}"})
    
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
