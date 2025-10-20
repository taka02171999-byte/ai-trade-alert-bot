# report_all.py — 1通で全部レポート（JST）+ 勝者自動切替（active_agent.txt 更新）
# - 昨日のデイリー
# - 直近7日/30日の fixed vs rt 比較（勝者を active_agent.txt に書き込み）
# - 全期間まとめ + 月次ハイライト
#
# 前提: logs/trades.csv（列例: close_ts, agent, symbol, pnl_pct）
# 環境変数: DISCORD_WEBHOOK (Discord Webhook URL)
import os, csv
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import requests

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
CSV_TRADES = Path("logs") / "trades.csv"
ACTIVE_AGENT_FILE = Path("active_agent.txt")  # server.py が参照して通知対象を切替

JST = timezone(timedelta(hours=9))

def jst_now():
    return datetime.now(timezone.utc).astimezone(JST)

def parse_ts_jst(s: str):
    """ISO8601→aware(JST)。TZなしはJST扱い。epoch秒にも対応。"""
    if not s:
        return None
    # epoch
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

def load_rows():
    if not CSV_TRADES.exists():
        return []
    rows = []
    with CSV_TRADES.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            t = parse_ts_jst(row.get("close_ts") or "")
            if not t:
                continue
            try:
                pnl = float(row.get("pnl_pct") or 0.0)
            except Exception:
                pnl = 0.0
            rows.append({
                "close_ts": t,
                "date": t.date(),
                "agent": (row.get("agent") or "fixed").lower(),
                "symbol": (row.get("symbol") or "-").upper(),
                "pnl_pct": pnl,
            })
    return rows

# ---- (1) 昨日のデイリー ----
def daily_summary(rows):
    # 実行時点の前日(JST)を集計
    target_date = (jst_now() - timedelta(days=1)).date()
    day_rows = [r for r in rows if r["date"] == target_date]
    total=0.0; wins=0; cnt=0
    for r in day_rows:
        p = r["pnl_pct"]; total += p; cnt += 1; wins += 1 if p>=0 else 0
    wr = (wins/cnt*100.0) if cnt>0 else 0.0
    return target_date, total, wr, cnt

# ---- (2) 直近7日/30日 比較 ----
def slice_since(days, rows):
    since = jst_now() - timedelta(days=days)
    return [r for r in rows if r["close_ts"] >= since]

def summarize_by_agent(rows):
    by = {}
    for r in rows:
        a = r["agent"]
        s = by.setdefault(a, {"sum":0.0,"wins":0,"losses":0,"cnt":0,"profit_sum":0.0,"loss_sum":0.0})
        p = r["pnl_pct"]
        s["sum"] += p; s["cnt"] += 1
        if p>=0: s["wins"] += 1; s["profit_sum"] += p
        else: s["losses"] += 1; s["loss_sum"] += abs(p)
    lines = []
    for a in sorted(by.keys()):
        s = by[a]
        wr = (s["wins"]/s["cnt"]*100.0) if s["cnt"]>0 else 0.0
        pf = (s["profit_sum"]/s["loss_sum"]) if s["loss_sum"]>0 else (s["profit_sum"] if s["profit_sum"]>0 else 0.0)
        lines.append((a, s["sum"], wr, pf, s["cnt"]))
    return lines

def fmt_compare(label, lines):
    order = {"fixed":0, "rt":1}
    lines = sorted(lines, key=lambda x: order.get(x[0], 9))
    if not lines:
        return f"・{label}\n  データなし"
    out = [f"・{label}"]
    for a, ssum, wr, pf, cnt in lines:
        out.append(f"  {a:5}: {ssum:+.2f}% / 勝率 {wr:.1f}% / PF {pf:.2f} / 件数 {cnt}")
    return "\n".join(out)

def pick_winner(lines_7d, lines_30d):
    # lines: [(agent, sum, wr, pf, cnt), ...]
    def best(lines):
        if not lines:
            return None
        # 合計損益 → 勝率 → PF の優先で評価
        return sorted(lines, key=lambda x: (x[1], x[2], x[3]))[-1][0]

    b7  = best(lines_7d)
    b30 = best(lines_30d)

    # 両期間で一致→それを採用
    if b7 and b30 and b7 == b30:
        return b7
    # 片方だけ決まっている→それを採用
    if b7 and not b30:
        return b7
    if b30 and not b7:
        return b30
    # 両方出ていて食い違う→直近7日を優先
    if b7 and b30 and b7 != b30:
        return b7
    return None

# ---- (3) 全期間 + 月次 ----
def agg_all(rows):
    total=0.0; wins=0; cnt=0
    by_sym=defaultdict(float)
    for r in rows:
        p = r["pnl_pct"]; total+=p; cnt+=1; wins += 1 if p>=0 else 0
        by_sym[r["symbol"]] += p
    wr = (wins/cnt*100.0) if cnt>0 else 0.0
    profit_sum = sum(max(0.0, r["pnl_pct"]) for r in rows)
    loss_sum = sum(max(0.0, -r["pnl_pct"]) for r in rows)
    pf = (profit_sum/loss_sum) if loss_sum>0 else (profit_sum if profit_sum>0 else 0.0)
    top = sorted(by_sym.items(), key=lambda x:-x[1])[:3]
    return total, wr, pf, cnt, top

def monthly_highlight(rows):
    by_month=defaultdict(float)
    for r in rows:
        key = r["close_ts"].strftime("%Y-%m")  # JSTの年月
        by_month[key] += r["pnl_pct"]
    return sorted(by_month.items(), key=lambda x:-x[1])[:2]

def post_discord(title, description, color=0x1abc9c):
    if not DISCORD_WEBHOOK:
        print("[warn] DISCORD_WEBHOOK not set"); return False
    payload = {"embeds":[{
        "title": title,
        "description": description,
        "color": color,
        "footer": {"text": "AIりんご式 総合レポート（JST）"}
    }]}
    r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=20)
    print("discord:", r.status_code, r.text[:120])
    return r.ok

def main():
    rows = load_rows()
    now_str = jst_now().strftime("%Y-%m-%d %H:%M JST")

    # (1) 昨日
    ymd, d_total, d_wr, d_cnt = daily_summary(rows)
    daily_block = f"**デイリー（{ymd.strftime('%Y-%m-%d')}）**\n損益: {d_total:+.2f}% / 勝率: {d_wr:.1f}% / 件数: {d_cnt}"

    # (2) 比較
    last7 = summarize_by_agent(slice_since(7, rows))
    last30 = summarize_by_agent(slice_since(30, rows))
    comp_block = "\n".join([
        fmt_compare("直近7日", last7),
        "",
        fmt_compare("直近30日", last30)
    ])

    # 勝者判定＆ active_agent.txt へ反映
    winner = pick_winner(last7, last30)
    winner_line = "🏁 総合判定：—（active_agentは変更しません）"
    if winner in ("fixed", "rt"):
        try:
            ACTIVE_AGENT_FILE.write_text(winner + "\n", encoding="utf-8")
            winner_line = f"🏁 総合判定：**{('固定' if winner=='fixed' else 'RT')}優勢**（通知対象を自動切替）"
        except Exception as e:
            winner_line = f"🏁 総合判定：{winner}（※ファイル更新失敗: {e}）"

    # (3) 全期間
    if rows:
        first = min(r["close_ts"] for r in rows).strftime("%Y-%m-%d")
        last  = max(r["close_ts"] for r in rows).strftime("%Y-%m-%d")
    else:
        first = last = "-"
    a_total, a_wr, a_pf, a_cnt, top = agg_all(rows)
    tops = monthly_highlight(rows)
    line_list = [f"**全期間（{first} ～ {last}）**",
                 f"総損益: {a_total:+.2f}% / 勝率: {a_wr:.1f}% / PF: {a_pf:.2f} / 件数: {a_cnt}"]
    if top:
        line_list.append("上位銘柄: " + ", ".join([f"{s}:{v:+.2f}%" for s,v in top]))
    if tops:
        line_list.append("月次ハイライト: " + ", ".join([f"{m}:{v:+.2f}%" for m,v in tops]))
    all_block = "\n".join(line_list)

    title = f"📦 総合レポート（JST）  {now_str}"
    desc = "\n\n".join([daily_block, comp_block, winner_line, all_block])

    ok = post_discord(title, desc, 0x5865F2)
    if not ok:
        return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
