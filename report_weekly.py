import os
import csv
from datetime import datetime, timedelta
from utils.time_utils import jst_now, get_jst_now_str

# ✅ 最小修正：このファイル位置基準で data/trades.csv を指す（cwd依存を排除）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRADES_PATH = os.path.join(BASE_DIR, "data", "trades.csv")

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

def generate_weekly_report():
    rows = _load_trades()
    now = jst_now()
    since = now - timedelta(days=7)

    picked = []
    for r in rows:
        t = _parse_iso(r.get("timestamp_exit", ""))
        if t and t >= since:
            picked.append(r)

    total = len(picked)
    pnl_sum = 0.0
    win = 0

    for r in picked:
        rp = r.get("realized_pct", "")
        try:
            v = float(rp) if rp != "" else 0.0
        except Exception:
            v = 0.0
        pnl_sum += v
        if v > 0:
            win += 1

    win_rate = (win / total * 100.0) if total else 0.0

    msg = (
        "📅 ウィークリーレポート（直近7日）\n"
        f"集計時刻(JST): {get_jst_now_str()}\n"
        f"件数: {total}\n"
        f"実現損益% 合計: {pnl_sum:.2f}%\n"
        f"勝率: {win_rate:.2f}%\n"
    )
    return msg
