# ai_model_trainer.py
# ===============================
# 学習ログ(data/learning_log.jsonl)を読んで
# 銘柄ごとに「このくらいで利確できてた」「これ以上持つと危ない」という
# tp/slの目安を data/ai_dynamic_thresholds.json に書き出す。
# （日次/週次バッチで実行する想定）
# ===============================

import json, os, statistics

LEARN_PATH = "data/learning_log.jsonl"
MODEL_PATH = "data/ai_dynamic_thresholds.json"

def train_dynamic_thresholds():
    if not os.path.exists(LEARN_PATH):
        return {}

    stats = {}
    with open(LEARN_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except:
                continue
            sym = row.get("symbol")
            final_pct = row.get("final_pct")
            if sym is None or final_pct is None:
                continue
            try:
                fp = float(final_pct)
            except:
                continue
            stats.setdefault(sym, []).append(fp)

    model = {}
    for sym, plist in stats.items():
        avg = statistics.mean(plist)
        std = statistics.pstdev(plist) if len(plist) > 1 else 0.3
        # 「だいたいこのくらいまで伸びる」がtp、
        # 「これ超えて負けてることが多い」がslみたいなイメージ
        model[sym] = {
            "tp": round(avg + std * 1.2, 2),
            "sl": round(avg - std * 1.5, 2)
        }

    os.makedirs("data", exist_ok=True)
    with open(MODEL_PATH, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=2)

    return model
