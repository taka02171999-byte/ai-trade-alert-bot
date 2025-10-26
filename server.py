# server.py
# ========================================
# AIりんご式 Entry+Follow サーバー（vFINAL_ONECHANCE対応）
# TradingView → Flask(Webhook) → Discord 通知
# ========================================

from flask import Flask, request, jsonify
from datetime import datetime, timedelta, timezone
import json
import requests
import os

JST = timezone(timedelta(hours=9))
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "")
SECRET_TOKEN = "super_secret_token_please_match"

app = Flask(__name__)

# ==========================
# 辞書（銘柄コード→日本語名）
# ==========================
with open("data/symbol_names.json", "r", encoding="utf-8") as f:
    SYMBOL_NAMES = json.load(f)

# ==========================
# 共通関数
# ==========================
def jst_now_str():
    return datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")

def discord_send(message, color=0x00ffcc):
    """DiscordにEmbed送信"""
    if not DISCORD_WEBHOOK:
        print("⚠ Discord Webhook URL未設定")
        return
    data = {
        "embeds": [
            {
                "title": "AIりんご式トレード通知",
                "description": message,
                "color": color,
                "footer": {"text": "AIりんご式 | " + jst_now_str()}
            }
        ]
    }
    requests.post(DISCORD_WEBHOOK, json=data)

# ==========================
# Webhook受信ルート
# ==========================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "reason": "no data"}), 400

    if data.get("secret") != SECRET_TOKEN:
        return jsonify({"status": "error", "reason": "invalid secret"}), 403

    event_type = data.get("type", "")
    symbol = data.get("symbol", "")
    price = data.get("price", None)
    pct = data.get("pct_from_entry", None)
    side = data.get("side", "")
    step_label = data.get("step_label", "")

    name = SYMBOL_NAMES.get(symbol, symbol)
    jst_time = jst_now_str()

    # === ENTRY系 ===
    if event_type in ["ENTRY_BUY", "ENTRY_SELL"]:
        emoji = "🟢" if event_type == "ENTRY_BUY" else "🔴"
        msg = f"{emoji}【エントリー】\n銘柄: {symbol} {name}\n方向: {'買い' if side == 'BUY' else '売り'}\n価格: {price}円\n時刻: {jst_time}"
        discord_send(msg, 0x00ff00 if side == "BUY" else 0xff3333)

    # === PRICE_TICK（AI判断用） ===
    elif event_type == "PRICE_TICK":
        print(f"📊 PRICE_TICK {symbol}: {price}, pct={pct}")

    # === STEP_UP / STEP_DOWN ===
    elif event_type in ["STEP_UP", "STEP_DOWN"]:
        # 今回はSTEP通知をスキップ（送らない）
        pass

    # === TP / SL / TIMEOUT ===
    elif event_type in ["TP", "SL", "TIMEOUT"]:
        emoji = "🎯" if event_type == "TP" else "⚡" if event_type == "SL" else "⏱"
        title = "利確" if event_type == "TP" else "損切り" if event_type == "SL" else "タイムアウト"
        msg = (
            f"{emoji}【{title}】\n"
            f"銘柄: {symbol} {name}\n"
            f"決済価格: {price}円\n"
            f"変化率: {pct if pct is not None else '---'}%\n"
            f"AI判定により自動決済\n"
            f"時刻: {jst_time}"
        )
        discord_send(msg, 0x33ccff if event_type == "TP" else 0xff6666 if event_type == "SL" else 0xcccc00)

    return jsonify({"status": "ok"})

# ==========================
# メイン起動
# ==========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
