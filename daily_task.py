# daily_task.py — 日次バッチを1本に統合
import os
import sys

def run(cmd):
    print(f"▶ {cmd}", flush=True)
    rc = os.system(cmd)
    if rc != 0:
        print(f"❌ FAILED: {cmd} (rc={rc})", flush=True)
        sys.exit(1)

# 実行順序に意味があります：
# 1. 翌日の「選定銘柄」を決める
# 2. 過去実績＋ATRから TP/SL と 時間 を最適化
# 3. 直近成績でエージェント切替
# 4. 実トレード（選定銘柄のみ）の日次レポ
# 5. 週次・月次レポ（同時刻でOK）

run("python select_symbols.py")
run("python optimize_percent.py")
run("python compare_agents.py")
run("python report_operational_aggregate.py")
run("python report_all.py")

print("✅ Daily batch finished.", flush=True)
