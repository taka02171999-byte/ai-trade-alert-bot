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
import logging

# ==========================
# ログフィルタ (UptimeRobotのpingとかHEADをコンソールに出さないだけ)
# ==========================
class QuietPingFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        if "ping-keepalive" in msg:
            return False
        if "UptimeRobot" in msg:
            return False
        if "HEAD /" in msg:
            return False
        return True

logging.getLogger("werkzeug").addFilter(QuietPingFilter())

# ==========================
# 環境・定数
# ==========================
JST = timezone(timedelta(hours=9))

# RenderのEnvから読む。なかったら""になる
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_MAIN", "")

# TradingView側Pineと合わせる
SECRET_TOKEN = "super_secret_token_please_match"

app = Flask(__name__)

# ==========================
# 銘柄名辞書読み込み
# ==========================
with open("data/symbol_names.json", "r", encoding="utf-8") as f:
    SYMBOL_NAMES = json.load(f)

# ==========================
# 共通ユーティリティ
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
                "footer": {"text": "AIりんご式 | " + jst_now_str()},
            }
        ]
    }

    try:
        resp = requests.post(DISCORD_WEBHOOK, json=data, timeout=5)
        print(f"→ Discord Webhook 送信 status={resp.status_code}")
    except Exception as e:
        print(f"Discord送信エラー: {e}")

# ==========================
# Webhook受信 (TradingView → ここ)
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
    price = data.get("price")
    pct   = data.get("pct_from_entry")
    side  = data.get("side", "")
    step_label = data.get("step_label", "")

    # 人が見やすい銘柄名
    name = SYMBOL_NAMES.get(symbol, symbol)
    jst_time = jst_now_str()

    # ---- ENTRY_BUY / ENTRY_SELL ----
    if event_type in ["ENTRY_BUY", "ENTRY_SELL"]:
        emoji = "🟢" if event_type == "ENTRY_BUY" else "🔴"
        msg = (
            f"{emoji}【エントリー】\n"
            f"銘柄: {symbol} {name}\n"
            f"方向: {'買い' if side == 'BUY' else '売り'}\n"
            f"価格: {price}\n"
            f"時刻: {jst_time}"
        )
        discord_send(msg, 0x00ff00 if side == "BUY" else 0xff3333)

    # ---- PRICE_TICK ----
    elif event_type == "PRICE_TICK":
        # これはDiscord通知しない運用
        # 開発デバッグ用のサーバー側ログだけ出す
        print(f"📊 PRICE_TICK {symbol}: price={price} pct={pct} step={step_label} at {jst_time}")

        # Discordにも出したいなら↓をアンコメント
        # debug_msg = (
        #     f"📊【PRICE_TICK】\n"
        #     f"{symbol} {name}\n"
        #     f"価格:{price} 変化率:{pct}%\n"
        #     f"{jst_time}"
        # )
        # discord_send(debug_msg, 0x3399ff)

    # ---- STEP_UP / STEP_DOWN ----
    elif event_type in ["STEP_UP", "STEP_DOWN"]:
        # これも今はDiscordに投げない
        print(f"↕ STEP {event_type} {symbol}: pct={pct} ({step_label}) {jst_time}")

        # Discordにも出したいなら↓をアンコメント
        # step_msg = (
        #     f"↕【{event_type}】\n"
        #     f"{symbol} {name}\n"
        #     f"変化率:{pct}% ({step_label})\n"
        #     f"価格:{price}\n"
        #     f"{jst_time}"
        # )
        # discord_send(step_msg, 0xffcc00)

    # ---- TP / SL / TIMEOUT ----
    elif event_type in ["TP", "SL", "TIMEOUT"]:
        emoji  = "🎯" if event_type == "TP" else "⚡" if event_type == "SL" else "⏱"
        title  = "利確" if event_type == "TP" else "損切り" if event_type == "SL" else "タイムアウト"
        color  = 0x33ccff if event_type == "TP" else 0xff6666 if event_type == "SL" else 0xcccc00

        msg = (
            f"{emoji}【{title}】\n"
            f"銘柄: {symbol} {name}\n"
            f"決済価格: {price}\n"
            f"変化率: {pct if pct is not None else '---'}%\n"
            f"時刻: {jst_time}"
        )
        discord_send(msg, color)

    # 未知タイプはとりあえず200で返す
    else:
        print(f"ℹ 未対応イベント type={event_type} data={data}")

    return jsonify({"status": "ok"})

# ==========================
# 起動
# ==========================
if __name__ == "__main__":
    # RenderのPORT環境変数を信じるようにしてもいいけど
    # あなたの環境は 10000 で固定してるのでそのまま
    app.run(host="0.0.0.0", port=10000)

