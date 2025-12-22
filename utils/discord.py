import requests

def send_discord(webhook: str, msg: str):
    if not webhook:
        print("⚠ DISCORD_WEBHOOK not set")
        return

    data = {
        "embeds": [
            {
                "title": "AIりんご式 レポート",
                "description": msg,
                "color": 0x00ccff
            }
        ]
    }

    try:
        r = requests.post(webhook, json=data, timeout=10)
        print(f"[send_discord] status={r.status_code}")
    except Exception as e:
        print(f"[send_discord] error: {e}")
