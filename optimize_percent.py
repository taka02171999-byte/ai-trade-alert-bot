# optimize_percent.py  (TradingView専用・numpy不要)
import os, csv, json, statistics, requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
SIGNALS_URL     = os.getenv("SIGNALS_URL", "")  # 例: https://<your-app>.onrender.com/signals
PARAMS_FILE     = Path("params.json")

def jst_now():
    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=9))
    ).strftime("%Y-%m-%d %H:%M:%S")

def log(msg): print(msg, flush=True)

def notify(title, desc):
    if not DISCORD_WEBHOOK:
        log("[warn] DISCORD_WEBHOOK not set; skip notify")
        return
    payload = {
        "embeds": [{
            "title": title,
            "description": desc,
            "color": 0x1ABC9C,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "AIりんご式 | " + jst_now()}
        }]
    }
    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        log(f"discord status: {r.status_code}")
    except Exception as e:
        log(f"[error] discord: {e}")

def fetch_signals_csv():
    rows = []
    local_path = Path("logs/signals.csv")
    got_remote = False
    if SIGNALS_URL:
        try:
            log(f"fetching CSV via HTTP: {SIGNALS_URL}")
            r = requests.get(SIGNALS_URL, timeout=20)
            if r.status_code == 200 and r.text.strip():
                reader = csv.DictReader(r.text.splitlines())
                rows = list(reader)
                got_remote = True
        except Exception as e:
            log(f"[error] http fetch: {e}")
    if not got_remote and local_path.exists():
        try:
            log(f"reading local CSV: {local_path}")
            with local_path.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except Exception as e:
            log(f"[error] local read: {e}")
    log(f"downloaded rows: {len(rows)}")
    return rows

def to_float(x, default=0.0):
    try: return float(x)
    except: return default

def simple_optimize(rows):
    by_sym = {}
    for r in rows:
        sym = r.get("symbol") or "UNKNOWN"
        c   = to_float(r.get("c"))
        atr = to_float(r.get("atr"))
        if c > 0 and atr > 0:
            by_sym.setdefault(sym, []).append(atr / c)  # ATR/Close = vol%
    new_params = {}
    for sym, vols in by_sym.items():
        if not vols: continue
        v_med = statistics.median(vols)  # 例: 0.006 → 0.6%
        if   v_med <= 0.004: sl_atr, tp_atr = 0.7, 1.2   # 低ボラ
        elif v_med <= 0.008: sl_atr, tp_atr = 0.8, 1.4
        elif v_med <= 0.015: sl_atr, tp_atr = 0.9, 1.6
        else:                sl_atr, tp_atr = 1.1, 2.0   # 高ボラ
        new_params[sym] = {"sl_atr": round(sl_atr,2), "tp_atr": round(tp_atr,2)}
    return new_params

def main():
    log("=== optimize job started ===")
    rows = fetch_signals_csv()
    old = {}
    if PARAMS_FILE.exists():
        try: old = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
        except: old = {}
    new = simple_optimize(rows)
    merged = dict(old); merged.update(new)
    PARAMS_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    if new:
        lines = [f"・{s}: SL×{v['sl_atr']} / TP×{v['tp_atr']}" for s, v in new.items()]
        desc = "本日の最適化（ATR係数, TradingViewベース）\n" + "\n".join(lines)
    else:
        desc = "更新なし（データ不足or同一）"
    notify("🤖 夜間学習レポート", desc)
    log("=== optimize job finished ===")

if __name__ == "__main__":
    main()
