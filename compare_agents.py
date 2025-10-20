# compare_agents.py — 直近7日/30日の fixed vs rt をDiscordに比較レポート（JST基準）
import os, csv
from pathlib import Path
from datetime import datetime, timezone, timedelta
import requests

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
CSV_TRADES = Path("logs") / "trades.csv"

# ===== すべて日本時間（JST, UTC+9）で集計・表示 =====
JST = timezone(timedelta(hours=9))

def jst_now():
    return datetime.now(timezone.utc).astimezone(JST)

def parse_iso_jst(s: str):
    """ISO8601 を JST の aware datetime へ。TZ なしは JST とみなす。epoch秒も可。"""
    if not s:
        return None
    # epoch seconds
    try:
        if s.strip().isdigit() or (s.strip().replace('.','',1).isdigit() and s.count('.')<=1):
            return datetime.fromtimestamp(float(s), tz=JST)
    except Exception:
        pass
    # ISO8601
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None

def load_trades():
    rows = []
    if not CSV_TRADES.exists():
        return rows
    with CSV_TRADES.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            close_ts = parse_iso_jst(row.get("close_ts") or "")
            if not close_ts:
                continue
            agent = (row.get("agent") or "fixed").lower()
            symbol = (row.get("symbol") or "-").upper()
            try:
                pnl_pct = float(row.get("pnl_pct") or 0.0)
            except Exception:
                pnl_pct = 0.0
            rows.append({"close_ts": close_ts, "agent": agent, "symbol": symbol, "pnl_pct": pnl_pct})
    return rows

def slice_since(days, rows):
    since = jst_now() - timedelta(days=days)
    return [r for r in rows if r["close_ts"] >= since]

def summarize(rows):
    by_agent = {}
    for r in rows:
        a = r["agent"]
        s = by_agent.setdefault(a, {"sum":0.0, "wins":0, "losses":0, "cnt":0, "profit_sum":0.0, "loss_sum":0.0})
        pnl = r["pnl_pct"]
        s["sum"] += pnl
        s["cnt"] += 1
        if pnl >= 0:
            s["wins"] += 1
            s["profit_sum"] += pnl
        else:
            s["losses"] += 1
            s["loss_sum"] += abs(pnl)
    lines = []
    for a in sorted(by_agent.keys()):
        s = by_agent[a]
        wr = (s["wins"]/s["cnt"]*100.0) if s["cnt"]>0 else 0.0
        pf = (s["profit_sum"]/s["loss_sum"]) if s["loss_sum"]>0 else (s["profit_sum"] if s["profit_sum"]>0 else 0.0)
        lines.append((a, s["sum"], wr, pf, s["cnt"]))
    return lines

def pick_winner(lines_7d, lines_30d):
    def best(lines):
        if not lines: return None
        lines_sorted = sorted(lines, key=lambda x: (x[1], x[2], x[3]))
        return lines_sorted[-1][0]
    b7 = best(lines_7d)
    b30 = best(lines_30d)
    if "rt" in (b7, b30):
        return "rt"
    if "fixed" in (b7, b30):
        return "fixed"
    return b7 or b30 or "-"

def fmt_lines(label, lines):
    order = {"fixed":0, "rt":1}
    lines = sorted(lines, key=lambda x: order.get(x[0], 9))
    if not lines:
        return f"・{label}\n  データなし"
    out = [f"・{label}"]
    for a, ssum, wr, pf, cnt in lines:
        tag = "fixed" if a=="fixed" else "rt"
        out.append(f"  {tag:5}: {ssum:+.2f}% / 勝率 {wr:.1f}% / PF {pf:.2f} / 件数 {cnt}")
    return "\n".join(out)

def post_embed(title, desc, color=0x1abc9c):
    if not DISCORD_WEBHOOK:
        print("[warn] DISCORD_WEBHOOK not set"); return
    # Discordの timestamp はUTCになるため、本文にJST時刻を明記
    now_jst_str = jst_now().strftime("%Y-%m-%d %H:%M JST")
    payload = {"embeds":[{
        "title": f"{title}  ({now_jst_str})",
        "description": desc,
        "color": color,
        "footer":{"text":"AIりんご式 比較レポート（JST）"}
    }]}
    r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
    print("discord:", r.status_code)

def main():
    rows = load_trades()
    rows7 = slice_since(7, rows)
    rows30 = slice_since(30, rows)

    sum7 = summarize(rows7)
    sum30 = summarize(rows30)
    winner = pick_winner(sum7, sum30)

    desc = []
    desc.append(fmt_lines("直近7日", sum7))
    desc.append("")
    desc.append(fmt_lines("直近30日", sum30))
    desc.append("")
    if winner in ("fixed","rt"):
        wn = "固定" if winner=="fixed" else "RT"
        desc.append(f"🏁 総合判定：**{wn}優勢**（自動切替候補）")
    else:
        desc.append("🏁 総合判定：—")

    post_embed("📆 パフォーマンス比較（fixed vs rt）", "\n".join(desc), 0x7289da)

if __name__ == "__main__":
    main()
