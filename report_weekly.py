import os
import csv
from datetime import datetime, timedelta
from utils.discord import send_discord

DATA_PATH = "data/trades.csv"
WEBHOOK = os.getenv("DISCORD_WEBHOOK_REPORT", "")

def read_trades():
    if not os.path.exists(DATA_PATH):
        return []
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)

def generate_weekly_report():
    trades = read_trades()
    today = datetime.now()
    week_ago = today - timedelta(days=7)
    filtered = [
        t for t in trades
        if week_ago.strftime("%Y-%m-%d") <= t.get("date", "") <= today.strftime("%Y-%m-%d")
    ]

    if not filtered:
        return "📊 今週の取引データがありません。"

    total = len(filtered)
    wins = len([t for t in filtered if t.get("result") == "WIN"])
    losses = len([t for t in filtered if t.get("result") == "LOSE"])
    profit_sum = sum(float(t.get("profit", 0)) for t in filtered)
    win_rate = round(wins / total * 100, 1) if total > 0 else 0

    lines = [
        f"📅 **週次レポート（{week_ago.strftime('%m/%d')}〜{today.strftime('%m/%d')}）**",
        f"取引数: {total}",
        f"勝率: {win_rate}%",
        f"合計損益: {profit_sum:.2f}%",
    ]
    return "\n".join(lines)

if __name__ == "__main__":
    msg = generate_weekly_report()
    send_discord(WEBHOOK, msg)
