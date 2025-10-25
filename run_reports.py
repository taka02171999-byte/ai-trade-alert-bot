import sys
import os
from utils.discord import send_discord
from report_daily import generate_daily_report
from report_weekly import generate_weekly_report
from report_monthly import generate_monthly_report

WEBHOOK = os.getenv("DISCORD_WEBHOOK_REPORT", "")

def main():
    if len(sys.argv) < 2:
        mode = "daily"
    else:
        mode = sys.argv[1].lower()

    if mode == "daily":
        msg = generate_daily_report()
    elif mode == "weekly":
        msg = generate_weekly_report()
    elif mode == "monthly":
        msg = generate_monthly_report()
    else:
        msg = f"⚠ 未知のレポート指定: {mode}"

    send_discord(WEBHOOK, msg)

if __name__ == "__main__":
    main()
