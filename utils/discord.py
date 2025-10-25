import requests
import json

def send_discord(webhook_url: str, content: str):
    """Discord通知を送る"""
    if not webhook_url:
        print("[Discord MOCK]\n" + content)
        return

    headers = {"Content-Type": "application/json"}
    payload = {"content": content}
    try:
        requests.post(webhook_url, headers=headers, data=json.dumps(payload), timeout=5)
    except Exception as e:
        print(f"[Discord ERROR] {e}")
        print("FAILED CONTENT:\n", content)
