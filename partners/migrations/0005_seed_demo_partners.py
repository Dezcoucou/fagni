from django.db import migrations

def set_if_has(obj, field, value):
    if hasattr(obj, field):
        setattr(obj, field, value)

def forwards(apps, schema_editor):
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
    pass

class Migration(migrations.Migration):
    dependencies = [
        # ⚠️ Laisse la dépendance auto générée par Django ici
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
