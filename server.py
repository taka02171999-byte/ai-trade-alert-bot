# server.py
# ===============================
# TradingView → Flask(Webhook) → AI判断 → Discord通知
# - ENTRY_BUY/SELL: 今すぐ入る？とりあえず保留？を決める
# - PRICE_TICK: 1分ごとの状態から「AI利確/損切り/タイムアウト」判断
# - shadow_pendingだったやつを後追いで昇格させて“エントリー通知”もできる
# - Pine側のTP/SL/TIMEOUTは最終保険
# ===============================

from flask import Flask, request, jsonify
from datetime import datetime, timezone, timedelta
import os, json, requests

import ai_entry_logic
import ai_exit_logic
import position_manager
import orchestrator  # active_symbols管理とか cooldown入れるやつ

JST = timezone(timedelta(hours=9))
app = Flask(__name__)

# ----- 環境変数 -----
SECRET_TOKEN = os.getenv("TV_SHARED_SECRET", "super_secret_token_please_match")
DISCORD_WEBHOOK_MAIN = os.getenv("DISCORD_WEBHOOK_MAIN", "")

# ----- 銘柄の日本語名辞書 -----
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

    # TradingViewとの共有シークレットチェック
    if payload.get("secret") != SECRET_TOKEN:
        return jsonify({"status": "error", "reason": "invalid secret"}), 403

    event_type = payload.get("type", "")
    symbol     = payload.get("symbol", "")
    side       = payload.get("side", "")  # "BUY"/"SELL"
    # Pineが送ってきたエントリー価格/最新価格
    try:
        price_now = float(payload.get("price", 0) or 0)
    except:
        price_now = 0.0

    # Pineが送ってくる「エントリーからの％」(SELLの場合は有利側を+にしてくれてる)
    raw_pct = payload.get("pct_from_entry")
    try:
        pct_now = float(raw_pct)
    except:
        pct_now = None

    print(f"[WEBHOOK] {event_type} {symbol} side={side} price={price_now} pct={pct_now} at {jst_now_str()}")

    # ============================================================
    # 1) ENTRY_BUY / ENTRY_SELL : 新規シグナル
    # ============================================================
    if event_type in ["ENTRY_BUY", "ENTRY_SELL"]:
        # Pine側から送ってほしい情報（安全にfloat化しとく）
        def safe_float(x, default=0.0):
            try:
                return float(x)
            except:
                return default

        vol_mult = safe_float(payload.get("vol_mult", 1.0), 1.0)      # 出来高スパイク倍率
        vwap     = safe_float(payload.get("vwap", 0.0), 0.0)
        atr      = safe_float(payload.get("atr", 0.0), 0.0)
        last_pct = safe_float(payload.get("last_pct", 0.0), 0.0)       # 直近5分の伸び率とか

        # AIで「即リアル or 保留シャドウ」を判定
        accept, reason = ai_entry_logic.should_accept_entry(
            symbol, side, vol_mult, vwap, atr, last_pct
        )

        # ポジションを記録（status="real" or "shadow_pending"）
        position_manager.start_position(
            symbol=symbol,
            side=side,
            price=price_now,
            accepted_real=accept
        )

        # orchestrator 側の追跡リストにも登録だけはする
        orchestrator.mark_symbol_active(symbol)

        # Discordへ
        if accept:
            # 即IN
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
            # 保留監視
            msg = (
                f"🕓エントリー保留監視中\n"
                f"銘柄: {symbol} {jp_name(symbol)}\n"
                f"方向: {'買い' if side=='BUY' else '売り'}\n"
                f"価格: {price_now}\n"
                f"理由: {reason}\n"
                f"※AIが数分間後追い監視。よく育ったら後出しで正式エントリー通知します。"
            )
            send_discord(msg, 0xaaaaaa)

        return jsonify({"status": "ok"})

    # ============================================================
    # 2) PRICE_TICK : 毎分のスナップショット
    #    → shadow_pendingの昇格チェック
    #    → AIの利確/損切り/タイムアウト判断
    # ============================================================
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
            # 既にクローズ済み/存在しない
            print(f"[INFO] PRICE_TICK for unknown {symbol}")
            return jsonify({"status": "ok"})

        if pos_before.get("closed"):
            # もう閉じてるならここで終了
            return jsonify({"status": "ok"})

        # --------- (A) shadow_pending → real 昇格チェック ---------
        if pos_before.get("status") == "shadow_pending":
            if ai_entry_logic.should_promote_to_real(pos_before):
                # 格上げ
                pos_after = position_manager.promote_to_real(symbol)
                if pos_after and pos_after.get("status") == "real":
                    # Discordに「後追いだけど正式エントリー入りました」って出す
                    side_now = pos_after.get("side", side)
                    msg = (
                        f"🟢(後追い)エントリー確定\n"
                        f"銘柄: {symbol} {jp_name(symbol)}\n"
                        f"方向: {'買い' if side_now=='BUY' else '売り'}\n"
                        f"今の価格: {price_now}\n"
                        f"時刻: {jst_now_str()}\n"
                        f"※保留監視から昇格"
                    )
                    send_discord(msg, 0x00ff00 if side_now=="BUY" else 0xff3333)

                    orchestrator.mark_symbol_active(symbol)

        # （pos_latestを取り直す。昇格後の状態で判断したい）
        pos_now = position_manager.get_position(symbol)

        # --------- (B) AIによる出口判定 ---------
        wants_exit, exit_info = ai_exit_logic.should_exit_now(pos_now)
        if wants_exit and exit_info:
            exit_type, exit_price = exit_info  # "AI_TP"とか, 決済価格

            closed_pos = position_manager.force_close(
                symbol,
                reason=exit_type,
                price_now=exit_price,
                pct_now=pct_now
            )

            orchestrator.mark_symbol_closed(symbol)

            # Discord通知もAI用の文面
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

    # ============================================================
    # 3) TP / SL / TIMEOUT : Pine側の保険エグジット
    #    → まだ閉じてないならここで閉める
    # ============================================================
    elif event_type in ["TP", "SL", "TIMEOUT"]:
        closed_pos = position_manager.force_close(
            symbol,
            reason=event_type,
            price_now=price_now,
            pct_now=pct_now
        )

        orchestrator.mark_symbol_closed(symbol)

        # すでにAIで閉じてた場合（close_reasonがAI_で始まる）はもうDiscord報告済なので二重通知しない
        already_ai = closed_pos and str(closed_pos.get("close_reason", "")).startswith("AI_")
        if not already_ai:
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

    # ============================================================
    # それ以外
    # ============================================================
    else:
        print(f"[INFO] 未対応イベント {event_type} payload={payload}")
        return jsonify({"status": "ok", "note": "unhandled"})


if __name__ == "__main__":
    # RenderのStartコマンドがgunicornじゃなくpython単体のとき用
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
