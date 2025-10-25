import os
import csv
from datetime import datetime, timedelta
from utils.discord import send_discord
from utils.time_utils import get_jst_now_str

TRADE_LOG = "data/trade_log.csv"
REPORT_HOOK = os.getenv("DISCORD_WEBHOOK_REPORT", "")

def load_trades():
    rows = []
    if not os.path.exists(TRADE_LOG):
        return rows
    with open(TRADE_LOG, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows

def parse_iso(ts):
    # trade_log.csv に入ったtimestampを雑にパース
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return datetime.utcnow()

def summarize_trades(rows, since_dt):
    picked = []
    for r in rows:
        t = parse_iso(r["timestamp"])
        if t >= since_dt:
            picked.append(r)

    total_cnt = len(picked)
    total_pnl = 0.0
    win_cnt = 0

    for r in picked:
        pnl_val = float(r.get("pnl", 0.0))
        total_pnl += pnl_val
        if pnl_val > 0:
            win_cnt += 1

    win_rate = (win_cnt / total_cnt * 100.0) if total_cnt > 0 else 0.0

    # 直近20件だけ詳細
    detail_lines = []
    for r in picked[:20]:
        detail_lines.append(
            f"{r['timestamp']} {r['symbol']} {r['side']} "
            f"IN:{r['entry_price']} -> OUT:{r.get('exit_price','-')} "
            f"{r.get('reason','?')} pnl:{r.get('pnl','0')}"
        )

    return total_cnt, total_pnl, win_rate, "\n".join(detail_lines)

def build_report(title, hours_back):
    rows = load_trades()
    since_dt = datetime.utcnow() - timedelta(hours=hours_back)
    total_cnt, total_pnl, win_rate, details = summarize_trades(rows, since_dt)

    msg = (
        f"📊 {title}\n"
        f"集計時刻(JST): {get_jst_now_str()}\n"
        f"取引回数: {total_cnt}\n"
        f"合計損益(円ベース想定): {total_pnl:.2f}\n"
        f"勝率: {win_rate:.2f}%\n"
        f"\n--- 最近のトレード ---\n"
        f"{details}\n"
    )
    return msg

# このファイルを直接cronから叩かない前提。
# 実際の呼び出しは run_reports.py にまとめてある。
