# run_daily_training.py
# ===============================
# 日次学習ジョブ用の超シンプルラッパ
# サーバー本体(server.py)には触らないで、
# スケジューラからこっちを起動する運用にしておけば安全。
# ===============================

from ai_model_trainer import run_daily_training

if __name__ == "__main__":
    run_daily_training()
