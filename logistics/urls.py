from .views_dashboard import logistics_dashboard
from django.urls import path
from .views import (
    driver_mission_v2,
    driver_mission_v2_start,
    driver_mission_v2_arrive,
    driver_mission_v2_complete,
    driver_mission_v2_create_incident,
    create_proof_v2,
    mission_send_otp_whatsapp,
    mission_check_otp_whatsapp,
    create_signature_v2,
    mission_client_pdf,
    resend_mission_client_pdf,
    retry_mission_client_pdf,
)

app_name = "logistics"

urlpatterns = [
    path("dashboard/", logistics_dashboard, name="dashboard"),
    path("mission/<int:mission_id>/resend-client-pdf/", resend_mission_client_pdf, name="resend_mission_client_pdf"),
    path("mission/<int:mission_id>/retry-client-pdf/", retry_mission_client_pdf, name="retry_mission_client_pdf"),

    path("driver/mission-v2/<int:mission_id>/", driver_mission_v2, name="driver_mission_v2"),
    path("driver/mission-v2/<int:mission_id>/start/", driver_mission_v2_start, name="driver_mission_v2_start"),
    path("driver/mission-v2/<int:mission_id>/arrive/", driver_mission_v2_arrive, name="driver_mission_v2_arrive"),
    path("driver/mission-v2/<int:mission_id>/complete/", driver_mission_v2_complete, name="driver_mission_v2_complete"),
    path("driver/mission-v2/<int:mission_id>/incident/", driver_mission_v2_create_incident, name="driver_mission_v2_create_incident"),
    path("mission/<int:mission_id>/proof/", create_proof_v2, name="create_proof_v2"),
    path("mission/<int:mission_id>/otp/send/", mission_send_otp_whatsapp, name="mission_send_otp_whatsapp"),
    path("mission/<int:mission_id>/otp/check/", mission_check_otp_whatsapp, name="mission_check_otp_whatsapp"),
    path("mission/<int:mission_id>/signature/", create_signature_v2, name="create_signature_v2"),
    path("mission/<int:mission_id>/client-pdf/", mission_client_pdf, name="mission_client_pdf"),
]
