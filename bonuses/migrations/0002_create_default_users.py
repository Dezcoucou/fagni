from django.db import migrations


def create_default_users(apps, schema_editor):
    # ✅ IMPORTANT: ne pas seed en environnement de test
    from django.conf import settings
    if getattr(settings, "TESTING", False):
        return

    # Récupère le User model de manière sûre en migration
    UserModelLabel = settings.AUTH_USER_MODEL  # ex: "auth.User" ou "accounts.CustomUser"
    app_label, model_name = UserModelLabel.split(".")
    User = apps.get_model(app_label, model_name)

    DeliveryPartner = apps.get_model("partners", "DeliveryPartner")

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
    admin.set_password(admin_password)
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
    driver_user.set_password(driver_password)
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
