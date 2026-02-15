import os
import json
import urllib.request
import urllib.error

ONESIGNAL_APP_ID = os.environ.get("ONESIGNAL_APP_ID", "").strip()
ONESIGNAL_REST_API_KEY = os.environ.get("ONESIGNAL_REST_API_KEY", "").strip()

def onesignal_enabled() -> bool:
    return bool(ONESIGNAL_APP_ID and ONESIGNAL_REST_API_KEY)

def send_push(external_user_ids, title: str, body: str, data=None):
    """
    external_user_ids: list[str] (on utilisera driver_id ou user_id comme external_user_id côté client)
    """
    if not onesignal_enabled():
        return {"ok": False, "error": "ONESIGNAL_NOT_CONFIGURED"}

    if not external_user_ids:
        return {"ok": False, "error": "NO_RECIPIENTS"}

    payload = {
        "app_id": ONESIGNAL_APP_ID,
        "include_external_user_ids": [str(x) for x in external_user_ids],
        "headings": {"en": title, "fr": title},
        "contents": {"en": body, "fr": body},
        "data": data or {},
    }

    req = urllib.request.Request(
        "https://onesignal.com/api/v1/notifications",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Basic {ONESIGNAL_REST_API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            out = resp.read().decode("utf-8", errors="replace")
            return {"ok": True, "status": resp.status, "raw": out}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": getattr(e, "code", None), "raw": e.read().decode("utf-8", errors="replace")}
    except Exception as e:
        return {"ok": False, "error": str(e)}
