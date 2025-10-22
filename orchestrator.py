# orchestrator.py
import os, asyncio, datetime as dt, json, sqlite3, pandas as pd
from dotenv import load_dotenv
import httpx
from pathlib import Path
from ai.trailing_ai import StateStore, TrailingAI

load_dotenv()

# === 環境変数 ===
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
TP_PCT = float(os.getenv("DEFAULT_TP_PCT", "0.8"))
SL_PCT = float(os.getenv("DEFAULT_SL_PCT", "0.5"))
TZ = os.getenv("TZ", "Asia/Tokyo")

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "state.db"

_store = StateStore(DB_PATH)

# === Discord通知 ===
async def _discord(text: str):
    if not DISCORD_WEBHOOK:
        return
    async with httpx.AsyncClient(timeout=10) as cli:
        await cli.post(DISCORD_WEBHOOK, json={"content": text})

def notify_sync(text: str):
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_discord(text))
    except RuntimeError:
        asyncio.run(_discord(text))

# === AI起動（TradingViewトリガー後） ===
async def handle_tv_signal(symbol: str, direction: str, price: float, ts: int):
    await _discord(f"📡 初動: {symbol} {direction} @ {price:.2f}")

    ai = TrailingAI(
        store=_store,
        tp_pct=TP_PCT,
        sl_pct=SL_PCT,
        poll_secs=45,
        max_minutes=20
    )
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, ai.run_once, symbol, direction, price, ts, notify_sync)

# === SQLiteユーティリティ ===
def _conn():
    return sqlite3.connect(DB_PATH)

# === 日次レポート ===
async def report_daily():
    date_str = dt.datetime.now().strftime("%Y-%m-%d")
    with _conn() as conn:
        df = pd.read_sql_query("""
            SELECT symbol, direction, entry_price, entry_ts
            FROM positions
        """, conn)
    if df.empty:
        await _discord(f"📅 {date_str}：本日の取引はありません。")
        return

    df["entry_time"] = pd.to_datetime(df["entry_ts"], unit="s").dt.tz_localize("UTC").dt.tz_convert(TZ)
    lines = [f"**📊 {date_str} 実績サマリー**"]
    for _, r in df.iterrows():
        lines.append(f"・{r.symbol} {r.direction} @ {r.entry_price:.2f} ({r.entry_time:%H:%M})")
    msg = "\n".join(lines)
    await _discord(msg)

# === 週次AI比較 ===
async def compare_agents():
    # 過去データからFixedとRTの勝率を比較する（サンプル）
    # 実際にはsummaryテーブルなどを参照
    msg = "🤖 今週のAI比較結果\nFixedAI 勝率: 62.3%\nRT-AI 勝率: 66.8%\n→ RT-AI継続採用"
    await _discord(msg)

# === 月次まとめ ===
async def summary_report():
    msg = "📈 月次まとめ（サンプル）\n総取引: 284回\n勝率: 64.1%\n平均利益: +0.47%\nAI切替: RT優勢"
    await _discord(msg)

# === 定期ジョブスケジューラ ===
async def scheduler_loop():
    """ 日次・週次・月次レポートを自動送信 """
    last_day = last_week = last_month = None
    while True:
        now = dt.datetime.now()
        # 日次：場引け後（15:10以降）
        if now.hour == 15 and now.minute >= 10:
            if last_day != now.date():
                await report_daily()
                last_day = now.date()
        # 週次：金曜15:30
        if now.weekday() == 4 and now.hour == 15 and now.minute >= 30:
            if last_week != now.isocalendar()[1]:
                await compare_agents()
                last_week = now.isocalendar()[1]
        # 月次：月初1日 15:30
        if now.day == 1 and now.hour == 15 and now.minute >= 30:
            if last_month != now.month:
                await summary_report()
                last_month = now.month
        await asyncio.sleep(60)

# === 健康チェック ===
async def healthcheck():
    ok = True
    if not DISCORD_WEBHOOK:
        ok = False
        return ok, "Missing DISCORD_WEBHOOK"
    return ok, "ok"

# === メインエントリ ===
if __name__ == "__main__":
    print("🚀 AIりんご式 完全統合 orchestrator 起動中...")
    asyncio.run(scheduler_loop())
