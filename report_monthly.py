import os
import csv
from datetime import datetime
from utils.discord import send_discord

DATA_PATH = "data/trades.csv"
WEBHOOK = os.getenv("DISCORD_WEBHOOK_REPORT", "")

def read_trades():
    if not os.path.exists(DATA_PATH):
        return []
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)

def generate_monthly_report():
    trades = read_trades()
    this_month = datetime.now().strftime("%Y-%m")
    month_trades = [t for t in trades if t.get("date", "").startswith(this_month)]

    if not month_trades:
        return f"📊 {this_month} の取引データなし。"

    total = len(month_trades)
    wins = len([t for t in month_trades if t.get("result") == "WIN"])
    losses = len([t for t in month_trades if t.get("result") == "LOSE"])
    profit_sum = sum(float(t.get("profit", 0)) for t in month_trades)
    win_rate = round(wins / total * 100, 1) if total > 0 else 0

    lines = [
        f"📅 **{this_month} 月次レポート**",
        f"取引数: {total}",
        f"勝率: {win_rate}%",
        f"合計損益: {profit_sum:.2f}%",
    ]
    return "\n".join(lines)

if __name__ == "__main__":
    msg = generate_monthly_report()
    send_discord(WEBHOOK, msg)
