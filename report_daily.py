import os
import csv
from datetime import datetime, timedelta
from utils.time_utils import get_jst_now_str

TRADE_LOG = "data/trade_log.csv"

def _load_trades():
    rows = []
    if not os.path.exists(TRADE_LOG):
        return rows
    with open(TRADE_LOG, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows

def _parse_iso(ts):
    # "2025-10-29T09:00:00+09:00" みたいなの or "2025-10-29T00:00:00"
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        # 失敗したらUTC扱いにしておく
        return datetime.utcnow()

def _summarize(rows, hours_back):
    since_dt = datetime.utcnow() - timedelta(hours=hours_back)

    picked = []
    for r in rows:
        t = _parse_iso(r.get("timestamp", ""))
        if t >= since_dt:
            picked.append(r)

    total_cnt = len(picked)
    win_cnt = 0
    pnl_sum = 0.0

    detail_lines = []

    for r in picked:
        pnl_pct = r.get("pnl_pct", "")
        try:
            pnl_val = float(pnl_pct) if pnl_pct != "" else 0.0
        except:
            pnl_val = 0.0

        pnl_sum += pnl_val
        if pnl_val > 0:
            win_cnt += 1

        detail_lines.append(
            f"{r.get('timestamp','?')} {r.get('symbol','?')} {r.get('side','?')} "
            f"IN:{r.get('entry_price','-')} -> OUT:{r.get('exit_price','-')} "
            f"{r.get('reason','?')} PnL%:{pnl_pct}"
        )

    win_rate = (win_cnt / total_cnt * 100.0) if total_cnt > 0 else 0.0

    return total_cnt, pnl_sum, win_rate, "\n".join(detail_lines[:20])

def generate_daily_report():
    rows = _load_trades()
    total_cnt, pnl_sum, win_rate, details = _summarize(rows, hours_back=24)

    msg = (
        "📊 デイリーレポート\n"
        f"集計時刻(JST): {get_jst_now_str()}\n"
        f"取引回数: {total_cnt}\n"
        f"平均損益率合計(ざっくり%合計): {pnl_sum:.2f}%\n"
        f"勝率: {win_rate:.2f}%\n"
        "\n--- 最近のトレード(最大20件) ---\n"
        f"{details}\n"
    )

    return msg
