import os
from utils.discord import send_discord
from utils.time_utils import jst_now, is_business_day
from report_daily import generate_daily_report

def main():
    hook = os.getenv("DISCORD_WEBHOOK_REPORT", "")
    now = jst_now()

    # 土日祝は送らない
    if not is_business_day(now):
        print("[daily] skip (weekend/holiday):", now)
        return

    msg = generate_daily_report()
    send_discord(hook, "AIりんご式 レポート", msg, 0x00CCFF)

if __name__ == "__main__":
    main()
