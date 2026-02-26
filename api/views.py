import json
import re
from decimal import Decimal

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
import binascii
import base64
import random
from django.http import JsonResponse, HttpResponse
from django.db import transaction, IntegrityError
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .forms import SingleImageForm


def upload(request):
    if request.method == "POST":
        form = SingleImageForm(request.POST, request.FILES)
        form.is_valid()
    else:
        form = SingleImageForm()
    return render(request, "api/upload.html", {"form": form})


def order_thanks(request):
    return HttpResponse("Commande enregistrée. Merci !")


# -------------------------------------------------------------------
# ✅ WEBHOOK WAVE (MVP)
# - Enregistre un PaymentEvent (idempotent via constraint)
# - Si payment_succeeded: crée un Payment via Order.add_payment (guards + sync)
# -------------------------------------------------------------------
@csrf_exempt
@require_http_methods(["POST"])
def wave_webhook(request):
    """
    Webhook Wave (MVP) – robuste:
    - Log un PaymentEvent (idempotent via provider + provider_reference)
    - Tente d'attacher Order via client_reference (order-478-XYZ / order-478 / 478)
    - Si payment_succeeded:
        * order introuvable => ignored
        * déjà paid => processed (sans nouveau Payment)
        * sinon => crée un Payment via Order.add_payment(reference=provider_reference)
    - Duplicate:
        * si déjà processed => ne pas rétrograder
        * sinon => heal: rattache order si possible et retente add_payment
    """
    from orders.models import Order, PaymentEvent

    # ---- Parse JSON ----
    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
        if not isinstance(payload, dict):
            payload = {"raw": payload}
    except Exception:
        payload = {}

    provider = (payload.get("provider") or "wave").strip().lower()
    event_type = (payload.get("event_type") or payload.get("type") or "").strip().lower() or "unknown"
    provider_reference = (payload.get("provider_reference") or payload.get("id") or payload.get("reference") or "").strip()

    # ---- Parse order_id depuis client_reference / reference ----
    order_id = None
    try:
        cr = str(
            payload.get("client_reference")
            or payload.get("clientReference")
            or payload.get("merchant_reference")
            or payload.get("merchantReference")
            or payload.get("reference")
            or ""
        )
        m2 = re.search(r"(?:^|[^0-9])(?:order[-_])?(\d+)(?:[^0-9]|$)", cr)
        if m2:
            order_id = int(m2.group(1))
    except Exception:
        order_id = None

    order = Order.objects.filter(pk=order_id).first() if order_id else None

    if not provider_reference:
        provider_reference = f"no-ref-{provider}-{event_type}"

    kwargs = {
        "provider": provider,
        "event_type": event_type,
        "provider_reference": provider_reference,
    }
    defaults = {"payload": payload, "status": "pending"}

    if order:
        kwargs["order"] = order  # utile même si nullable

    def _process_payment(o):
        """
        Retente le traitement paiement si event_type == success.
        Retourne (final_status, reason)
        """
        if event_type not in ("payment_succeeded", "succeeded", "paid"):
            return ("processed", "event_not_success")

        if not o:
            return ("ignored", "order_not_found")

        if (getattr(o, "payment_status", "") or "").lower() == "paid":
            return ("processed", "already_paid")

        # montant
        amt_raw = payload.get("amount", None)
        amt = None
        try:
            if amt_raw is not None:
                amt = Decimal(str(amt_raw)).quantize(Decimal("1"))
        except Exception:
            amt = None

        try:
            total = Decimal(str(getattr(o, "total_client_ttc", 0) or 0))
            paid = Decimal(str(getattr(o, "amount_paid", 0) or 0))
            remaining = total - paid
            if remaining < 0:
                remaining = Decimal("0")
        except Exception:
            remaining = Decimal("0")

        if amt is None or amt <= 0:
            amt = remaining

        if amt <= 0:
            return ("processed", "nothing_to_pay")

        res = o.add_payment(
            amount=int(amt),
            channel="wave",
            reference=provider_reference,
            source="webhook",
            save=True,
        )
        if res is None:
            return ("processed", "add_payment_none")
        return ("processed", None)

    try:
        with transaction.atomic():
            evt, created = PaymentEvent.objects.get_or_create(**kwargs, defaults=defaults)

            # -------- Duplicate (heal) --------
            if not created:
                if (evt.status or "").lower() == "processed":
                    return JsonResponse(
                        {"ok": True, "created": False, "duplicate": True, "event_id": evt.id, "order_id": evt.order_id, "status": evt.status},
                        status=200,
                    )

                if not evt.order_id and order:
                    evt.order = order
                    evt.save(update_fields=["order"])

                final_status, reason = _process_payment(evt.order or order)
                evt.status = final_status
                evt.processed_at = timezone.now()
                evt.save(update_fields=["status", "processed_at"])

                out = {"ok": True, "created": False, "duplicate": True, "event_id": evt.id, "order_id": evt.order_id, "status": evt.status}
                if reason:
                    out["reason"] = reason
                return JsonResponse(out, status=200)

            # -------- Created: traitement normal --------
            final_status, reason = _process_payment(order)
            evt.status = final_status
            evt.processed_at = timezone.now()
            if not evt.order_id and order:
                evt.order = order
                evt.save(update_fields=["order", "status", "processed_at"])
            else:
                evt.save(update_fields=["status", "processed_at"])

            out = {"ok": True, "created": True, "event_id": evt.id, "order_id": evt.order_id, "status": evt.status}
            if reason:
                out["reason"] = reason
            return JsonResponse(out, status=200)

    except IntegrityError:
        return JsonResponse({"ok": True, "created": False, "duplicate": True}, status=200)
    except Exception as e:
        return JsonResponse({"ok": False, "error": "server_error", "details": str(e)}, status=500)


# -------------------------------------------------------------------
# ✅ POD (Proof of Delivery) — OTP + Signature (MVP)
# -------------------------------------------------------------------
@csrf_exempt
@require_http_methods(["POST"])
def create_delivery_otp(request, leg_id: int):
    """
    Génère un OTP de livraison pour la jambe RETURN uniquement.
    Règles:
      - return only (hard)
      - si déjà vérifié => refuse / n'écrase pas (idempotent)
      - OTP valable 30 minutes (MVP)
    """
    from orders.models import DeliveryLeg

    leg = DeliveryLeg.objects.filter(pk=leg_id).first()
    if not leg:
        return JsonResponse({"ok": False, "error": "leg_not_found"}, status=404)

    if (getattr(leg, "leg_type", None) or "").lower() != "return":
        return JsonResponse({"ok": False, "error": "return_only"}, status=400)

    # déjà vérifié => on ne régénère pas
    if getattr(leg, "delivery_otp_verified_at", None):
        return JsonResponse(
            {
                "ok": True,
                "leg_id": leg.pk,
                "already_verified": True,
                "verified_at": leg.delivery_otp_verified_at.isoformat(),
            },
            status=200,
        )

    otp = f"{random.randint(0, 999999):06d}"
    now = timezone.now()
    expires = now + timezone.timedelta(minutes=30)

    DeliveryLeg.objects.filter(pk=leg.pk).update(
        delivery_otp=otp,
        delivery_otp_expires_at=expires,
        delivery_otp_verified_at=None,
    )

    return JsonResponse(
        {
            "ok": True,
            "leg_id": leg.pk,
            "otp": otp,
            "expires_at": expires.isoformat(),
        },
        status=200,
    )


@csrf_exempt
@require_http_methods(["POST"])
def verify_delivery_otp(request, leg_id: int):
    """
    Vérifie OTP pour jambe RETURN uniquement.
    Règles:
      - return only (hard)
      - si déjà vérifié => OK (idempotent)
      - expire => otp_expired
      - mismatch => otp_invalid
    """
    from orders.models import DeliveryLeg

    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
        if not isinstance(payload, dict):
            payload = {"raw": payload}
    except Exception:
        payload = {}

    otp = str(payload.get("otp") or "").strip()

    with transaction.atomic():
        leg = DeliveryLeg.objects.select_for_update().filter(pk=leg_id).first()
        if not leg:
            return JsonResponse({"ok": False, "error": "leg_not_found"}, status=404)

        if (getattr(leg, "leg_type", None) or "").lower() != "return":
            return JsonResponse({"ok": False, "error": "return_only"}, status=400)

        # déjà vérifié => réponse idempotente
        if getattr(leg, "delivery_otp_verified_at", None):
            return JsonResponse(
                {
                    "ok": True,
                    "leg_id": leg.pk,
                    "already_verified": True,
                    "verified_at": leg.delivery_otp_verified_at.isoformat(),
                },
                status=200,
            )

        if not getattr(leg, "delivery_otp", None):
            return JsonResponse({"ok": False, "error": "otp_not_set"}, status=400)

        now = timezone.now()
        if getattr(leg, "delivery_otp_expires_at", None) and now > leg.delivery_otp_expires_at:
            return JsonResponse({"ok": False, "error": "otp_expired"}, status=400)

        if not otp or otp != str(leg.delivery_otp).strip():
            return JsonResponse({"ok": False, "error": "otp_invalid"}, status=400)

        DeliveryLeg.objects.filter(pk=leg.pk).update(delivery_otp_verified_at=now)

    return JsonResponse({"ok": True, "leg_id": leg_id, "verified_at": now.isoformat()}, status=200)


@csrf_exempt
@require_http_methods(["POST"])
def submit_delivery_proof(request, leg_id: int):
    """
    Body JSON:
      - signature: "data:image/png;base64,...." (ou base64 brut)
      - lat, lng (optionnel)

    Règles MVP durcies:
      - RETURN only (hard)
      - nécessite OTP vérifié
      - nécessite signature valide + limite taille
      - marque la jambe DONE + timestamp + coords
      - resync statut commande (pickup+return => done) via sync_order_status_from_legs
    """
    from orders.models import DeliveryLeg

    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
        if not isinstance(payload, dict):
            payload = {"raw": payload}
    except Exception:
        payload = {}

    signature = (payload.get("signature") or "").strip()
    lat = payload.get("lat", None)
    lng = payload.get("lng", None)

    # ---- validation signature (MVP) ----
    if not signature:
        return JsonResponse({"ok": False, "error": "signature_missing"}, status=400)

    MAX_BYTES = 300 * 1024
    b64_part = signature
    if signature.startswith("data:image/"):
        if "base64," not in signature:
            return JsonResponse({"ok": False, "error": "signature_invalid_format"}, status=400)
        b64_part = signature.split("base64,", 1)[1].strip()

    try:
        decoded = base64.b64decode(b64_part, validate=True)
    except (binascii.Error, ValueError):
        return JsonResponse({"ok": False, "error": "signature_invalid_base64"}, status=400)

    if len(decoded) > MAX_BYTES:
        return JsonResponse({"ok": False, "error": "signature_too_large"}, status=400)

    now = timezone.now()

    with transaction.atomic():
        leg = DeliveryLeg.objects.select_related("order").select_for_update().filter(pk=leg_id).first()
        if not leg:
            return JsonResponse({"ok": False, "error": "leg_not_found"}, status=404)

        if (getattr(leg, "leg_type", None) or "").lower() != "return":
            return JsonResponse({"ok": False, "error": "return_only"}, status=400)

        if not getattr(leg, "delivery_otp_verified_at", None):
            return JsonResponse({"ok": False, "error": "otp_not_verified"}, status=400)

        # garde-fou workflow : pickup doit être DONE avant RETURN
        pickup_leg = None
        try:
            if getattr(leg, "order_id", None):
                pickup_leg = (
                    leg.order.legs.filter(leg_type__iexact="pickup")
                    .order_by("-id")
                    .first()
                )
        except Exception:
            pickup_leg = None

        if pickup_leg and (pickup_leg.status or "").lower() != "done":
            return JsonResponse(
                {
                    "ok": False,
                    "error": "pickup_not_done",
                    "pickup_leg_id": pickup_leg.id,
                    "pickup_status": pickup_leg.status,
                },
                status=400,
            )

        # update leg
        DeliveryLeg.objects.filter(pk=leg.pk).update(
            client_signature=signature,
            client_signed_at=now,
            delivered_lat=lat,
            delivered_lng=lng,
            status="done",
            finished_at=now,
        )

        # resync order
        order_status = None
        try:
            from orders.models import sync_order_status_from_legs
            o = leg.order
            if o:
                sync_order_status_from_legs(o, save=True)
                o.refresh_from_db(fields=["status"])
                order_status = o.status
        except Exception:
            # même si resync foire, on renvoie au moins un statut lisible
            try:
                o = leg.order
                if o:
                    o.refresh_from_db(fields=["status"])
                    order_status = o.status
            except Exception:
                order_status = None

    out = {"ok": True, "leg_id": leg_id, "status": "done", "signed_at": now.isoformat()}
    if order_status is not None:
        out["order_status"] = order_status
    return JsonResponse(out, status=200)


# -------------------------------------------------------------------
# ✅ POP (Proof of Pickup) — OTP + Signature (MVP)
# -------------------------------------------------------------------
@csrf_exempt
@require_http_methods(["POST"])
def create_pickup_otp(request, leg_id: int):
    """
    Génère un OTP de collecte pour la jambe PICKUP uniquement.
    Règles:
      - pickup only (hard)
      - si déjà vérifié => refuse / n'écrase pas
      - OTP valable 30 minutes (MVP)
    """
    from orders.models import DeliveryLeg

    leg = DeliveryLeg.objects.filter(pk=leg_id).first()
    if not leg:
        return JsonResponse({"ok": False, "error": "leg_not_found"}, status=404)

    if (getattr(leg, "leg_type", None) or "").lower() != "pickup":
        return JsonResponse({"ok": False, "error": "pickup_only"}, status=400)

    # déjà vérifié => on ne régénère pas
    if getattr(leg, "pickup_otp_verified_at", None):
        return JsonResponse(
            {"ok": True, "leg_id": leg.pk, "already_verified": True, "verified_at": leg.pickup_otp_verified_at.isoformat()},
            status=200,
        )

    otp = f"{random.randint(0, 999999):06d}"
    now = timezone.now()
    expires = now + timezone.timedelta(minutes=30)

    DeliveryLeg.objects.filter(pk=leg.pk).update(
        pickup_otp=otp,
        pickup_otp_expires_at=expires,
        pickup_otp_verified_at=None,
    )

    return JsonResponse({"ok": True, "leg_id": leg.pk, "otp": otp, "expires_at": expires.isoformat()}, status=200)




@csrf_exempt
@require_http_methods(["POST"])
def verify_pickup_otp(request, leg_id: int):
    """
    Vérifie OTP pour jambe PICKUP uniquement.
    Règles:
      - pickup only (hard)
      - si déjà vérifié => OK (idempotent)
      - expire => otp_expired
      - mismatch => otp_invalid
    """
    from orders.models import DeliveryLeg

    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
        if not isinstance(payload, dict):
            payload = {"raw": payload}
    except Exception:
        payload = {}

    otp = str(payload.get("otp") or "").strip()

    with transaction.atomic():
        leg = DeliveryLeg.objects.select_for_update().filter(pk=leg_id).first()
        if not leg:
            return JsonResponse({"ok": False, "error": "leg_not_found"}, status=404)

        if (getattr(leg, "leg_type", None) or "").lower() != "pickup":
            return JsonResponse({"ok": False, "error": "pickup_only"}, status=400)

        # déjà vérifié => réponse idempotente
        if getattr(leg, "pickup_otp_verified_at", None):
            return JsonResponse(
                {"ok": True, "leg_id": leg.pk, "already_verified": True, "verified_at": leg.pickup_otp_verified_at.isoformat()},
                status=200,
            )

        if not getattr(leg, "pickup_otp", None):
            return JsonResponse({"ok": False, "error": "otp_not_set"}, status=400)

        now = timezone.now()
        if getattr(leg, "pickup_otp_expires_at", None) and now > leg.pickup_otp_expires_at:
            return JsonResponse({"ok": False, "error": "otp_expired"}, status=400)

        if not otp or otp != str(leg.pickup_otp).strip():
            return JsonResponse({"ok": False, "error": "otp_invalid"}, status=400)

        DeliveryLeg.objects.filter(pk=leg.pk).update(pickup_otp_verified_at=now)

    return JsonResponse({"ok": True, "leg_id": leg_id, "verified_at": now.isoformat()}, status=200)




@csrf_exempt
@require_http_methods(["POST"])
def submit_pickup_proof(request, leg_id: int):
    """
    Body JSON:
      - signature: "data:image/png;base64,...." (ou base64 brut)
      - lat, lng (optionnel)

    Règles MVP durcies:
      - PICKUP only (hard)
      - nécessite OTP vérifié
      - nécessite signature valide + limite taille
      - marque la jambe DONE + timestamp + coords
      - resync statut commande (pickup+return => done) via sync_order_status_from_legs
    """
    from orders.models import DeliveryLeg

    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
        if not isinstance(payload, dict):
            payload = {"raw": payload}
    except Exception:
        payload = {}

    signature = (payload.get("signature") or "").strip()
    lat = payload.get("lat", None)
    lng = payload.get("lng", None)

# ---- validation signature (MVP) ----
    if not signature:
        return JsonResponse({"ok": False, "error": "signature_missing"}, status=400)

    MAX_BYTES = 300 * 1024
    b64_part = signature
    if signature.startswith("data:image/"):
        if "base64," not in signature:
            return JsonResponse({"ok": False, "error": "signature_invalid_format"}, status=400)
        b64_part = signature.split("base64,", 1)[1].strip()

    try:
        decoded = base64.b64decode(b64_part, validate=True)
    except (binascii.Error, ValueError):
        return JsonResponse({"ok": False, "error": "signature_invalid_base64"}, status=400)

    if len(decoded) > MAX_BYTES:
        return JsonResponse({"ok": False, "error": "signature_too_large"}, status=400)

    now = timezone.now()

    with transaction.atomic():
        leg = DeliveryLeg.objects.select_related("order").select_for_update().filter(pk=leg_id).first()
        if not leg:
            return JsonResponse({"ok": False, "error": "leg_not_found"}, status=404)

        if (getattr(leg, "leg_type", None) or "").lower() != "pickup":
            return JsonResponse({"ok": False, "error": "pickup_only"}, status=400)

        if not getattr(leg, "pickup_otp_verified_at", None):
            return JsonResponse({"ok": False, "error": "otp_not_verified"}, status=400)

        # update leg
        DeliveryLeg.objects.filter(pk=leg.pk).update(
            pickup_signature=signature,
            pickup_signed_at=now,
            picked_up_lat=lat,
            picked_up_lng=lng,
            status="done",
            finished_at=now,
        )

        # resync order
        order_status = None
        try:
            from orders.models import sync_order_status_from_legs
            o = leg.order
            if o:
                sync_order_status_from_legs(o, save=True)
                o.refresh_from_db(fields=['status'])
                order_status = o.status
        except Exception:
            # même si resync foire, on renvoie au moins un statut lisible
            try:
                o = leg.order
                if o:
                    o.refresh_from_db(fields=['status'])
                    order_status = o.status
            except Exception:
                order_status = None


    out = {"ok": True, "leg_id": leg_id, "status": "done", "signed_at": now.isoformat()}
    if order_status is not None:
        out["order_status"] = order_status
    return JsonResponse(out, status=200)
