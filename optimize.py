# optimize.py（雛形：ATR係数の簡易最適化＋Discordレポート）
import json, os, csv, statistics, requests
from pathlib import Path
from datetime import datetime, timezone

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
LOG_FILE = Path("logs") / "signals.csv"
PARAMS_FILE = Path("params.json")

def notify(title, desc):
    if not DISCORD_WEBHOOK:
        print("no DISCORD_WEBHOOK")
        return
    payload = {"embeds": [{
        "title": title,
        "description": desc,
        "color": 0x1ABC9C,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }]}
    try:
        requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
    except Exception as e:
        print("discord notify error:", e)

def load_signals():
    if not LOG_FILE.exists():
        return []
    with open(LOG_FILE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def simple_optimize(rows):
    """
    超シンプル最適化：
    - 銘柄ごとのATRの中央値を見て、SL/TP倍率を微調整。
    - まずは“自動で調整される”ことが大事。あとで本格バックテストに差し替え可能。
    """
    by_sym = {}
    for r in rows:
        sym = r.get("symbol") or "UNKNOWN"
        try:
            atr = float(r.get("atr") or 0)
        except:
            atr = 0.0
        if atr > 0:
            by_sym.setdefault(sym, []).append(atr)

    new_params = {}
    for sym, atrs in by_sym.items():
        if not atrs:
            continue
        m = statistics.median(atrs)
        # ATRが大きい銘柄ほど余裕、ATRが小さい銘柄はタイトめ（例）
        if m <= 1.0:
            sl_atr, tp_atr = 0.8, 1.5
        elif m <= 2.0:
            sl_atr, tp_atr = 0.9, 1.6
        else:
            sl_atr, tp_atr = 1.0, 1.8
        new_params[sym] = {"sl_atr": sl_atr, "tp_atr": tp_atr}
    return new_params

def main():
    rows = load_signals()
    if not rows:
        notify("🤖 夜間学習レポート", "本日は新規シグナルがありませんでした。")
        return

    # 既存 params を読み込み
    old = {}
    if PARAMS_FILE.exists():
        try:
            old = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
        except:
            old = {}

    # 最適化
    new = simple_optimize(rows)
    merged = dict(old)
    merged.update(new)

    # 保存
    PARAMS_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    # Discordに結果通知
    if new:
        lines = [f"- {sym}: SL×{v['sl_atr']} / TP×{v['tp_atr']}" for sym, v in new.items()]
        desc = "本日の最適化（ATR係数）\n" + "\n".join(lines)
    else:
        desc = "更新なし（データ不足or同一）"
    notify("🤖 夜間学習レポート", desc)

if __name__ == "__main__":
    main()
