import os
from utils.discord import send_discord
from utils.time_utils import jst_now, is_business_day
from report_weekly import generate_weekly_report

def main():
    hook = os.getenv("DISCORD_WEBHOOK_REPORT", "")
    now = jst_now()

    # 土日祝は送らない
    if not is_business_day(now):
        print("[weekly] skip (weekend/holiday):", now)
        return

    # 金曜のみ送る（Render側スケジュールが金曜でも、二重保険で入れる）
    if now.weekday() != 4:
        print("[weekly] skip (not Friday):", now)
        return

    msg = generate_weekly_report()
    send_discord(hook, "AIりんご式 週次レポ", msg, 0x00CCFF)

if __name__ == "__main__":
    main()
