import os
import sys
from django.db import migrations

def _running_tests():
    # pytest-django sets PYTEST_CURRENT_TEST; also sys.argv contains 'pytest'
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or any("pytest" in a for a in sys.argv)



def create_default_users(apps, schema_editor):
    if _running_tests():
        return

    # ✅ IMPORTANT: ne pas seed en environnement de test
    from django.conf import settings
    if getattr(settings, "TESTING", False):
        return

    # Hash password compatible même si le User n'a pas set_password()
    from django.contrib.auth.hashers import make_password

    # Récupère le User model de manière sûre en migration
    UserModelLabel = settings.AUTH_USER_MODEL  # ex: "auth.User" ou "accounts.CustomUser"
    app_label, model_name = UserModelLabel.split(".")
    User = apps.get_model(app_label, model_name)

    DeliveryPartner = apps.get_model("partners", "DeliveryPartner")

    def _set_password_compat(user, raw_password: str):
        """
        Compatible avec:
        - Django User (a set_password)
        - Custom user minimal (pas set_password)
        """
        if hasattr(user, "set_password"):
            user.set_password(raw_password)
        else:
            # suppose que le modèle a un champ "password"
            setattr(user, "password", make_password(raw_password))

    # ========= ADMIN =========
    admin_email = "admin@fagni.app"
    admin_password = "AdminFagni2025!"

    admin, created = User.objects.get_or_create(
        username=admin_email,
        defaults={
            "email": admin_email,
            "is_active": True,
            "is_staff": True,
            "is_superuser": True,
        },
    )
    admin.email = admin_email
    admin.is_active = True
    admin.is_staff = True
    admin.is_superuser = True
    _set_password_compat(admin, admin_password)
    admin.save()

    # ========= LIVREUR (USER) =========
    driver_email = "livreur1@fagni.app"
    driver_password = "FagniDriver2025!"

    driver_user, created = User.objects.get_or_create(
        username=driver_email,
        defaults={
            "email": driver_email,
            "is_active": True,
        },
    )
    driver_user.email = driver_email
    driver_user.is_active = True
    _set_password_compat(driver_user, driver_password)
    driver_user.save()

    # ========= DeliveryPartner lié au livreur =========
    DeliveryPartner.objects.get_or_create(
        email=driver_email,
        defaults={
            "name": "Livreur Démo FAGNI",
            "phone": "0700000000",
        },
    )


def delete_default_users(apps, schema_editor):
    from django.conf import settings
    UserModelLabel = settings.AUTH_USER_MODEL
    app_label, model_name = UserModelLabel.split(".")
    User = apps.get_model(app_label, model_name)

    User.objects.filter(
        username__in=["admin@fagni.app", "livreur1@fagni.app"]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("bonuses", "0001_initial"),
        ("partners", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_default_users, delete_default_users),
    ]
