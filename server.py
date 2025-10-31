# ===============================
# TradingView Webhook -> Discord通知（日本語銘柄名対応）
# 通知は「本エントリー＆その後のAI決済のみ」
# shadow（保留監視）は通知しない
# さらに：shadow→real 昇格を実装（昇格通知あり）
# 昇格は「エントリー発生から PROMOTION_WINDOW_MIN 分以内のみ」許可
# ===============================

from flask import Flask, request, jsonify
from datetime import datetime, timezone, timedelta
import os, json, requests, csv

import ai_entry_logic
import ai_exit_logic
import position_manager
import orchestrator  # active_symbolsなど

JST = timezone(timedelta(hours=9))
app = Flask(__name__)

# ---- 環境変数
SECRET_TOKEN = os.getenv("TV_SHARED_SECRET", "super_secret_token_please_match")
DISCORD_WEBHOOK_MAIN = os.getenv("DISCORD_WEBHOOK_MAIN", "")

TRADE_LOG_PATH = "data/trade_log.csv"

# 昇格を許す時間（分）:
#   PROMOTION_WINDOW_MIN が無ければ、過去互換の AI_PROMOTE_WINDOW_MIN を見る（最後は 5 にフォールバック）
PROMOTION_WINDOW_MIN = float(
    os.getenv("PROMOTION_WINDOW_MIN", os.getenv("AI_PROMOTE_WINDOW_MIN", "5"))
)

# ---- 日本語銘柄名マップ
SYMBOL_NAMES_PATH = "data/symbol_names.json"
if os.path.exists(SYMBOL_NAMES_PATH):
    with open(SYMBOL_NAMES_PATH, "r", encoding="utf-8") as f:
        SYMBOL_NAMES = json.load(f)
else:
    SYMBOL_NAMES = {}

def jp_name(symbol: str) -> str:
    if not symbol:
        return symbol
    cand = [symbol]
    up = symbol.upper()
    if up not in cand: cand.append(up)
    if not up.endswith(".T"):
        cand.append(up + ".T")
    if up.endswith(".T"):
        cand.append(up[:-2])
    digits = "".join(ch for ch in up if ch.isalnum())
    if digits and digits not in cand:
        cand.append(digits)
    for k in cand:
        if k in SYMBOL_NAMES:
            return SYMBOL_NAMES[k]
    return symbol

def jst_now():
    return datetime.now(JST)

def jst_now_str():
    return jst_now().strftime("%Y/%m/%d %H:%M:%S")

def send_discord(msg: str, color: int = 0x00ccff):
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
    レポート系（weekly/monthly）が参照する列名に合わせて 'pnl_pct' を出力する。
    """
    os.makedirs("data", exist_ok=True)
    file_exists = os.path.exists(TRADE_LOG_PATH)
    with open(TRADE_LOG_PATH, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["timestamp","symbol","side","entry_price","exit_price","pnl_pct","reason"],
        )
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

# ---- health check
@app.route("/ping")
def ping():
    return jsonify({"ok": True, "now": jst_now_str()})

@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.get_json()
    if not payload:
        return jsonify({"status": "error", "reason": "no data"}), 400

    if (payload.get("secret") or "").strip() != SECRET_TOKEN:
        return jsonify({"status": "error", "reason": "invalid secret"}), 403

    # 受信値の正規化
    raw_event_type = (payload.get("type", "") or "").strip()
    etype = raw_event_type.upper()
    alias = {
        "ENTRY_BUY_NOW": "ENTRY_BUY",
        "ENTRY_SELL_NOW": "ENTRY_SELL",
        "BUY_ENTRY": "ENTRY_BUY",
        "SELL_ENTRY": "ENTRY_SELL",
        "TICK": "PRICE_TICK",
        "PRICEFOLLOW": "PRICE_TICK",
        "FOLLOW_TICK": "PRICE_TICK",
        "STEP_UP": "PRICE_TICK",
        "STEP_DOWN": "PRICE_TICK",
    }
    etype = alias.get(etype, etype)

    symbol = (payload.get("symbol", "") or "").strip()

    side = (payload.get("side", "") or "").strip().upper()
    if side in ("LONG", "BUY_SIGNAL"):
        side = "BUY"
    elif side in ("SHORT", "SELL_SIGNAL"):
        side = "SELL"

    # 数値系は安全にパース
    price_now = payload.get("price", 0)
    try:
        price_now = float(price_now)
    except Exception:
        price_now = 0.0

    pct_now = payload.get("pct_from_entry")
    try:
        pct_now = float(pct_now) if pct_now is not None else None
    except Exception:
        pct_now = None

    # Pine から来る「エントリー発生ms」
    entry_ts_ms = payload.get("entry_ts")
    try:
        entry_ts_ms = int(entry_ts_ms) if entry_ts_ms is not None else None
    except Exception:
        entry_ts_ms = None

    print(f"[WEBHOOK] etype={etype} raw={raw_event_type} {symbol} {side} {price_now} pct={pct_now} at {jst_now_str()}")

    # ==========================
    # 1) ENTRY_BUY / ENTRY_SELL
    # ==========================
    if etype in ["ENTRY_BUY", "ENTRY_SELL"]:
        # Pineから来る文脈値
        def _to_float(v, default=0.0):
            try:
                return float(v)
            except Exception:
                return default

        vol_mult = _to_float(payload.get("vol_mult", 1.0), 1.0)
        vwap     = _to_float(payload.get("vwap", 0.0), 0.0)
        atr      = _to_float(payload.get("atr", 0.0), 0.0)
        last_pct = _to_float(payload.get("last_pct", 0.0), 0.0)

        accept, reason = ai_entry_logic.should_accept_entry(
            symbol, side, vol_mult, vwap, atr, last_pct
        )

        _ = position_manager.start_position(
            symbol=symbol,
            side=side,
            price=price_now,
            accepted_real=accept
        )

        orchestrator.mark_symbol_active(symbol)

        if accept:
            msg = (
                f"🟢エントリー確定\n"
                f"銘柄: {symbol} {jp_name(symbol)}\n"
                f"方向: {'買い' if side=='BUY' else '売り'}\n"
                f"価格: {price_now}\n"
                f"理由: {reason}\n"
                f"時刻: {jst_now_str()}"
            )
            send_discord(msg, 0x00ff00 if side=="BUY" else 0xff3333)

            append_trade_log({
                "timestamp": jst_now().isoformat(timespec="seconds"),
                "symbol": symbol,
                "side": side,
                "entry_price": price_now,
                "exit_price": "",
                "pnl_pct": "",
                "reason": "ENTRY",
            })

        # accept=False（shadow）は通知しない
        return jsonify({"status": "ok"})

    # ==========================
    # 2) PRICE_TICK
    # ==========================
    elif etype == "PRICE_TICK":
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
        if not pos_before or pos_before.get("closed"):
            return jsonify({"status": "ok"})

        # ----- まず shadow の昇格判定だけ先にやる -----
        if pos_before.get("status") == "shadow_pending":
            # 昇格は「エントリー後 PROMOTION_WINDOW_MIN 分以内」だけ許可
            mins_from_entry = tick.get("mins_from_entry")
            try:
                mins_from_entry = float(mins_from_entry) if mins_from_entry is not None else None
            except Exception:
                mins_from_entry = None

            within_window = False
            if mins_from_entry is not None:
                # Pine 側で昼休み補正済みの「経過分」
                within_window = mins_from_entry <= PROMOTION_WINDOW_MIN
            elif entry_ts_ms is not None:
                # 念のためのフォールバック（サーバー時刻とエントリーmsから算出）
                now_ms = int(datetime.now(JST).timestamp() * 1000)
                within_window = (now_ms - entry_ts_ms) <= int(PROMOTION_WINDOW_MIN * 60 * 1000)

            if within_window and ai_entry_logic.should_promote_to_real(pos_before):
                # 昇格実行
                promoted = position_manager.promote_to_real(symbol)
                if promoted and not promoted.get("closed"):
                    # 昇格エントリー通知
                    promote_side = promoted.get("side", side)
                    msg = (
                        f"🟢エントリー確定（昇格）\n"
                        f"銘柄: {symbol} {jp_name(symbol)}\n"
                        f"方向: {'買い' if promote_side=='BUY' else '売り'}\n"
                        f"価格: {price_now}\n"
                        f"理由: 後追い監視から本採用に昇格\n"
                        f"時刻: {jst_now_str()}"
                    )
                    send_discord(msg, 0x00ff00 if promote_side=="BUY" else 0xff3333)

                    append_trade_log({
                        "timestamp": jst_now().isoformat(timespec="seconds"),
                        "symbol": symbol,
                        "side": promote_side,
                        "entry_price": promoted.get("entry_price", price_now),
                        "exit_price": "",
                        "pnl_pct": "",
                        "reason": "ENTRY",
                    })

                # 昇格判定の後でも、shadow のままなら以降は無視
                # real になってもこのTickでは決済判定は走らせずOK（次Tickからで十分）
                return jsonify({"status": "ok"})
            else:
                # 昇格不可（時間外 or 条件不足） → このTickは何もしない
                return jsonify({"status": "ok"})

        # ----- ここからは real のみ（AIのTP/SL/TOを判定） -----
        if pos_before.get("status") != "real":
            return jsonify({"status": "ok"})

        wants_exit, exit_info = ai_exit_logic.should_exit_now(pos_before)
        if wants_exit and exit_info:
            exit_type, exit_price = exit_info

            closed_pos = position_manager.force_close(
                symbol, reason=exit_type, price_now=exit_price, pct_now=pct_now
            )
            orchestrator.mark_symbol_closed(symbol)

            if exit_type == "AI_TP":
                kind_label = "AI利確🎯"; color = 0x33ccff
            elif exit_type == "AI_SL":
                kind_label = "AI損切り⚡"; color = 0xff6666
            else:
                kind_label = "AIタイムアウト⏱"; color = 0xcccc00

            msg = (
                f"{kind_label}\n"
                f"銘柄: {symbol} {jp_name(symbol)}\n"
                f"決済価格: {exit_price}\n"
                f"最終変化率: {round(pct_now,2) if pct_now is not None else '---'}%\n"
                f"時刻: {jst_now_str()}"
            )
            send_discord(msg, color)

            append_trade_log({
                "timestamp": jst_now().isoformat(timespec="seconds"),
                "symbol": symbol,
                "side": closed_pos.get("side", ""),
                "entry_price": closed_pos.get("entry_price", ""),
                "exit_price": exit_price,
                "pnl_pct": round(pct_now,2) if pct_now is not None else "",
                "reason": exit_type,
            })

        return jsonify({"status": "ok"})

    # ==========================
    # 3) TP / SL / TIMEOUT  (Pine側の保険決済イベント)
    # ==========================
    elif etype in ["TP", "SL", "TIMEOUT"]:
        closed_pos = position_manager.force_close(
            symbol, reason=etype, price_now=price_now, pct_now=pct_now
        )
        orchestrator.mark_symbol_closed(symbol)

        already_ai = closed_pos and str(closed_pos.get("close_reason","")).startswith("AI_")
        if not already_ai:
            if etype == "TP":
                kind_label = "利確🎯"; color = 0x33ccff
            elif etype == "SL":
                kind_label = "損切り⚡"; color = 0xff6666
            else:
                kind_label = "タイムアウト⏱"; color = 0xcccc00

            msg = (
                f"{kind_label}\n"
                f"銘柄: {symbol} {jp_name(symbol)}\n"
                f"決済価格: {price_now}\n"
                f"最終変化率: {round(pct_now,2) if pct_now is not None else '---'}%\n"
                f"時刻: {jst_now_str()}"
            )
            send_discord(msg, color)

            append_trade_log({
                "timestamp": jst_now().isoformat(timespec="seconds"),
                "symbol": symbol,
                "side": closed_pos.get("side", "") if closed_pos else "",
                "entry_price": closed_pos.get("entry_price", "") if closed_pos else "",
                "exit_price": price_now,
                "pnl_pct": round(pct_now,2) if pct_now is not None else "",
                "reason": etype,
            })

        return jsonify({"status": "ok"})

    else:
        known = ["ENTRY_BUY","ENTRY_SELL","PRICE_TICK","TP","SL","TIMEOUT"]
        print(f"[INFO] 未対応event etype={etype} (raw={raw_event_type}) known={known} payload={payload}")
        return jsonify({"status": "ok", "note": "unhandled"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
