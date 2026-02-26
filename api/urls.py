from django.urls import path
from . import views

urlpatterns = [
    path("", views.upload, name="upload"),
    path("order/thanks/", views.order_thanks, name="order_thanks"),

    # ✅ Webhook Wave
    path("payments/webhook/wave/", views.wave_webhook, name="wave_webhook"),

    # ✅ POD (Proof of Delivery) — OTP + Signature
    path("legs/<int:leg_id>/delivery-otp/create/", views.create_delivery_otp, name="create_delivery_otp"),
    path("legs/<int:leg_id>/delivery-otp/verify/", views.verify_delivery_otp, name="verify_delivery_otp"),
    path("legs/<int:leg_id>/delivery-proof/", views.submit_delivery_proof, name="submit_delivery_proof"),

    # ✅ POP (Proof of Pickup) — OTP + Signature
    path("legs/<int:leg_id>/pickup-otp/create/", views.create_pickup_otp, name="create_pickup_otp"),
    path("legs/<int:leg_id>/pickup-otp/verify/", views.verify_pickup_otp, name="verify_pickup_otp"),
    path("legs/<int:leg_id>/pickup-proof/", views.submit_pickup_proof, name="submit_pickup_proof"),


]
