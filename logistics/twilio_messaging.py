import random
from datetime import timedelta

from django.conf import settings
from django.utils import timezone


def normalize_ci_phone(phone_number: str) -> str:
    raw = str(phone_number or "").strip().replace(" ", "").replace("-", "")

    if not raw:
        return ""

    if raw.startswith("+225"):
        return raw

    if raw.startswith("225"):
        return "+" + raw

    if raw.startswith("0"):
        return "+225" + raw

    if raw.startswith("+"):
        return raw

    return "+225" + raw

    if raw.startswith("0") and len(raw) == 10:
        return f"+225{raw}"

    if len(raw) == 10 and not raw.startswith("0"):
        return f"+225{raw}"

    if raw.startswith("+"):
        return raw

    return f"+{raw}"


def generate_otp_code(length: int = 6) -> str:
    return "".join(random.choice("0123456789") for _ in range(length))


def _client():
    try:
        from twilio.rest import Client
    except ImportError as exc:
        raise RuntimeError(
            "Le SDK Twilio n'est pas installé dans cet environnement."
        ) from exc

    return Client(
        settings.TWILIO_ACCOUNT_SID,
        settings.TWILIO_AUTH_TOKEN,
    )


def send_whatsapp_otp(*, phone_number: str, otp_code: str):
    if not getattr(settings, "TWILIO_WHATSAPP_ENABLED", False):
        raise RuntimeError("TWILIO_WHATSAPP_ENABLED est désactivé.")

    normalized = normalize_ci_phone(phone_number)
    if not normalized:
        raise ValueError("Numéro téléphone invalide ou vide.")

    client = _client()

    to_value = f"whatsapp:{normalized}"

    content_sid = getattr(settings, "TWILIO_WHATSAPP_CONTENT_SID", "")
    from_value = getattr(settings, "TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

    if content_sid:
        message = client.messages.create(
            from_=from_value,
            content_sid=content_sid,
            content_variables='{"1":"' + otp_code + '"}',
            to=to_value,
        )
    else:
        message = client.messages.create(
            from_=from_value,
            body=f"{otp_code} est votre code de vérification. Pour votre sécurité, ne partagez pas ce code.",
            to=to_value,
        )

    return {
        "sid": message.sid,
        "status": message.status,
        "to": normalized,
        "expires_at": timezone.now() + timedelta(minutes=10),
    }
