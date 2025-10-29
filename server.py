# server.py
# ===============================
# TradingView -> Flask webhook受信 -> AI判断 -> Discord通知
# ===============================

from flask import Flask, request, jsonify
from datetime import datetime, timezone, timedelta
import os, json, requests

import ai_entry_logic
import ai_exit_logic
import position_manager
import orchestrator  # 既存のやつをそのまま使う（active_symbols管理とか）

JST = timezone(timedelta(hours=9))

app = Flask(__name__)

# Render 環境変数
SECRET_TOKEN = os.getenv("TV_SHARED_SECRET", "super_secret_token_please_match")
DISCORD_WEBHOOK_MAIN = os.getenv("DISCORD_WEBHOOK_MAIN", "")

# 銘柄の日本語名辞書（orchestratorと同じ場所/形式で持ってる前提）
# 例: data/symbol_names.json に {"7203.T": "トヨタ自動車"} みたいなのが入ってる想定
SYMBOL_NAMES_PATH = "data/symbol_names.json"
if os.path.exists(SYMBOL_NAMES_PATH):
    with open(SYMBOL_NAMES_PATH, "r", encoding="utf-8") as f:
        SYMBOL_NAMES = json.load(f)
else:
    SYMBOL_NAMES = {}

def jp_name(symbol: str) -> str:
    return SYMBOL_NAMES.get(symbol, symbol)

def jst_now_str():
    return datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")

def send_discord(msg: str, color: int = 0x00ccff):
    """
    Discordに日本語でEmbed送信
    """
    if not DISCORD_WEBHOOK_MAIN:
        print("⚠ Discord Webhook未設定")
        print(msg)
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
    try:
        resp = requests.post(DISCORD_WEBHOOK_MAIN, json=data, timeout=5)
        print(f"Discord送信 status={resp.status_code}")
    except Exception as e:
        print(f"Discord送信エラー: {e}")
        print("FAILED MSG >>>", msg)


@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.get_json()

    if not payload:
        return jsonify({"status": "error", "reason": "no data"}), 400

    # secretチェック
    if payload.get("secret") != SECRET_TOKEN:
        return jsonify({"status": "error", "reason": "invalid secret"}), 403

    event_type = payload.get("type", "")
    symbol     = payload.get("symbol", "")
    side       = payload.get("side", "")  # "BUY"/"SELL"
    price_now  = float(payload.get("price", 0))
    pct_now    = payload.get("pct_from_entry")  # Pine側でpct_from_entry送ってくる
    if pct_now is not None:
        try:
            pct_now = float(pct_now)
        except:
            pct_now = None

    print(f"[WEBHOOK] {event_type} {symbol} {side} {price_now} pct={pct_now} at {jst_now_str()}")

    # ==========================
    # 1) ENTRY_BUY / ENTRY_SELL
    # ==========================
    if event_type in ["ENTRY_BUY", "ENTRY_SELL"]:
        # Pineからもらった追加情報（出来高の強さとか勢いとか）
        vol_mult  = float(payload.get("vol_mult", 1.0))
        vwap      = float(payload.get("vwap", 0.0))
        atr       = float(payload.get("atr", 0.0))
        last_pct  = float(payload.get("last_pct", 0.0))

        # AIで「即エントリー採用 or 保留監視(shadow)」を判定
        accept, reason = ai_entry_logic.should_accept_entry(
            symbol,
            side,
            vol_mult,
            vwap,
            atr,
            last_pct
        )

        # ポジション開始を記録（status=real or shadow_pending）
        pos_info = position_manager.start_position(
            symbol=symbol,
            side=side,
            price=price_now,
            accepted_real=accept
        )

        # orchestrator側の管理にも反映（active_symbolsに入れる等）
        orchestrator.mark_symbol_active(symbol)

        # Discord通知（日本語・銘柄名入り・理由つき）
        if accept:
            # 本採用
            msg = (
                f"🟢エントリー確定\n"
                f"銘柄: {symbol} {jp_name(symbol)}\n"
                f"方向: {'買い' if side=='BUY' else '売り'}\n"
                f"価格: {price_now}\n"
                f"理由: {reason}\n"
                f"時刻: {jst_now_str()}"
            )
            send_discord(msg, 0x00ff00 if side=="BUY" else 0xff3333)
        else:
            # 保留監視（shadow）
            msg = (
                f"🕓エントリー保留監視中\n"
                f"銘柄: {symbol} {jp_name(symbol)}\n"
                f"方向: {'買い' if side=='BUY' else '売り'}\n"
                f"価格: {price_now}\n"
                f"理由: {reason}\n"
                f"※AIがしばらく後追い監視して、良ければ後出しで『エントリー』通知します"
            )
            send_discord(msg, 0xaaaaaa)

        return jsonify({"status": "ok"})

    # ==========================
    # 2) PRICE_TICK
    #    毎分のスナップショット
    #    → AI利確/損切/タイムアウト判定
    # ==========================
    elif event_type == "PRICE_TICK":
        tick = {
            "t": datetime.now(JST).isoformat(timespec="seconds"),
            "price": price_now,
            "pct": pct_now,
            "volume": payload.get("volume"),
            "vwap": payload.get("vwap"),
            "atr": payload.get("atr"),
            "mins_from_entry": payload.get("mins_from_entry"),
        }

        pos_before = position_manager.add_tick(symbol, tick)
        if not pos_before:
            # まだposition_managerに登録されてない＝shadowで弾かれて終了した/古い みたいなケース
            print(f"[INFO] PRICE_TICK for unknown or closed {symbol}")
            return jsonify({"status": "ok"})

        # すでにclose済なら何もしない
        if pos_before.get("closed"):
            return jsonify({"status": "ok"})

        # ===== AIに出口判定させる =====
        wants_exit, exit_info = ai_exit_logic.should_exit_now(pos_before)
        if wants_exit and exit_info:
            exit_type, exit_price = exit_info  # e.g. "AI_TP", 3050.5

            # クローズ処理（学習ログにも残す）
            closed_pos = position_manager.force_close(
                symbol,
                reason=exit_type,
                price_now=exit_price,
                pct_now=pct_now
            )

            orchestrator.mark_symbol_closed(symbol)

            # Discordに「AI利確/AI損切り/AIタイムアウト」報告
            if exit_type == "AI_TP":
                kind_label = "AI利確🎯"
                color = 0x33ccff
            elif exit_type == "AI_SL":
                kind_label = "AI損切り⚡"
                color = 0xff6666
            else:
                kind_label = "AIタイムアウト⏱"
                color = 0xcccc00

            msg = (
                f"{kind_label}\n"
                f"銘柄: {symbol} {jp_name(symbol)}\n"
                f"決済価格: {exit_price}\n"
                f"最終変化率: {round(pct_now,2) if pct_now is not None else '---'}%\n"
                f"時刻: {jst_now_str()}"
            )
            send_discord(msg, color)

        return jsonify({"status": "ok"})

    # ==========================
    # 3) TP / SL / TIMEOUT
    #    Pine側の保険エグジット
    #    （AIが閉じてなかったらここで最終的に閉める）
    # ==========================
    elif event_type in ["TP", "SL", "TIMEOUT"]:
        # まだ閉じてないなら閉じる
        closed_pos = position_manager.force_close(
            symbol,
            reason=event_type,
            price_now=price_now,
            pct_now=pct_now
        )

        orchestrator.mark_symbol_closed(symbol)

        # すでにAIで閉じてたら close_reason が AI_xxx のはずだから、重複通知はスキップ
        already_ai = closed_pos and closed_pos.get("close_reason","").startswith("AI_")
        if not already_ai:
            # 種別に応じて見出しと色
            if event_type == "TP":
                kind_label = "利確🎯"
                color = 0x33ccff
            elif event_type == "SL":
                kind_label = "損切り⚡"
                color = 0xff6666
            else:
                kind_label = "タイムアウト⏱"
                color = 0xcccc00

            msg = (
                f"{kind_label}\n"
                f"銘柄: {symbol} {jp_name(symbol)}\n"
                f"決済価格: {price_now}\n"
                f"最終変化率: {round(pct_now,2) if pct_now is not None else '---'}%\n"
                f"時刻: {jst_now_str()}"
            )
            send_discord(msg, color)

        return jsonify({"status": "ok"})

    # ==========================
    # それ以外
    # ==========================
    else:
        print(f"[INFO] 未対応event {event_type} payload={payload}")
        return jsonify({"status": "ok", "note": "unhandled"})
    

if __name__ == "__main__":
    # Render側はPORT環境変数渡してるはずだけど、今あなたの環境は10000固定でもOKだったよね
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
