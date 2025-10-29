# ai_model_trainer.py
# 銘柄ごとの平均リターン・勝率から利確/損切り幅を自動最適化する
import json, os, statistics

LEARN_PATH = "data/learning_log.jsonl"
MODEL_PATH = "data/ai_dynamic_thresholds.json"

def train_dynamic_thresholds():
    if not os.path.exists(LEARN_PATH):
        return {}
    stats = {}
    with open(LEARN_PATH, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            sym = d["symbol"]
            pct = float(d.get("final_pct") or 0)
            if sym not in stats: stats[sym] = []
            stats[sym].append(pct)

    model = {}
    for sym, pcts in stats.items():
        avg = statistics.mean(pcts)
        std = statistics.pstdev(pcts) or 0.1
        model[sym] = {
            "tp": round(avg + std, 2),
            "sl": round(avg - 1.5 * std, 2)
        }
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    with open(MODEL_PATH, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=2)
    return model
