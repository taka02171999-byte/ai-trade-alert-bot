import os
from datetime import timedelta
from utils.discord import send_discord
from utils.time_utils import jst_now, is_business_day
from report_monthly import generate_monthly_report

def is_first_business_day_of_month(now):
    if not is_business_day(now):
        return False

    # 月初から今日まで遡って、最初の営業日が今日か？
    d = now.replace(day=1)
    while True:
        if is_business_day(d):
            return d.date() == now.date()
        d = d + timedelta(days=1)

def main():
    hook = os.getenv("DISCORD_WEBHOOK_REPORT", "")
    now = jst_now()

    # 土日祝は送らない
    if not is_business_day(now):
        print("[monthly] skip (weekend/holiday):", now)
        return

    # 月初の営業日のみ送る
    if not is_first_business_day_of_month(now):
        print("[monthly] skip (not first business day):", now)
        return

    msg = generate_monthly_report()
    send_discord(hook, "AIりんご式 月次レポ", msg, 0x00CCFF)

if __name__ == "__main__":
    main()
