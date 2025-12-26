from django.db import migrations


def set_if_has(obj, field, value):
    if hasattr(obj, field):
        setattr(obj, field, value)


def forwards(apps, schema_editor):
    # ✅ IMPORTANT: ne pas seed en environnement de test
    from django.conf import settings
    if getattr(settings, "TESTING", False):
        return

    LaundryPartner = apps.get_model("partners", "LaundryPartner")
    DeliveryPartner = apps.get_model("partners", "DeliveryPartner")

    laundries = [
        ("Pressing Demo Cocody", "Cocody Angré", 5.389, -3.998),
        ("Pressing Demo Plateau", "Plateau", 5.323, -4.020),
    ]

    for name, address, lat, lng in laundries:
        obj = LaundryPartner.objects.filter(name=name).first()
        if not obj:
            obj = LaundryPartner()
            set_if_has(obj, "name", name)
            set_if_has(obj, "address", address)
            set_if_has(obj, "is_active", True)
            set_if_has(obj, "latitude", lat)
            set_if_has(obj, "longitude", lng)
            obj.save()

    drivers = [
        ("Livreur Demo 1", "0700000001"),
        ("Livreur Demo 2", "0700000002"),
    ]

    for name, phone in drivers:
        obj = DeliveryPartner.objects.filter(name=name).first()
        if not obj:
            obj = DeliveryPartner()
            set_if_has(obj, "name", name)
            set_if_has(obj, "phone", phone)
            set_if_has(obj, "mobile", phone)
            set_if_has(obj, "is_active", True)
            obj.save()


def backwards(apps, schema_editor):
    # Optionnel: tu peux supprimer les démos ici si tu veux,
    # mais on laisse vide pour éviter tout risque.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("partners", "0004_relaypointpartner_delete_relaypoint"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
