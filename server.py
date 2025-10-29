# server.py
# ===============================
# TradingView -> Flask webhook受信 -> AI判断 -> Discord通知 + trade_log記録
# ===============================

from flask import Flask, request, jsonify
from datetime import datetime, timezone, timedelta
import os, json, requests, csv

import ai_entry_logic
import ai_exit_logic
import position_manager
import orchestrator  # active_symbolsとかクールダウン管理

JST = timezone(timedelta(hours=9))

app = Flask(__name__)

# Render 環境変数
SECRET_TOKEN = os.getenv("TV_SHARED_SECRET", "super_secret_token_please_match")
DISCORD_WEBHOOK_MAIN = os.getenv("DISCORD_WEBHOOK_MAIN", "")

TRADE_LOG_PATH = "data/trade_log.csv"

# 銘柄の日本語名辞書
SYMBOL_NAMES_PATH = "data/symbol_names.json"
if os.path.exists(SYMBOL_NAMES_PATH):
    with open(SYMBOL_NAMES_PATH, "r", encoding="utf-8") as f:
        SYMBOL_NAMES = json.load(f)
else:
    SYMBOL_NAMES = {}

def jp_name(symbol: str) -> str:
    return SYMBOL_NAMES.get(symbol, symbol)

def jst_now():
    return datetime.now(JST)

def jst_now_str():
    return jst_now().strftime("%Y/%m/%d %H:%M:%S")

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

def append_trade_log(row: dict):
    """
    trade_log.csv に1行追記する。
    rowは {
      "timestamp": ISO文字列,
      "symbol": "...",
      "side": "BUY"/"SELL",
      "entry_price": ...,
      "exit_price": ...,
      "pnl_pct": ...,
      "reason": "ENTRY" / "AI_TP" / "AI_SL" / ...,
    }
    """
    os.makedirs("data", exist_ok=True)

    file_exists = os.path.exists(TRADE_LOG_PATH)
    with open(TRADE_LOG_PATH, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "timestamp",
                "symbol",
                "side",
                "entry_price",
                "exit_price",
                "pnl_pct",
                "reason",
            ],
        )
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


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
        # Pineから来た追加情報（勢いとか）
        vol_mult  = float(payload.get("vol_mult", 1.0))
        vwap      = float(payload.get("vwap", 0.0))
        atr       = float(payload.get("atr", 0.0))
        last_pct  = float(payload.get("last_pct", 0.0))

        # AIで「即エントリー(=real)か、とりあえずshadow_pendingか」を判定
        accept, reason = ai_entry_logic.should_accept_entry(
            symbol,
            side,
            vol_mult,
            vwap,
            atr,
            last_pct
        )

        # ポジション開始を記録
        pos_info = position_manager.start_position(
            symbol=symbol,
            side=side,
            price=price_now,
            accepted_real=accept
        )

        # active_symbolsに入れる（クールダウン管理など用）
        orchestrator.mark_symbol_active(symbol)

        # Discord通知
        if accept:
            # 本採用（"🟢エントリー確定"）
            msg = (
                f"🟢エントリー確定\n"
                f"銘柄: {symbol} {jp_name(symbol)}\n"
                f"方向: {'買い' if side=='BUY' else '売り'}\n"
                f"価格: {price_now}\n"
                f"理由: {reason}\n"
                f"時刻: {jst_now_str()}"
            )
            send_discord(msg, 0x00ff00 if side=="BUY" else 0xff3333)

            # ★ここでログ行を追加（ENTRYとして記録）
            append_trade_log({
                "timestamp": jst_now().isoformat(timespec="seconds"),
                "symbol": symbol,
                "side": side,
                "entry_price": price_now,
                "exit_price": "",
                "pnl_pct": "",
                "reason": "ENTRY",
            })

        else:
            # 保留（shadowウォッチ）→これはレポには入れたくないのでログしない
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
            # 未登録 or すでに閉じたやつかも
            print(f"[INFO] PRICE_TICK for unknown or closed {symbol}")
            return jsonify({"status": "ok"})

        # もう閉じてたら何もしない
        if pos_before.get("closed"):
            return jsonify({"status": "ok"})

        # ===== AI出口判定 =====
        wants_exit, exit_info = ai_exit_logic.should_exit_now(pos_before)
        if wants_exit and exit_info:
            exit_type, exit_price = exit_info  # e.g. ("AI_TP", 3050.5)

            # クローズ処理（学習ログにも保存される）
            closed_pos = position_manager.force_close(
                symbol,
                reason=exit_type,
                price_now=exit_price,
                pct_now=pct_now
            )

            orchestrator.mark_symbol_closed(symbol)

            # Discord通知
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

            # ★ここでログ行を追加（EXITとして記録）
            append_trade_log({
                "timestamp": jst_now().isoformat(timespec="seconds"),
                "symbol": symbol,
                "side": closed_pos.get("side", side),
                "entry_price": closed_pos.get("entry_price", ""),
                "exit_price": exit_price,
                "pnl_pct": round(pct_now,2) if pct_now is not None else "",
                "reason": exit_type,
            })

        return jsonify({"status": "ok"})

    # ==========================
    # 3) TP / SL / TIMEOUT
    #    Pine側の保険エグジット
    # ==========================
    elif event_type in ["TP", "SL", "TIMEOUT"]:
        closed_pos = position_manager.force_close(
            symbol,
            reason=event_type,
            price_now=price_now,
            pct_now=pct_now
        )

        orchestrator.mark_symbol_closed(symbol)

        already_ai = closed_pos and closed_pos.get("close_reason","").startswith("AI_")
        if not already_ai:
            # 通知内容
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

            # ★ここでログ行を追加（EXITとして記録）
            append_trade_log({
                "timestamp": jst_now().isoformat(timespec="seconds"),
                "symbol": symbol,
                "side": closed_pos.get("side", side) if closed_pos else side,
                "entry_price": closed_pos.get("entry_price", "") if closed_pos else "",
                "exit_price": price_now,
                "pnl_pct": round(pct_now,2) if pct_now is not None else "",
                "reason": event_type,
            })

        return jsonify({"status": "ok"})

    # ==========================
    # その他
    # ==========================
    else:
        print(f"[INFO] 未対応event {event_type} payload={payload}")
        return jsonify({"status": "ok", "note": "unhandled"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
