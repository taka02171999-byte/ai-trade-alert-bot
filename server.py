# server.py  — AIりんご式 Webhook 受け口 + CSV蓄積 + 健康チェック
# 必要ライブラリ: flask, requests
import os, csv
from pathlib import Path
from datetime import datetime, timezone, timedelta
from flask import Flask, request

# ====== 基本設定 ======
app = Flask(__name__)
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
LOG_DIR  = Path("logs")
CSV_PATH = LOG_DIR / "signals.csv"

# CSVヘッダ（TradingViewから保存したい項目。必要に応じて追加OK）
CSV_HEADERS = [
    "ts_iso", "symbol", "side", "o", "h", "l", "c", "v", "vwap", "atr", "tf"
]

# ====== ユーティリティ ======
def jst_now_iso():
    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=9))
    ).isoformat(timespec="seconds")

def ensure_csv():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not CSV_PATH.exists():
        with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_HEADERS).writeheader()

def notify_discord(text: str):
    if not DISCORD_WEBHOOK:
        print("[warn] DISCORD_WEBHOOK not set; skip notify", flush=True)
        return
    try:
        import requests
        requests.post(DISCORD_WEBHOOK, json={"content": text}, timeout=8)
        print("[info] discord notified", flush=True)
    except Exception as e:
        print(f"[error] discord notify: {e}", flush=True)

def pick(d: dict, k: str, default=""):
    v = d.get(k)
    if v is None: return default
    return str(v)

# ====== ルート（起動確認） ======
@app.route("/", methods=["GET"])
def root():
    return "ok", 200

@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "time": jst_now_iso()}, 200

# ====== TradingView → Webhook 受け口（GET/POST/末尾スラッシュ両対応） ======
@app.route("/webhook",  methods=["GET","POST"])
@app.route("/webhook/", methods=["GET","POST"])
def webhook():
    ct  = request.headers.get("Content-Type", "")
    raw = request.get_data(as_text=True) or ""
    js  = request.get_json(silent=True) or {}
    print(f"[webhook] {request.method} ct={ct} len={len(raw)} body={raw[:500]}", flush=True)

    # 1) テスト: ?ping=1 または {"ping": true} で即200 & Discord通知
    if request.args.get("ping") == "1" or (isinstance(js, dict) and js.get("ping")):
        notify_discord(f"✅ Webhook test OK {jst_now_iso()}")
        return "ok", 200

    # 2) 実弾: 受け取ったJSONをCSVへ追記（可能な項目だけ拾う）
    ensure_csv()
    row = {
        "ts_iso": jst_now_iso(),
        "symbol": pick(js, "symbol"),
        "side":   pick(js, "side"),
        "o":      pick(js, "o"),
        "h":      pick(js, "h"),
        "l":      pick(js, "l"),
        "c":      pick(js, "c"),
        "v":      pick(js, "v"),
        "vwap":   pick(js, "vwap"),
        "atr":    pick(js, "atr"),
        "tf":     pick(js, "tf"),
    }
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=CSV_HEADERS).writerow(row)

    # 3) Discordにも抜粋を通知（長文防止で一部だけ）
    notify_discord(
        f"📩 Signal {row['symbol']} {row['side']} c={row['c']} v={row['v']} tf={row['tf']} @ {row['ts_iso']}"
    )
    return "ok", 200

# ====== 蓄積CSVのダウンロード（最適化ジョブ用） ======
@app.route("/signals", methods=["GET"])
def get_signals():
    ensure_csv()
    try:
        return CSV_PATH.read_text(encoding="utf-8"), 200, {
            "Content-Type": "text/csv; charset=utf-8"
        }
    except Exception as e:
        print(f"[error] read csv: {e}", flush=True)
        return "error", 500

# ====== ローカル実行用（RenderではGunicornが使う） ======
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
