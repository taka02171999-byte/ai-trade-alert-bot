# optimize.py（Cron用：/signalsからCSV取得→簡易最適化→Discord通知）
import os, csv, json, requests, statistics
from datetime import datetime, timezone

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
SIGNALS_URL     = os.getenv("SIGNALS_URL")  # ← Cronの環境変数に設定する
LOCAL_LOG       = os.path.join("logs", "signals.csv")  # フォールバック用
PARAMS_FILE     = "params.json"

def log(msg: str):
    # Render のログに即時出力
    print(msg, flush=True)

def post_discord(title: str, desc: str, color: int = 0x1ABC9C):
    if not DISCORD_WEBHOOK:
        log("no DISCORD_WEBHOOK")
        return
    payload = {
        "embeds": [{
            "title": title,
            "description": desc,
            "color": color,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }]
    }
    try:
        resp = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
        log(f"discord status: {resp.status_code}")
    except Exception as e:
        log(f"discord notify error: {e}")

def fetch_rows():
    """まず SIGNALS_URL から取得。なければローカルCSVを読む。"""
    rows = []
    # 1) HTTP（推奨）
    if SIGNALS_URL:
        try:
            log(f"fetching CSV via HTTP: {SIGNALS_URL}")
            r = requests.get(SIGNALS_URL, timeout=15)
            r.raise_for_status()
            text = r.text.splitlines()
            rows = list(csv.DictReader(text))
            log(f"downloaded rows: {len(rows)}")
            if rows:
                return rows
        except Exception as e:
            log(f"HTTP fetch error: {e}")

    # 2) ローカル（フォールバック）
    if os.path.exists(LOCAL_LOG):
        try:
            log(f"reading local CSV: {LOCAL_LOG}")
            with open(LOCAL_LOG, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            log(f"local rows: {len(rows)}")
        except Exception as e:
            log(f"local CSV read error: {e}")
    else:
        log("no CSV found (neither HTTP nor local)")
    return rows

def simple_optimize(rows):
    """
    超シンプル最適化：
      - 銘柄ごとにATR中央値をとり、SL/TP倍率をざっくり調整。
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
        if m <= 1.0:
            sl_atr, tp_atr = 0.8, 1.5
        elif m <= 2.0:
            sl_atr, tp_atr = 0.9, 1.6
        else:
            sl_atr, tp_atr = 1.0, 1.8
        new_params[sym] = {"sl_atr": sl_atr, "tp_atr": tp_atr}
    return new_params

def main():
    log("=== optimize job started ===")
    rows = fetch_rows()
    if not rows:
        post_discord("🤖 夜間学習レポート", "本日は新規シグナルがありませんでした。")
        log("finished (no rows)")
        return

    # 既存 params 読込
    old = {}
    if os.path.exists(PARAMS_FILE):
        try:
            with open(PARAMS_FILE, encoding="utf-8") as f:
                old = json.load(f)
        except Exception as e:
            log(f"params load error: {e}")

    # 最適化
    new = simple_optimize(rows)
    merged = dict(old)
    merged.update(new)

    # 保存
    try:
        with open(PARAMS_FILE, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        log(f"params saved: {len(new)} symbols updated")
    except Exception as e:
        log(f"params save error: {e}")

    # Discord通知
    if new:
        lines = [f"- {sym}: SL×{v['sl_atr']} / TP×{v['tp_atr']}" for sym, v in new.items()]
        desc = "本日の最適化（ATR係数）\n" + "\n".join(lines)
    else:
        desc = "更新なし（データ不足or同一）"
    post_discord("🤖 夜間学習レポート", desc)
    log("=== optimize job finished ===")

if __name__ == "__main__":
    main()
