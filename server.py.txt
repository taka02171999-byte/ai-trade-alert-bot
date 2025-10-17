# server.py
from flask import Flask, request, jsonify
import json, os, time, requests
from datetime import datetime, timezone, timedelta

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
app = Flask(__name__)

def post_discord_embed(title, description, fields, color):
    payload = {
        "embeds": [{
            "title": title,
            "description": description,
            "color": color,
            "fields": fields,
            "footer": {"text": "AIりんご式"},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }]
    }
    requests.post(DISCORD_WEBHOOK, json=payload)

@app.route("/webhook", methods=["POST"])
def webhook():
    d = request.get_json(force=True)
    symbol = d.get("symbol")
    side = d.get("side")
    close = float(d.get("c"))
    atr = float(d.get("atr", 0))
    now_jst = datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=9))
    ).strftime("%Y-%m-%d %H:%M:%S JST")

    if side == "buy":
        color, side_text = 0x2ECC71, "買い"
    else:
        color, side_text = 0xE74C3C, "売り"

    desc = f"銘柄: **{symbol}**\n方向: **{side_text}**\n時刻: {now_jst}"
    fields = [{"name": "終値", "value": f"{close:.1f}円"}]

    post_discord_embed(f"📈 {side_text}シグナル", desc, fields, color)
    return jsonify({"ok": True})

@app.route("/")
def index():
    return "ok"
