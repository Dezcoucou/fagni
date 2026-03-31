from django.conf import settings
from django.utils import timezone
import requests

from logistics.models_pdf_log import MissionPdfSendLog


def _normalize_ci_phone(phone_number: str) -> str:
    raw = str(phone_number or "").strip().replace(" ", "").replace("-", "")
    if not raw:
        return ""
    if raw.startswith("225"):
        return raw
    if raw.startswith("0"):
        return "225" + raw
    if raw.startswith("+225"):
        return raw.replace("+", "")
    return raw.replace("+", "")


def _short_error(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if len(raw) <= 300:
        return raw
    return raw[:300] + "..."


def send_mission_pdf_whatsapp_meta(mission):
    base_url = (getattr(settings, "SITE_BASE_URL", "") or "").strip().rstrip("/")
    pdf_url = f"{base_url}/logistics/mission/{mission.id}/client-pdf/" if base_url else ""

    phone = _normalize_ci_phone(getattr(mission, "contact_phone", "") or "")
    phone_id = (getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "") or "").strip()
    access_token = (getattr(settings, "WHATSAPP_ACCESS_TOKEN", "") or "").strip()

    log = MissionPdfSendLog.objects.create(
        mission=mission,
        order=getattr(mission, "order", None),
        channel="whatsapp_meta",
        recipient=phone,
        status="pending",
        pdf_url=pdf_url,
    )

    if not phone_id:
        log.status = "failed"
        log.error_message = "WHATSAPP_PHONE_NUMBER_ID absent"
        log.save(update_fields=["status", "error_message"])
        return "WHATSAPP_PHONE_NUMBER_ID absent"

    if not access_token:
        log.status = "failed"
        log.error_message = "WHATSAPP_ACCESS_TOKEN absent"
        log.save(update_fields=["status", "error_message"])
        return "WHATSAPP_ACCESS_TOKEN absent"

    if not phone:
        log.status = "failed"
        log.error_message = "Téléphone client absent"
        log.save(update_fields=["status", "error_message"])
        return "Téléphone client absent"

    if not pdf_url:
        log.status = "failed"
        log.error_message = "SITE_BASE_URL absent"
        log.save(update_fields=["status", "error_message"])
        return "SITE_BASE_URL absent"

    url = f"https://graph.facebook.com/v25.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "document",
        "document": {
            "link": pdf_url,
            "filename": f"FAGNI_PREUVE_{mission.code}.pdf",
            "caption": (
                f"FAGNI ✅\n\n"
                f"Preuve de service – Mission {mission.code}\n"
                f"Merci pour votre confiance 🙏"
            ),
        },
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        data = response.json()

        if response.status_code == 200 and data.get("messages"):
            msg_id = data["messages"][0].get("id", "") or ""
            log.status = "sent"
            log.provider_message_sid = msg_id
            log.sent_at = timezone.now()
            log.error_message = ""
            log.save(update_fields=["status", "provider_message_sid", "sent_at", "error_message"])
            return f"OK:{msg_id}"

        log.status = "failed"
        log.error_message = _short_error(data)
        log.save(update_fields=["status", "error_message"])
        return _short_error(data)

    except Exception as e:
        log.status = "failed"
        log.error_message = _short_error(str(e))
        log.save(update_fields=["status", "error_message"])
        return _short_error(str(e))


def send_mission_pdf_whatsapp(mission):
    return send_mission_pdf_whatsapp_meta(mission)


def retry_last_failed_pdf_send(mission):
    last_failed = MissionPdfSendLog.objects.filter(
        mission=mission,
        channel__in=["whatsapp", "whatsapp_meta"],
        status="failed",
    ).first()

    if not last_failed:
        return "Aucun envoi échoué à relancer"

    return send_mission_pdf_whatsapp(mission)
