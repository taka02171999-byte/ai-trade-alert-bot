import requests
import json
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

def _now_jst():
    return datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")

def send_discord(
    webhook: str,
    msg: str,
    *,
    title: str = "AIりんご式トレード通知",
    color: int = 0x00ccff,
    footer: str | None = None,
    timeout: int = 10,
) -> bool:
    """
    Discord Webhookへ送信。
    成功: True / 失敗: False
    失敗時に「理由がわかるログ」を必ず出す。
    """
    if not webhook:
        print("⚠ [send_discord] DISCORD_WEBHOOK is empty")
        return False

    # ログにWebhook全文を出さない（漏洩防止）
    safe_webhook = webhook[:45] + "..." if len(webhook) > 45 else webhook

    if footer is None:
        footer = f"AIりんご式 | {_now_jst()}"

    payload = {
        "embeds": [
            {
                "title": title,
                "description": msg,
                "color": color,
                "footer": {"text": footer},
            }
        ]
    }

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ai-ringo-bot/1.0",
    }

    try:
        r = requests.post(webhook, json=payload, headers=headers, timeout=timeout)

        # Discord webhookの成功はだいたい 204 No Content
        ok = (200 <= r.status_code < 300)

        print(f"[send_discord] webhook={safe_webhook} status={r.status_code} ok={ok}")

        # 失敗時はDiscordの返答を全部出す（ここが原因特定に重要）
        if not ok:
            # できるだけ情報を出す
            body_text = ""
            try:
                body_text = r.text
            except Exception:
                body_text = "<no text>"

            # JSONなら整形して出す
            try:
                body_json = r.json()
                print("[send_discord] response_json =", json.dumps(body_json, ensure_ascii=False))
            except Exception:
                print("[send_discord] response_text =", body_text)

        return ok

    except Exception as e:
        print(f"[send_discord] EXCEPTION: {type(e).__name__}: {e} webhook={safe_webhook}")
        return False
