from django.db import migrations
from django.contrib.auth import get_user_model

def create_default_users(apps, schema_editor):
    User = get_user_model()
    DeliveryPartner = apps.get_model("partners", "DeliveryPartner")

    # ----- ADMIN -----
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
    admin.set_password(admin_password)
    admin.save()

    # ----- LIVREUR -----
    driver_email = "livreur1@fagni.app"
    driver_password = "FagniDriver2025!"

    driver_user, created = User.objects.get_or_create(
        username=driver_email,
        defaults={"email": driver_email, "is_active": True},
    )
    driver_user.set_password(driver_password)
    driver_user.save()

    # ----- DeliveryPartner -----
    DeliveryPartner.objects.get_or_create(
        email=driver_email,
        defaults={"name": "Livreur Démo FAGNI", "phone": "0700000000"},
    )

def delete_default_users(apps, schema_editor):
    User = get_user_model()
    User.objects.filter(username__in=["admin@fagni.app", "livreur1@fagni.app"]).delete()

class Migration(migrations.Migration):

    dependencies = [
        ("bonuses", "0001_initial"),
        ("partners", "__latest__"),
    ]

    operations = [
        migrations.RunPython(create_default_users, delete_default_users),
    ]
