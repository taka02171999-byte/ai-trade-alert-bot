# run_reports_weekly.py
# ==========================================
# 週次レポ専用（例: 土曜の朝だけCronで叩く）
#
# やること:
#   1. 週次レポをDiscordへ送信
#
# 環境変数:
#   DISCORD_WEBHOOK_REPORT
# ==========================================

import os
from datetime import datetime
import pytz

from report_weekly import generate_weekly_report
from utils.discord import send_discord


def main():
    JST = pytz.timezone("Asia/Tokyo")
    now_jst = datetime.now(JST)

    hook = os.getenv("DISCORD_WEBHOOK_REPORT", "")

    # 週次レポ
    try:
        weekly_msg = generate_weekly_report()
    except Exception as e:
        weekly_msg = f"⚠ 週次レポ生成中にエラー: {e}"
        print("[run_reports_weekly] 週次レポエラー:", e)

    if hook:
        send_discord(hook, weekly_msg)
    else:
        print("[run_reports_weekly] ⚠ DISCORD_WEBHOOK_REPORT 未設定(weekly)")
        print(weekly_msg)


if __name__ == "__main__":
    main()
