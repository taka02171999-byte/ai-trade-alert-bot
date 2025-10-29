# server.py
# ========================================
# AIりんご式 Entry+Follow サーバー (shadow監視 + 後追い昇格 + AI決済)
# TradingView(Pine) → Flask(Webhook) → orchestratorフィルタ → Discord
# ========================================

from flask import Flask, request, jsonify
from datetime import datetime, timedelta, timezone
import json
import requests
import os
import logging

import orchestrator          # 既存のやつそのまま使う
import position_manager      # 新規ファイル
import ai_exit_logic         # 新規ファイル

# ==========================
# ログフィルタ (UptimeRobotのpingとかHEADをコンソールに出さない)
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

DISCORD_WEBHOOK_MAIN   = os.getenv("DISCORD_WEBHOOK_MAIN", "")
DISCORD_WEBHOOK_REPORT = os.getenv("DISCORD_WEBHOOK_REPORT", "")
MARKET_CLOSE_HHMM      = os.getenv("MARKET_CLOSE_HHMM", "15:25")
SECRET_TOKEN           = os.getenv("TV_SHARED_SECRET", "super_secret_token_please_match")
TOP_SYMBOL_LIMIT       = int(os.getenv("TOP_SYMBOL_LIMIT", "10"))

app = Flask(__name__)

# ==========================
# 銘柄名辞書読み込み
# ==========================
with open("data/symbol_names.json", "r", encoding="utf-8") as f:
    SYMBOL_NAMES = json.load(f)

def pretty_name(symbol: str):
    return SYMBOL_NAMES.get(symbol, symbol)

def jst_now_str():
    return datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")

# ==========================
# Discord送信用ユーティリティ
# ==========================
def discord_embed(msg, color=0x00ffcc, title="AIりんご式トレード通知"):
    if not DISCORD_WEBHOOK_MAIN:
        print("⚠ Discord Webhook URL未設定")
        return
    data = {
        "embeds": [
            {
                "title": title,
                "description": msg,
                "color": color,
                "footer": {"text": "AIりんご式 | " + jst_now_str()},
            }
        ]
    }
    try:
        resp = requests.post(DISCORD_WEBHOOK_MAIN, json=data, timeout=5)
        print(f"→ Discord Webhook 送信 status={resp.status_code}")
    except Exception as e:
        print(f"Discord送信エラー: {e}")

def send_entry_alert(symbol, side, price, reason_label):
    """
    エントリー通知（即エントリー or 後追いエントリー）
    """
    emoji = "🟢" if side == "BUY" else "🔴"
    msg = (
        f"{emoji}【{reason_label}】\n"
        f"銘柄: {symbol} {pretty_name(symbol)}\n"
        f"方向: {'買い' if side=='BUY' else '売り'}\n"
        f"価格: {price}\n"
        f"時刻: {jst_now_str()}"
    )
    discord_embed(
        msg,
        0x00ff00 if side == "BUY" else 0xff3333
    )

def send_close_alert(symbol, exit_type, price, pct=None, ai_flag=False):
    """
    クローズ通知（AI利確/損切り/タイムアウト or Pine由来のTP/SL/TIMEOUT）
    """
    # exit_type: "TP","SL","TIMEOUT","AI_TP","AI_SL","AI_TIMEOUT"
    if exit_type in ["TP","AI_TP"]:
        emoji="🎯"; title="利確"
        color=0x33ccff
    elif exit_type in ["SL","AI_SL"]:
        emoji="⚡"; title="損切り"
        color=0xff6666
    else:
        emoji="⏱"; title="タイムアウト"
        color=0xcccc00

    if ai_flag:
        title = "AI" + title  # "AI利確" / "AI損切り" / "AIタイムアウト"

    msg = (
        f"{emoji}【{title}】\n"
        f"銘柄: {symbol} {pretty_name(symbol)}\n"
        f"決済価格: {price}\n"
        f"変化率: {pct if pct is not None else '---'}%\n"
        f"時刻: {jst_now_str()}"
    )
    discord_embed(msg, color)

# ==========================
# Webhookエンドポイント (TradingView → ここ)
# ==========================
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "reason": "no data"}), 400

    # Pineのsecretチェック
    if data.get("secret") != SECRET_TOKEN:
        return jsonify({"status": "error", "reason": "invalid secret"}), 403

    event_type   = data.get("type", "")
    symbol       = data.get("symbol", "")
    price        = data.get("price")
    pct          = data.get("pct_from_entry")
    side         = data.get("side", "")
    step_label   = data.get("step_label", "")
    mins_from_en = data.get("mins_from_entry")  # PRICE_TICKでくる
    # entry_ts は今のロジックでは保存用途があればtickに入れられる

    print(f"[WEBHOOK] {event_type} {symbol} price={price} pct={pct} step={step_label} {jst_now_str()}")

    # ===== 1) ENTRY_BUY / ENTRY_SELL =====
    if event_type in ["ENTRY_BUY", "ENTRY_SELL"]:
        # orchestratorに「この銘柄いま採用していい？」を聞く
        accept, reject_reason, _tp_guess, _sl_guess = orchestrator.should_accept_signal(symbol, side)

        pos_info = position_manager.start_position(
            symbol=symbol,
            side=side,
            entry_price=price,
            accepted_real=accept
        )

        if accept:
            # 採用されたトレード：即Discordにエントリー通知
            send_entry_alert(symbol, side, price, reason_label="エントリー")
            orchestrator.mark_symbol_active(symbol)
        else:
            # 一旦はDiscordに出さない。shadow_pendingとして数分スカウト対象にする
            print(f"[INFO] shadow_pending start for {symbol} reason={reject_reason}")

        return jsonify({"status": "ok", "accepted": accept, "reason": reject_reason})

    # ===== 2) PRICE_TICK =====
    elif event_type == "PRICE_TICK":
        # 毎分のスナップショットをポジションに追加
        tick = {
            "t": datetime.now(JST).isoformat(timespec="seconds"),
            "price": price,
            "pct": pct,
            "mins_from_entry": mins_from_en,
            "step": step_label,
            "volume": data.get("volume"),
            "vwap": data.get("vwap"),
            "atr": data.get("atr"),
        }

        pos_before = position_manager.add_tick(symbol, tick)

        if (not pos_before) or pos_before.get("closed"):
            # そもそも保有扱いじゃない or すでに閉じてるなら、ログ出して終わり
            print(f"📊 PRICE_TICK(ignore/closed) {symbol} pct={pct}")
            return jsonify({"status": "ok"})

        # --- shadow_pendingの扱い ---
        if pos_before["status"] == "shadow_pending":
            promoted = False

            # (1) まず監視リミット切れかどうか（数分追跡しても伸びなかった）
            _, expired = position_manager.maybe_expire_shadow(symbol)
            if expired:
                print(f"[PENDING-EXPIRE] {symbol} expired_pending -> closed")
                return jsonify({"status": "ok"})

            # (2) 期限内なら、いま昇格すべきか？
            if ai_exit_logic.should_promote_to_real(pos_before):
                pos_now = position_manager.promote_to_real(symbol)
                if pos_now and pos_now.get("status") == "real":
                    promoted = True
                    # 昇格の瞬間に「(後追い)エントリー」通知をDiscordへ
                    send_entry_alert(
                        symbol,
                        pos_now.get("side", "BUY"),
                        price,
                        reason_label="(後追い)エントリー"
                    )
                    orchestrator.mark_symbol_active(symbol)

            if not promoted:
                # まだ保留＆監視継続中の段階
                print(f"[PENDING] still watching {symbol} pct={pct}")
                # ※このあとAIのexit判定にも進む（保留中でも即死レベルなら切る）

        # --- AIによる利確・損切り・タイムアウト判定 ---
        pos_latest = position_manager.get_position(symbol)
        wants_exit, exit_info = ai_exit_logic.should_exit_now(pos_latest)

        if wants_exit and exit_info:
            exit_type, exit_price = exit_info  # ex: ("AI_TP", 3050.5)

            closed_pos = position_manager.force_close(
                symbol,
                reason=exit_type,
                price_now=exit_price
            )
            if closed_pos:
                # この銘柄は冷却期間に入れる
                orchestrator.mark_symbol_closed(symbol)

                # Discordに「AI利確/AI損切り/AIタイムアウト」を送る
                send_close_alert(
                    symbol,
                    exit_type,
                    exit_price,
                    pct=pct,
                    ai_flag=True
                )

        return jsonify({"status": "ok"})

    # ===== 3) STEP_UP / STEP_DOWN =====
    elif event_type in ["STEP_UP", "STEP_DOWN"]:
        # 今はDiscordに投げない。学習用ログに使えるなら後で使う
        print(f"↕ STEP {event_type} {symbol}: pct={pct} ({step_label})")
        return jsonify({"status": "ok"})

    # ===== 4) TP / SL / TIMEOUT =====
    elif event_type in ["TP", "SL", "TIMEOUT"]:
        # これはPine側の「保険」クローズ
        closed_pos = position_manager.force_close(
            symbol,
            reason=event_type,
            price_now=price
        )

        # もし既にAI側でclose_reasonがAI_系なら二重でDiscordに送らない
        if closed_pos and not str(closed_pos.get("close_reason", "")).startswith("AI_"):
            orchestrator.mark_symbol_closed(symbol)
            send_close_alert(
                symbol,
                event_type,
                price,
                pct=pct,
                ai_flag=False
            )
        else:
            print(f"[SKIP CLOSE ALERT] already closed by AI for {symbol}")

        return jsonify({"status": "ok"})

    # ===== その他のタイプ =====
    else:
        print(f"ℹ 未対応イベント type={event_type} data={data}")
        return jsonify({"status": "ok", "note": "unhandled type"})


# ==========================
# サーバー起動
# ==========================
if __name__ == "__main__":
    # あなたのローカル/Renderで動かす想定
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
