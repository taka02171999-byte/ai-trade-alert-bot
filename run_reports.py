# run_reports.py
# ==========================================
# Renderのcronから毎日1回 "python run_reports.py" で実行される。
#
# やること:
#   1. 学習モデル更新 (train_dynamic_thresholds)
#      -> data/ai_dynamic_thresholds.json を銘柄別に更新
#
#   2. デイリーレポをDiscordへ送る
#
#   3. 土曜ならウィークリーレポも送る
#
#   4. 毎月1日ならマンスリーレポも送る
#
# 環境変数:
#   DISCORD_WEBHOOK_REPORT
# ==========================================

import os
from datetime import datetime
import pytz

from ai_model_trainer import train_dynamic_thresholds
from report_daily import generate_daily_report
from report_weekly import generate_weekly_report
from report_monthly import generate_monthly_report
from utils.discord import send_discord

def _is_weekly_send_day(now_jst: datetime) -> bool:
    # 土曜の朝に週次も送る
    # weekday(): 月=0 ... 日=6 なので土曜=5
    return now_jst.weekday() == 5

def _is_monthly_send_day(now_jst: datetime) -> bool:
    # 毎月1日の朝に月次も送る
    return now_jst.day == 1

def main():
    JST = pytz.timezone("Asia/Tokyo")
    now_jst = datetime.now(JST)

    hook = os.getenv("DISCORD_WEBHOOK_REPORT", "")

    # 1) 学習モデル更新
    try:
        model = train_dynamic_thresholds()
        print("[run_reports] 学習モデル更新OK:", model)
        if hook:
            send_discord(
                hook,
                "🤖 学習モデル更新完了\n"
                f"対象銘柄: {list(model.keys())}\n"
                f"{now_jst.isoformat(timespec='seconds')}"
            )
    except Exception as e:
        print("[run_reports] 学習モデル更新エラー:", e)
        if hook:
            send_discord(
                hook,
                f"⚠ 学習モデル更新エラー: {e}\n{now_jst.isoformat(timespec='seconds')}"
            )

    # 2) デイリーレポ
    try:
        daily_msg = generate_daily_report()
    except Exception as e:
        daily_msg = f"⚠ 日次レポ生成中にエラー: {e}"
        print("[run_reports] 日次レポエラー:", e)

    if hook:
        send_discord(hook, daily_msg)
    else:
        print("[run_reports] ⚠ DISCORD_WEBHOOK_REPORT 未設定")
        print(daily_msg)

    # 3) 週次（毎週土曜だけ）
    if _is_weekly_send_day(now_jst):
        try:
            weekly_msg = generate_weekly_report()
        except Exception as e:
            weekly_msg = f"⚠ 週次レポ生成中にエラー: {e}"
            print("[run_reports] 週次レポエラー:", e)

        if hook:
            send_discord(hook, weekly_msg)
        else:
            print("[run_reports] ⚠ DISCORD_WEBHOOK_REPORT 未設定(weekly)")
            print(weekly_msg)

    # 4) 月次（毎月1日だけ）
    if _is_monthly_send_day(now_jst):
        try:
            monthly_msg = generate_monthly_report()
        except Exception as e:
            monthly_msg = f"⚠ 月次レポ生成中にエラー: {e}"
            print("[run_reports] 月次レポエラー:", e)

        if hook:
            send_discord(hook, monthly_msg)
        else:
            print("[run_reports] ⚠ DISCORD_WEBHOOK_REPORT 未設定(monthly)")
            print(monthly_msg)

if __name__ == "__main__":
    main()
