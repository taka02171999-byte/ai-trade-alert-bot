# learning_logger.py
#
# ポジションがクローズしたときに、その内容を学習用ログとして追記保存する。
# 出力形式は data/learning_log.jsonl に1行1JSONで積んでいく。

import os
import json
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
LEARN_LOG_PATH = "data/learning_log.jsonl"


def _now_jst_iso():
    return datetime.now(JST).isoformat(timespec="seconds")


def log_position(final_pos: dict):
    """
    final_pos: position_manager.force_close() 後などで取得できるポジション状態。
    期待してる主なキー:
      symbol
      side ("BUY"/"SELL")
      status ("real"/"shadow_pending"/"shadow_closed")
      entry_price
      entry_time
      close_price
      close_time
      close_reason ("AI_TP","AI_SL","AI_TIMEOUT","expired_pending","TP","SL","TIMEOUT",...)
      ticks: [ {t, price, pct, mins_from_entry, volume, vwap, atr}, ... ]
    """

    if not final_pos:
        return

    # ticksの最後の状態から最終リターンなど推定
    ticks = final_pos.get("ticks", [])
    last_tick = ticks[-1] if ticks else {}

    record = {
        "logged_at": _now_jst_iso(),

        # どの銘柄/どっち方向/どの扱いだったか
        "symbol": final_pos.get("symbol"),
        "side": final_pos.get("side"),
        "status": final_pos.get("status"),

        # エントリー情報
        "entry_price": final_pos.get("entry_price"),
        "entry_time": final_pos.get("entry_time"),

        # クローズ情報
        "close_price": final_pos.get("close_price"),
        "close_time": final_pos.get("close_time"),
        "close_reason": final_pos.get("close_reason"),

        # 最終状態系
        "final_pct": last_tick.get("pct"),
        "final_mins_from_entry": last_tick.get("mins_from_entry"),

        # 市場コンテキストっぽいもの
        "final_vwap": last_tick.get("vwap"),
        "final_atr": last_tick.get("atr"),
        "final_volume": last_tick.get("volume"),

        # 生のtick列も保存しておく(学習に使える)。めっちゃ長くなるなら圧縮もOK。
        "ticks": ticks,
    }

    os.makedirs(os.path.dirname(LEARN_LOG_PATH), exist_ok=True)
    with open(LEARN_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
