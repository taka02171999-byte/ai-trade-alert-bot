# run_reports_daily.py
# ==========================================
# 毎営業日用（日次レポ＋モデル学習更新）
#
# やること:
#   1. 学習モデル更新（エントリー側＋利確/損切り側）
#   2. デイリーレポをDiscordへ送信
#
# 環境変数:
#   DISCORD_WEBHOOK_REPORT
# ==========================================

import os
from datetime import datetime
import pytz

from ai_model_trainer import train_dynamic_thresholds, train_entry_thresholds
from report_daily import generate_daily_report
from utils.discord import send_discord


def main():
    JST = pytz.timezone("Asia/Tokyo")
    now_jst = datetime.now(JST)

    hook = os.getenv("DISCORD_WEBHOOK_REPORT", "")

    #
    # 1) 学習モデル更新（EXIT側とENTRY側の両方）
    #
    try:
        exit_model = train_dynamic_thresholds()
        entry_model = train_entry_thresholds()

        print("[run_reports_daily] 学習モデル更新OK")
        print("  EXITモデル銘柄:", list(exit_model.keys()))
        print("  ENTRYモデル銘柄:", list(entry_model.keys()))

        if hook:
            send_discord(
                hook,
                "🤖 学習モデル更新完了(日次)\n"
                f"EXIT側更新銘柄: {list(exit_model.keys())}\n"
                f"ENTRY側更新銘柄: {list(entry_model.keys())}\n"
                f"{now_jst.isoformat(timespec='seconds')}"
            )
    except Exception as e:
        print("[run_reports_daily] 学習モデル更新エラー:", e)
        if hook:
            send_discord(
                hook,
                f"⚠ 学習モデル更新エラー(日次): {e}\n{now_jst.isoformat(timespec='seconds')}"
            )

    #
    # 2) デイリーレポ
    #
    try:
        daily_msg = generate_daily_report()
    except Exception as e:
        daily_msg = f"⚠ 日次レポ生成中にエラー: {e}"
        print("[run_reports_daily] 日次レポエラー:", e)

    if hook:
        send_discord(hook, daily_msg)
    else:
        print("[run_reports_daily] ⚠ DISCORD_WEBHOOK_REPORT 未設定")
        print(daily_msg)


if __name__ == "__main__":
    main()
