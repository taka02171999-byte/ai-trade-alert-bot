import requests
import json

def send_discord(webhook_url: str, text: str):
    if not webhook_url:
        print("[Discord MOCK]", text)
        return

    headers = {"Content-Type": "application/json"}
    payload = {"content": text}

    try:
        requests.post(webhook_url, headers=headers, data=json.dumps(payload), timeout=5)
    except Exception as e:
        print("[Discord ERROR]", e)
        print("FAILED CONTENT >>>", text)
