# orchestrator.py
import os
import time
import httpx
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")

# ====== ユーティリティ ======
async def send_discord(msg: str):
    """Discord通知（非同期）"""
    if not DISCORD_WEBHOOK:
        print("[warn] DISCORD_WEBHOOK 未設定")
        return
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(DISCORD_WEBHOOK, json={"content": msg})

def fetch_yahoo(symbol: str, interval="1m", lookback_min=60):
    """YahooFinanceから直近データを取得"""
    try:
        df = yf.download(
            tickers=symbol,
            period=f"{int(lookback_min/60)+1}h",
            interval=interval,
            progress=False,
            prepost=True,
        )
        if df.empty:
            return None
        df = df.tail(lookback_min)
        return df
    except Exception as e:
        print(f"[yahoo] error: {e}")
        return None

# ====== メイン処理 ======
async def handle_tv_signal(symbol: str, direction: str, price: float, ts: int = 0):
    """
    TradingViewからの初動信号を受け、AI後追い監視を実行
    """
    print(f"[AI] 受信: {symbol} {direction} @ {price}")

    # 監視時間・設定
    start_time = time.time()
    monitor_sec = 60 * 15  # 最大15分追跡
    check_interval = 60    # 1分ごとに更新

    # ATR・初期ライン設定
    df = fetch_yahoo(symbol)
    if df is None or len(df) < 5:
        await send_discord(f"⚠️ {symbol} のデータ取得に失敗（YahooFinance）")
        return

    atr = (df["High"] - df["Low"]).rolling(14).mean().iloc[-1]
    tp = price + atr * 1.7 if direction == "BUY" else price - atr * 1.7
    sl = price - atr * 0.9 if direction == "BUY" else price + atr * 0.9

    await send_discord(f"📊 {symbol} 追跡開始\n方向: {direction}\nTP: {tp:.2f} / SL: {sl:.2f}\nATR: {atr:.3f}")

    # ====== 後追い監視ループ ======
    while time.time() - start_time < monitor_sec:
        time.sleep(3)  # 短いスリープで落ち着かせる
        df_new = fetch_yahoo(symbol, lookback_min=3)
        if df_new is None or df_new.empty:
            continue

        latest = df_new.iloc[-1]
        high, low = latest["High"], latest["Low"]
        now_price = latest["Close"]

        # 利確／損切りチェック
        if direction == "BUY":
            if high >= tp:
                await send_discord(f"🎯 利確達成: {symbol} @ {tp:.2f}")
                return
            elif low <= sl:
                await send_discord(f"🛑 損切り発動: {symbol} @ {sl:.2f}")
                return
        else:  # SELL
            if low <= tp:
                await send_discord(f"🎯 利確達成: {symbol} @ {tp:.2f}")
                return
            elif high >= sl:
                await send_discord(f"🛑 損切り発動: {symbol} @ {sl:.2f}")
                return

        # 経過ログ
        if int((time.time() - start_time) // 60) % 3 == 0:
            await send_discord(f"⏱ {symbol} 監視中... 現在価格: {now_price:.2f}")
        time.sleep(check_interval)

    await send_discord(f"⌛ {symbol} 監視終了（15分経過・決済なし）")

# ====== 健康チェック ======
async def healthcheck():
    try:
        return True, "orchestrator OK"
    except Exception as e:
        return False, str(e)
