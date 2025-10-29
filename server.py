# server.py (AI ENTRY + EXIT 完全統合)
from flask import Flask, request, jsonify
from datetime import datetime, timedelta, timezone
import json, requests, os
import orchestrator, position_manager, ai_exit_logic, ai_entry_logic

JST = timezone(timedelta(hours=9))
DISCORD_WEBHOOK_MAIN = os.getenv("DISCORD_WEBHOOK_MAIN", "")
SECRET_TOKEN = os.getenv("TV_SHARED_SECRET", "super_secret_token_please_match")

app = Flask(__name__)

def discord_embed(msg, color=0x00ffcc, title="AIりんご式トレード通知"):
    if not DISCORD_WEBHOOK_MAIN: return
    data = {"embeds": [{"title": title, "description": msg, "color": color}]}
    try: requests.post(DISCORD_WEBHOOK_MAIN, json=data, timeout=5)
    except: pass

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if not data or data.get("secret") != SECRET_TOKEN:
        return jsonify({"status":"error"}),403
    t = data.get("type","")
    sym = data.get("symbol","")
    price = float(data.get("price",0))
    pct = float(data.get("pct_from_entry",0))
    side = data.get("side","")

    # --- ENTRY ---
    if t in ["ENTRY_BUY","ENTRY_SELL"]:
        vol_mult = float(data.get("vol_mult", 1.0))
        vwap = float(data.get("vwap", 0))
        atr = float(data.get("atr", 0))
        last_pct = pct
        accept, reason = ai_entry_logic.should_accept_entry(sym, side, vol_mult, vwap, atr, last_pct)
        pos = position_manager.start_position(sym, side, price, accepted_real=accept)
        if accept:
            discord_embed(f"🟢ENTRY {sym} {side} {price}\n理由: {reason}", 0x00ff00)
        else:
            discord_embed(f"🕓PENDING {sym} {side}（AI監視中）", 0xcccccc)
        return jsonify({"ok":True})

    # --- PRICE_TICK ---
    if t == "PRICE_TICK":
        tick = {
            "t": datetime.now(JST).isoformat(),
            "price": price,
            "pct": pct,
            "volume": data.get("volume"),
            "vwap": data.get("vwap"),
            "atr": data.get("atr"),
            "mins_from_entry": data.get("mins_from_entry")
        }
        pos = position_manager.add_tick(sym, tick)
        if not pos or pos.get("closed"): return jsonify({"ok":True})
        wants_exit, exit_info = ai_exit_logic.should_exit_now(pos)
        if wants_exit and exit_info:
            etype, eprice = exit_info
            position_manager.force_close(sym, etype, eprice)
            discord_embed(f"🎯{etype} {sym} {round(pct,2)}% @ {eprice}", 0x33ccff)
        return jsonify({"ok":True})

    # --- TP/SL/TIMEOUT（保険）---
    if t in ["TP","SL","TIMEOUT"]:
        position_manager.force_close(sym, t, price)
        discord_embed(f"⚡{t} {sym} {round(pct,2)}% @ {price}", 0xff9999)
        return jsonify({"ok":True})

    return jsonify({"ok":True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
