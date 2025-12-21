import os
import csv
from datetime import datetime, timedelta
from utils.time_utils import jst_now, get_jst_now_str

TRADES_PATH = "data/trades.csv"

def _load_trades():
    if not os.path.exists(TRADES_PATH):
        return []
    with open(TRADES_PATH, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def _parse_iso(ts: str):
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None

def generate_daily_report():
    rows = _load_trades()
    now = jst_now()
    since = now - timedelta(days=1)

    picked = []
    for r in rows:
        t = _parse_iso(r.get("timestamp_exit", ""))
        if t and t >= since:
            picked.append(r)

    total = len(picked)
    pnl_sum = 0.0
    win = 0
    lines = []

    for r in picked[-50:]:
        rp = r.get("realized_pct", "")
        try:
            v = float(rp) if rp != "" else 0.0
        except Exception:
            v = 0.0
        pnl_sum += v
        if v > 0:
            win += 1

        lines.append(
            f"{r.get('timestamp_exit','?')} {r.get('symbol','?')} {r.get('side','?')} "
            f"{r.get('session','?')} realized:{r.get('realized_pct','')}% reason:{r.get('exit_reason','')}"
        )

    win_rate = (win / total * 100.0) if total else 0.0

    msg = (
        "📊 デイリーレポート（直近24h）\n"
        f"集計時刻(JST): {get_jst_now_str()}\n"
        f"件数: {total}\n"
        f"実現損益% 合計: {pnl_sum:.2f}%\n"
        f"勝率: {win_rate:.2f}%\n"
        "\n--- 明細（最大50件）---\n"
        + ("\n".join(lines) if lines else "（対象なし）")
    )
    return msg
