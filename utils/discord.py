import requests

def send_discord(webhook_url: str, title: str, description: str, color: int = 0x00CCFF):
    if not webhook_url:
        print("⚠ DISCORD webhook未設定:", title, description)
        return

    payload = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
            }
        ]
    }

    try:
        r = requests.post(webhook_url, json=payload, timeout=8)
        print("Discord status:", r.status_code)
    except Exception as e:
        print("Discord送信エラー:", e)
