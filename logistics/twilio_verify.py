from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from twilio.rest import Client


def normalize_ci_phone(phone_number: str) -> str:
    """
    Normalise un numéro ivoirien vers E.164.
    Exemples:
    0748643892 -> +2250748643892
    2250748643892 -> +2250748643892
    +2250748643892 -> +2250748643892
    """
    raw = "".join(ch for ch in str(phone_number or "") if ch.isdigit() or ch == "+").strip()

    if not raw:
        return ""

    if raw.startswith("+225"):
        return raw

    if raw.startswith("225"):
        return f"+{raw}"

    if raw.startswith("0") and len(raw) == 10:
        return f"+225{raw}"

    if len(raw) == 10 and not raw.startswith("0"):
        return f"+225{raw}"

    if raw.startswith("+"):
        return raw

    return f"+{raw}"


def _client():
    return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def send_whatsapp_verification(*, phone_number: str):
    if not getattr(settings, "TWILIO_VERIFY_ENABLED", False):
        raise RuntimeError("TWILIO_VERIFY_ENABLED est désactivé.")

    normalized = normalize_ci_phone(phone_number)
    if not normalized:
        raise ValueError("Numéro téléphone invalide ou vide.")

    client = _client()
    verification = (
        client.verify.v2.services(settings.TWILIO_VERIFY_SERVICE_SID)
        .verifications
        .create(to=normalized, channel="whatsapp")
    )
    return {
        "sid": verification.sid,
        "status": verification.status,
        "to": normalized,
        "expires_at": timezone.now() + timedelta(minutes=10),
    }


def check_whatsapp_verification(*, phone_number: str, code: str):
    normalized = normalize_ci_phone(phone_number)
    if not normalized:
        raise ValueError("Numéro téléphone invalide ou vide.")

    client = _client()
    check = (
        client.verify.v2.services(settings.TWILIO_VERIFY_SERVICE_SID)
        .verification_checks
        .create(to=normalized, code=code)
    )
    return {
        "sid": check.sid,
        "status": check.status,
        "valid": check.status == "approved",
        "to": normalized,
    }
