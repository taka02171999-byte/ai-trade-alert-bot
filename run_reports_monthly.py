# run_reports_monthly.py
# ==========================================
# 月次レポ専用（毎月1日の朝だけCron）
#
# やること:
#   1. マンスリーレポをDiscordへ送信
#
# 環境変数:
#   DISCORD_WEBHOOK_REPORT
# ==========================================

import os
from datetime import datetime
import pytz

from report_monthly import generate_monthly_report
from utils.discord import send_discord


def main():
    JST = pytz.timezone("Asia/Tokyo")
    now_jst = datetime.now(JST)

    hook = os.getenv("DISCORD_WEBHOOK_REPORT", "")

    # 月次レポ
    try:
        monthly_msg = generate_monthly_report()
    except Exception as e:
        monthly_msg = f"⚠ 月次レポ生成中にエラー: {e}"
        print("[run_reports_monthly] 月次レポエラー:", e)

    if hook:
        send_discord(hook, monthly_msg)
    else:
        print("[run_reports_monthly] ⚠ DISCORD_WEBHOOK_REPORT 未設定(monthly)")
        print(monthly_msg)


if __name__ == "__main__":
    main()
