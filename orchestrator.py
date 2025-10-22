# orchestrator.py
import os, asyncio, sqlite3
from pathlib import Path
from dotenv import load_dotenv
import httpx
import yfinance as yf

from ai.trailing_ai import StateStore, TrailingAI

load_dotenv()
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
TP_PCT = float(os.getenv("DEFAULT_TP_PCT", "0.8"))
SL_PCT = float(os.getenv("DEFAULT_SL_PCT", "0.5"))
NAMES_OVERRIDES = os.getenv("NAMES_OVERRIDES", "")  # 例: "7203.T=トヨタ自動車;6758.T=ソニーG"

# 同じstate.dbを使う（存在しなければ自動作成）
DATA_DIR = Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "state.db"

_store = StateStore()

# ---- 日本語銘柄名リゾルバ（自動取得＋キャッシュ） ---------------------------
class NameResolver:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ticker_names(
                    symbol TEXT PRIMARY KEY,
                    name   TEXT NOT NULL
                )
            """)
        # 環境変数の上書き設定（優先）
        self.overrides = {}
        if NAMES_OVERRIDES.strip():
            for pair in NAMES_OVERRIDES.split(";"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    self.overrides[k.strip()] = v.strip()

    def get(self, symbol: str) -> str:
        # 1) 手動上書きがあればそれを返す
        if symbol in self.overrides:
            return self.overrides[symbol]

        # 2) DBキャッシュ
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("SELECT name FROM ticker_names WHERE symbol=?", (symbol,))
            row = cur.fetchone()
            if row:
                return row[0]

        # 3) yfinance から取得（shortName/longName）
        name = symbol
        try:
            info = yf.Ticker(symbol).info or {}
            name = info.get("shortName") or info.get("longName") or symbol
            # ちょい整形：よくあるカッコ表記の簡易クリーニング
            for s, r in [("(株)", ""), ("株式会社", ""), (" Co., Ltd.", ""), (" Holdings", ""), (" Group", "")]:
                name = name.replace(s, r).strip()
        except Exception:
            name = symbol  # 失敗時はシンボル

        # 4) DBに保存
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("REPLACE INTO ticker_names(symbol,name) VALUES(?,?)", (symbol, name))
        except Exception:
            pass
        return name

_name_resolver = NameResolver(DB_PATH)
# -----------------------------------------------------------------------

async def _discord(text: str):
    if not DISCORD_WEBHOOK: return
    async with httpx.AsyncClient(timeout=10) as cli:
        await cli.post(DISCORD_WEBHOOK, json={"content": text})

def _notify_sync(text: str):
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_discord(text))
    except RuntimeError:
        asyncio.run(_discord(text))

async def handle_tv_signal(symbol: str, direction: str, price: float, ts: int):
    # 日本語（または取得できた名称）を自動解決
    jp_name = _name_resolver.get(symbol)
    await _discord(f"📡 初動: {jp_name} ({symbol}) {direction} @ {price:.2f}")

    ai = TrailingAI(
        store=_store,
        tp_pct=TP_PCT,
        sl_pct=SL_PCT,
        poll_secs=45,
        max_minutes=20
    )
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, ai.run_once, symbol, direction, price, ts, _notify_sync)

async def healthcheck():
    ok = True
    if not DISCORD_WEBHOOK:
        ok = False
        return ok, "Missing DISCORD_WEBHOOK"
    return ok, "ok"
