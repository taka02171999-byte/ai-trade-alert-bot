import os
import csv
from datetime import datetime, timedelta
from utils.discord import send_discord
from utils.time_utils import get_jst_now_str

DATA_PATH = "data/trades.csv"
WEBHOOK = os.getenv("DISCORD_WEBHOOK_REPORT", "")

def read_trades():
    if not os.path.exists(DATA_PATH):
        return []
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)

def generate_daily_report():
    trades = read_trades()
    today = datetime.now().strftime("%Y-%m-%d")

    today_trades = [t for t in trades if t.get("date") == today]
    if not today_trades:
        return f"📊 {today} の取引はありません。"

    total = len(today_trades)
    wins = len([t for t in today_trades if t.get("result") == "WIN"])
    losses = len([t for t in today_trades if t.get("result") == "LOSE"])
    profit_sum = sum(float(t.get("profit", 0)) for t in today_trades)

    win_rate = round(wins / total * 100, 1) if total > 0 else 0

    lines = [
        f"📅 **{today} 日次レポート**",
        f"取引回数: {total}",
        f"勝率: {win_rate}%",
        f"損益合計: {profit_sum:.2f}%",
        "",
        "🟢 勝ちトレード:",
    ]
    for t in today_trades:
        if t.get("result") == "WIN":
            lines.append(f"・{t['symbol']} ({t['side']}) +{t['profit']}%")

    lines.append("")
    lines.append("🔴 負けトレード:")
    for t in today_trades:
        if t.get("result") == "LOSE":
            lines.append(f"・{t['symbol']} ({t['side']}) {t['profit']}%")

    lines.append("")
    lines.append(f"🕒 {get_jst_now_str()}")

    return "\n".join(lines)

if __name__ == "__main__":
    msg = generate_daily_report()
    send_discord(WEBHOOK, msg)
