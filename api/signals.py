from django.conf import settings
from django.core.mail import send_mail, EmailMessage
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Order

def _mail_to_admin(subject, body):
    if getattr(settings, "ADMINS", None):
        to = [email for _, email in settings.ADMINS]
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, to, fail_silently=True)

@receiver(post_save, sender=Order)
def notify_order_created(sender, instance, created, **kwargs):
    if not created:
        return
    ref = getattr(instance, "reference", instance.pk)
    customer = getattr(instance, "customer_name", "")
    email = getattr(instance, "email", None)

    subject_admin = f"[FAGNI] Nouvelle commande #{ref}"
    body_admin = f"Commande #{ref} créée. Client: {customer or 'N/A'}"
    _mail_to_admin(subject_admin, body_admin)

    # Mail client si un champ email existe
    if email:
        subject = "Votre commande FAGNI a bien été enregistrée"
        body = f"Bonjour {customer or ''},\n\nVotre commande #{ref} a été reçue. Merci !"
        try:
            send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=True)
        except Exception:
            pass
