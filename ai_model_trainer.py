# ai_model_trainer.py
# ===============================
# data/learning_log.jsonl を読んで、
# 銘柄ごとの「このくらいの利幅でやめるのが現実的」「ここまで行ったらヤバい」を
# data/ai_dynamic_thresholds.json に保存する。
# ===============================

import json, os, statistics

LEARN_PATH = "data/learning_log.jsonl"
MODEL_PATH = "data/ai_dynamic_thresholds.json"

def train_dynamic_thresholds():
    if not os.path.exists(LEARN_PATH):
        return {}

    per_symbol = {}
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
                p = float(final_pct)
            except:
                continue

            per_symbol.setdefault(sym, []).append(p)

    model = {}
    for sym, arr in per_symbol.items():
        avg = statistics.mean(arr)
        std = statistics.pstdev(arr) if len(arr) > 1 else 0.3

        # tp = 平均 + ちょい上振れ
        # sl = 平均 - もっと下振れ
        model[sym] = {
            "tp": round(avg + std * 1.2, 2),
            "sl": round(avg - std * 1.5, 2),
        }

    os.makedirs("data", exist_ok=True)
    with open(MODEL_PATH, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=2)

    return model
