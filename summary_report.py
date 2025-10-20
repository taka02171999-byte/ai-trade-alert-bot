# summary_report.py — 全期間まとめ＆月次ハイライトをDiscordへ
import os, csv
from pathlib import Path
from datetime import datetime, timezone, timedelta
import requests
from collections import defaultdict

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
CSV_TRADES = Path("logs") / "trades.csv"
JST = timezone(timedelta(hours=9))

def parse_iso(s):
    try:
        return datetime.fromisoformat(s)
    except:
        return None

def load_trades():
    if not CSV_TRADES.exists(): return []
    rows = []
    with CSV_TRADES.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            t = parse_iso(row.get("close_ts") or "")
            if not t: continue
            rows.append({
                "close_ts": t.astimezone(JST),
                "agent": (row.get("agent") or "fixed").lower(),
                "symbol": (row.get("symbol") or "-").upper(),
                "pnl_pct": float(row.get("pnl_pct") or 0.0),
            })
    return rows

def agg(rows):
    total=0.0; wins=0; losses=0; cnt=0
    by_sym=defaultdict(float)
    for r in rows:
        p=r["pnl_pct"]; total+=p; cnt+=1
        if p>=0: wins+=1
        else: losses+=1
        by_sym[r["symbol"]]+=p
    winrate = (wins/cnt*100.0) if cnt>0 else 0.0
    profit_sum = sum(max(0.0, r["pnl_pct"]) for r in rows)
    loss_sum = sum(max(0.0, -r["pnl_pct"]) for r in rows)
    pf = (profit_sum/loss_sum) if loss_sum>0 else (profit_sum if profit_sum>0 else 0.0)
    top = sorted(by_sym.items(), key=lambda x:-x[1])[:3]
    return total, winrate, pf, cnt, top

def monthly_highlight(rows):
    by_month=defaultdict(float)
    for r in rows:
        key = r["close_ts"].strftime("%Y-%m")
        by_month[key]+=r["pnl_pct"]
    return sorted(by_month.items(), key=lambda x:-x[1])[:2]

def post_embed(title, desc, color=0x1abc9c):
    if not DISCORD_WEBHOOK:
        print("[warn] DISCORD_WEBHOOK not set"); return
    payload = {"embeds":[{
        "title": title, "description": desc, "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer":{"text":"AIりんご式 サマリーレポート"}
    }]}
    r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
    print("discord:", r.status_code)

def main():
    rows = load_trades()
    if not rows:
        post_embed("📦 サマリー", "取引履歴がまだありません。", 0x95a5a6); return

    first = min(r["close_ts"] for r in rows).strftime("%Y-%m-%d")
    last  = max(r["close_ts"] for r in rows).strftime("%Y-%m-%d")

    total, winrate, pf, cnt, top = agg(rows)
    tops = monthly_highlight(rows)

    lines = []
    lines.append(f"期間: **{first} ～ {last}** (JST)")
    lines.append(f"総損益: **{total:+.2f}%** / 勝率 **{winrate:.1f}%** / PF **{pf:.2f}** / 件数 **{cnt}**")
    if top:
        lines.append("上位銘柄:")
        for s,v in top:
            lines.append(f"・{s}: {v:+.2f}%")
    if tops:
        lines.append("月次ハイライト:")
        for m,v in tops:
            lines.append(f"・{m}: {v:+.2f}%")

    post_embed("📚 全期間まとめレポート", "\n".join(lines), 0x00b894)

if __name__ == "__main__":
    main()
