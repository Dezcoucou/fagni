from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from logistics.models import Mission
from logistics.models_otp import MissionOTP
from logistics.models_proof import ProofOfDelivery
from logistics.models_signature import MissionSignature
from logistics.models_pdf_log import MissionPdfSendLog
from logistics.pdf_utils import build_mission_client_pdf
from logistics.services_pdf_send import send_mission_pdf_whatsapp, retry_last_failed_pdf_send
from logistics.services import start_mission, mark_mission_arrived, complete_mission
from logistics.twilio_messaging import send_whatsapp_otp, generate_otp_code
from production.models import PartnerJob, WeighingRecord
from tracking.models import Incident, TrackingEvent
from tracking.services import create_incident, create_tracking_event


def _humanize_twilio_error(error_text: str) -> str:
    raw = (error_text or "").strip()

    if "63038" in raw or "exceeded the 5 daily messages limit" in raw:
        return "Limite quotidienne Twilio atteinte pour les messages WhatsApp de test."

    if "20003" in raw or "Authentication Error - invalid username" in raw:
        return "Identifiants Twilio invalides ou compte non reconnu."

    if "403" in raw and "Unable to create record" in raw:
        return "Twilio a refusé la création du message. Vérifie le sandbox WhatsApp, le template et les autorisations du compte."

    if "TWILIO_WHATSAPP_ENABLED est désactivé" in raw:
        return "L'envoi WhatsApp est désactivé dans la configuration."

    if "Code OTP invalide" in raw:
        return "Code OTP invalide."

    if "OTP expiré" in raw:
        return "OTP expiré."

    return raw or "Erreur inconnue."



def driver_mission_v2(request, mission_id):
    mission = get_object_or_404(
        Mission.objects.select_related(
            "order",
            "source_address",
            "destination_address",
        ),
        pk=mission_id,
    )

    partner_jobs = PartnerJob.objects.filter(order=mission.order).order_by("-created_at")
    weighings = WeighingRecord.objects.filter(order=mission.order).order_by("-recorded_at")
    tracking_events = TrackingEvent.objects.filter(order=mission.order).order_by("-created_at")
    incidents = Incident.objects.filter(order=mission.order).order_by("-reported_at")
    delivery_proofs = ProofOfDelivery.objects.filter(mission=mission).order_by("-created_at")
    signatures = MissionSignature.objects.filter(mission=mission).order_by("-created_at")
    otp_records = MissionOTP.objects.filter(mission=mission).order_by("-created_at")
    pdf_send_logs = MissionPdfSendLog.objects.filter(mission=mission).order_by("-created_at")[:5]
    latest_otp = otp_records.first()
    latest_otp_active = otp_records.filter(status__in=["sent", "approved"]).first()

    quota_reached = False
    if latest_otp and getattr(latest_otp, "last_error", ""):
        err = (latest_otp.last_error or "").lower()
        quota_reached = ("63038" in err) or ("5 daily messages limit" in err) or ("limite quotidienne twilio atteinte" in err)

    context = {
        "mission": mission,
        "order": mission.order,
        "partner_jobs": partner_jobs,
        "weighings": weighings,
        "tracking_events": tracking_events,
        "incidents": incidents,
        "delivery_proofs": delivery_proofs,
        "signatures": signatures,
        "otp_records": otp_records,
        "latest_otp": latest_otp,
        "latest_otp_active": latest_otp_active,
        "pdf_send_logs": pdf_send_logs,
        "quota_reached": quota_reached,
        "twilio_whatsapp_enabled": getattr(settings, "TWILIO_WHATSAPP_ENABLED", False),
    }
    return render(request, "driver/mission_v2.html", context)


@require_POST
def driver_mission_v2_start(request, mission_id):
    with transaction.atomic():
        mission = get_object_or_404(Mission.objects.select_for_update(), pk=mission_id)
        before = mission.status
        try:
            start_mission(mission=mission, notes="Mission démarrée depuis l'interface V2")
            create_tracking_event(
                order=mission.order,
                service_execution=mission.service_execution,
                mission=mission,
                event_type="mission_started",
                actor_user=request.user if request.user.is_authenticated else None,
                actor_role="ops" if request.user.is_authenticated else "system",
                title="Mission démarrée",
                description="Démarrage déclenché depuis l’interface mission V2.",
                status_before=before,
                status_after=mission.status,
            )
            messages.success(request, f"Mission {mission.code} démarrée.")
        except Exception as e:
            messages.error(request, f"Erreur démarrage mission {mission.code} : {e}")
    return redirect("logistics:driver_mission_v2", mission_id=mission_id)


@require_POST
def driver_mission_v2_arrive(request, mission_id):
    with transaction.atomic():
        mission = get_object_or_404(Mission.objects.select_for_update(), pk=mission_id)
        before = mission.status
        try:
            mark_mission_arrived(mission=mission, notes="Arrivée confirmée depuis l'interface V2")

            phone = (mission.contact_phone or "").strip()
            if phone:
                try:
                    otp_code = generate_otp_code()
                    result = send_whatsapp_otp(phone_number=phone, otp_code=otp_code)

                    MissionOTP.objects.create(
                        mission=mission,
                        order=mission.order,
                        phone_number=result.get("to", phone),
                        channel="whatsapp",
                        provider="twilio_messaging",
                        otp_code=otp_code,
                        message_sid=result.get("sid", ""),
                        status="sent",
                        sent_at=timezone.now(),
                        expires_at=result.get("expires_at"),
                        created_by=request.user if request.user.is_authenticated else None,
                    )
                    messages.success(
                        request,
                        f"Arrivée confirmée et OTP WhatsApp envoyé à {result.get('to', phone)}."
                    )
                except Exception as otp_error:
                    MissionOTP.objects.create(
                        mission=mission,
                        order=mission.order,
                        phone_number=phone,
                        channel="whatsapp",
                        provider="twilio_messaging",
                        status="failed",
                        last_error=_humanize_twilio_error(str(otp_error)),
                        created_by=request.user if request.user.is_authenticated else None,
                    )
                    messages.warning(
                        request,
                        f"Arrivée confirmée pour {mission.code}, mais l'envoi OTP a échoué : {_humanize_twilio_error(str(otp_error))}"
                    )
            else:
                messages.warning(
                    request,
                    f"Arrivée confirmée pour {mission.code}, mais aucun téléphone n'est disponible pour envoyer l'OTP."
                )

            create_tracking_event(
                order=mission.order,
                service_execution=mission.service_execution,
                mission=mission,
                event_type="mission_started",
                actor_user=request.user if request.user.is_authenticated else None,
                actor_role="ops" if request.user.is_authenticated else "system",
                title="Arrivée confirmée",
                description="Arrivée mission confirmée depuis l’interface mission V2.",
                status_before=before,
                status_after=mission.status,
            )
        except Exception as e:
            messages.error(request, f"Erreur arrivée mission {mission.code} : {e}")
    return redirect("logistics:driver_mission_v2", mission_id=mission_id)


@require_POST
def driver_mission_v2_complete(request, mission_id):
    with transaction.atomic():
        mission = get_object_or_404(Mission.objects.select_for_update(), pk=mission_id)
        before = mission.status

        proofs_qs = ProofOfDelivery.objects.filter(mission=mission, status="validated")
        if not proofs_qs.exists():
            messages.error(
                request,
                f"Impossible de terminer {mission.code} : aucune preuve validée."
            )
            return redirect("logistics:driver_mission_v2", mission_id=mission_id)

        otp_active_qs = MissionOTP.objects.filter(mission=mission, status__in=["sent", "approved"])
        otp_qs = otp_active_qs.filter(status="approved")
        if otp_active_qs.exists() and not otp_qs.exists():
            messages.error(
                request,
                f"Impossible de terminer {mission.code} : OTP WhatsApp non validé."
            )
            return redirect("logistics:driver_mission_v2", mission_id=mission_id)

        signatures_qs = MissionSignature.objects.filter(mission=mission, status="validated")
        if not signatures_qs.exists():
            messages.error(
                request,
                f"Impossible de terminer {mission.code} : signature client requise."
            )
            return redirect("logistics:driver_mission_v2", mission_id=mission_id)

        try:
            complete_mission(
                mission=mission,
                notes="Mission terminée depuis l'interface V2",
                action_type="completed",
            )
            create_tracking_event(
                order=mission.order,
                service_execution=mission.service_execution,
                mission=mission,
                event_type="delivery_completed",
                actor_user=request.user if request.user.is_authenticated else None,
                actor_role="ops" if request.user.is_authenticated else "system",
                title="Mission terminée",
                description="Fin de mission déclenchée depuis l’interface mission V2.",
                status_before=before,
                status_after=mission.status,
            )
            send_status = send_mission_pdf_whatsapp(mission)
            messages.success(request, f"Mission {mission.code} terminée. PDF envoyé ({send_status})")
        except Exception as e:
            messages.error(request, f"Erreur fin mission {mission.code} : {e}")

    return redirect("logistics:driver_mission_v2", mission_id=mission_id)


@require_POST
def driver_mission_v2_create_incident(request, mission_id):
    with transaction.atomic():
        mission = get_object_or_404(
            Mission.objects.select_related("order").select_for_update(),
            pk=mission_id,
        )
        try:
            create_incident(
                order=mission.order,
                service_execution=mission.service_execution,
                mission=mission,
                incident_type="delay",
                title="Incident déclaré depuis mission V2",
                description="Incident de test créé depuis l'interface mission V2.",
                severity="medium",
                reported_by=request.user if request.user.is_authenticated else None,
            )
            create_tracking_event(
                order=mission.order,
                service_execution=mission.service_execution,
                mission=mission,
                event_type="issue_reported",
                actor_user=request.user if request.user.is_authenticated else None,
                actor_role="ops" if request.user.is_authenticated else "system",
                title="Incident déclaré",
                description="Incident créé depuis l’interface mission V2.",
                status_before=mission.status,
                status_after=mission.status,
            )
            messages.success(request, f"Incident créé pour {mission.code}.")
        except Exception as e:
            messages.error(request, f"Erreur création incident {mission.code} : {e}")
    return redirect("logistics:driver_mission_v2", mission_id=mission_id)


@require_POST
def create_proof_v2(request, mission_id):
    mission = get_object_or_404(Mission, pk=mission_id)

    photo = request.FILES.get("photo")
    notes = request.POST.get("notes")

    proof = ProofOfDelivery.objects.create(
        mission=mission,
        order=mission.order,
        service_execution=mission.service_execution,
        photo=photo,
        notes=notes,
        created_by=request.user if request.user.is_authenticated else None,
    )

    proof.status = "validated"
    proof.save(update_fields=["status"])

    messages.success(request, "Preuve enregistrée avec succès.")
    return redirect("logistics:driver_mission_v2", mission_id=mission.id)


@require_POST
def mission_send_otp_whatsapp(request, mission_id):
    mission = get_object_or_404(Mission.objects.select_related("order"), pk=mission_id)
    phone = (mission.contact_phone or "").strip()

    if not phone:
        messages.error(request, "Aucun numéro client sur cette mission.")
        return redirect("logistics:driver_mission_v2", mission_id=mission.id)

    try:
        otp_code = generate_otp_code()
        result = send_whatsapp_otp(phone_number=phone, otp_code=otp_code)

        MissionOTP.objects.create(
            mission=mission,
            order=mission.order,
            phone_number=result.get("to", phone),
            channel="whatsapp",
            provider="twilio_messaging",
            otp_code=otp_code,
            message_sid=result.get("sid", ""),
            status="sent",
            sent_at=timezone.now(),
            expires_at=result.get("expires_at"),
            created_by=request.user if request.user.is_authenticated else None,
        )
        messages.success(request, f"OTP WhatsApp envoyé à {result.get('to', phone)}.")
    except Exception as e:
        MissionOTP.objects.create(
            mission=mission,
            order=mission.order,
            phone_number=phone,
            channel="whatsapp",
            provider="twilio_messaging",
            status="failed",
            last_error=_humanize_twilio_error(str(e)),
            created_by=request.user if request.user.is_authenticated else None,
        )
        messages.error(request, f"Échec envoi OTP WhatsApp : {_humanize_twilio_error(str(e))}")

    return redirect("logistics:driver_mission_v2", mission_id=mission.id)


@require_POST
def mission_check_otp_whatsapp(request, mission_id):
    mission = get_object_or_404(Mission.objects.select_related("order"), pk=mission_id)
    code = (request.POST.get("verification_code") or "").strip()

    if not code:
        messages.error(request, "Aucun code OTP saisi.")
        return redirect("logistics:driver_mission_v2", mission_id=mission.id)

    otp_record = MissionOTP.objects.filter(mission=mission).order_by("-created_at").first()
    if not otp_record:
        messages.error(request, "Aucun OTP envoyé pour cette mission.")
        return redirect("logistics:driver_mission_v2", mission_id=mission.id)

    otp_record.attempts += 1

    if otp_record.expires_at and timezone.now() > otp_record.expires_at:
        otp_record.status = "expired"
        otp_record.last_error = "OTP expiré."
        otp_record.save(update_fields=["attempts", "status", "last_error", "updated_at"])
        messages.error(request, "OTP expiré.")
        return redirect("logistics:driver_mission_v2", mission_id=mission.id)

    if code == otp_record.otp_code:
        otp_record.status = "approved"
        otp_record.verified_at = timezone.now()
        otp_record.last_error = ""
        otp_record.save(update_fields=["attempts", "status", "verified_at", "last_error", "updated_at"])

        if mission.service_execution_id is not None:
            from services.services import complete_service_execution_if_ready

            complete_service_execution_if_ready(
                service_execution=mission.service_execution,
                note=(
                    "ServiceExecution réévaluée après validation OTP "
                    f"de la mission {mission.code}."
                ),
            )

        messages.success(request, "OTP WhatsApp validé.")
    else:
        otp_record.status = "failed"
        otp_record.last_error = "Code OTP invalide."
        otp_record.save(update_fields=["attempts", "status", "last_error", "updated_at"])
        messages.error(request, "OTP invalide.")

    return redirect("logistics:driver_mission_v2", mission_id=mission.id)


@require_POST
def create_signature_v2(request, mission_id):
    import base64
    from django.core.files.base import ContentFile

    mission = get_object_or_404(Mission, pk=mission_id)

    signature_data = (request.POST.get("signature_data") or "").strip()
    signer_name = (request.POST.get("signer_name") or "").strip()
    notes = (request.POST.get("signature_notes") or "").strip()

    if not signature_data.startswith("data:image/png;base64,"):
        messages.error(request, "Signature invalide ou vide.")
        return redirect("logistics:driver_mission_v2", mission_id=mission.id)

    try:
        header, encoded = signature_data.split(",", 1)
        image_data = base64.b64decode(encoded)
    except Exception:
        messages.error(request, "Impossible de décoder la signature.")
        return redirect("logistics:driver_mission_v2", mission_id=mission.id)

    filename = f"mission_{mission.id}_signature.png"

    signature = MissionSignature.objects.create(
        mission=mission,
        order=mission.order,
        signer_name=signer_name,
        signer_role="customer",
        notes=notes,
        image=ContentFile(image_data, name=filename),
        status="captured",
        created_by=request.user if request.user.is_authenticated else None,
    )

    signature.status = "validated"
    signature.save(update_fields=["status"])

    if mission.service_execution_id is not None:
        from services.services import complete_service_execution_if_ready

        complete_service_execution_if_ready(
            service_execution=mission.service_execution,
            note=(
                "ServiceExecution réévaluée après validation de la signature "
                f"de la mission {mission.code}."
            ),
        )

    messages.success(request, "Signature enregistrée avec succès.")
    return redirect("logistics:driver_mission_v2", mission_id=mission.id)


def mission_client_pdf(request, mission_id):
    mission = get_object_or_404(
        Mission.objects.select_related("order", "source_address", "destination_address"),
        pk=mission_id,
    )

    latest_otp = MissionOTP.objects.filter(mission=mission).order_by("-created_at").first()
    latest_proof = ProofOfDelivery.objects.filter(mission=mission).order_by("-created_at").first()
    latest_signature = MissionSignature.objects.filter(mission=mission).order_by("-created_at").first()

    return build_mission_client_pdf(
        mission=mission,
        latest_otp=latest_otp,
        latest_proof=latest_proof,
        latest_signature=latest_signature,
    )


from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from logistics.models import Mission
from logistics.services_pdf_send import send_mission_pdf_whatsapp

def resend_mission_client_pdf(request, mission_id):
    mission = get_object_or_404(Mission, pk=mission_id)

    try:
        status = send_mission_pdf_whatsapp(mission)
        if str(status).startswith("OK:"):
            messages.success(request, "PDF client renvoyé sur WhatsApp.")
        else:
            messages.warning(request, f"Renvoyer PDF : {status}")
    except Exception as e:
        messages.error(request, f"Erreur envoi PDF: {e}")

    return redirect("logistics:driver_mission_v2", mission_id=mission_id)


def retry_mission_client_pdf(request, mission_id):
    mission = get_object_or_404(Mission, pk=mission_id)
    try:
        status = retry_last_failed_pdf_send(mission)
        if str(status).startswith("OK:"):
            messages.success(request, "Retry PDF réussi sur WhatsApp.")
        else:
            messages.warning(request, f"Retry PDF : {status}")
    except Exception as e:
        messages.error(request, f"Erreur retry PDF: {e}")
    return redirect("logistics:driver_mission_v2", mission_id=mission_id)
