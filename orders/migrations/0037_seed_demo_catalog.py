from django.db import migrations
from django.utils.text import slugify

def set_if_has(obj, field, value):
    if hasattr(obj, field):
        setattr(obj, field, value)

def forwards(apps, schema_editor):
    ServiceCategory = apps.get_model("orders", "ServiceCategory")
    ServiceItem = apps.get_model("orders", "ServiceItem")

    categories = [
        "Lavage & Repassage",
        "Repassage seul",
        "Couettes & couvertures",
        "Retouche",
        "Cordonnerie",
    ]

    cat_map = {}
    for name in categories:
        slug = slugify(name)
        obj = ServiceCategory.objects.filter(slug=slug).first()
        if not obj:
            obj = ServiceCategory(slug=slug)
            set_if_has(obj, "name", name)
            set_if_has(obj, "is_active", True)
            obj.save()
        cat_map[name] = obj

    services = [
        ("Lavage & Repassage", "Chemise", 500),
        ("Lavage & Repassage", "Pantalon", 700),
        ("Lavage & Repassage", "T-shirt", 400),
        ("Repassage seul", "Chemise (repassage)", 300),
        ("Repassage seul", "Pantalon (repassage)", 400),
        ("Couettes & couvertures", "Couette 2 places", 2500),
        ("Couettes & couvertures", "Drap", 800),
        ("Retouche", "Ourlet pantalon", 1500),
        ("Retouche", "Changement fermeture", 2500),
        ("Cordonnerie", "Cirage", 500),
    ]

    for cat_name, svc_name, price in services:
        cat = cat_map.get(cat_name)
        if not cat:
            continue

        qs = ServiceItem.objects.filter(name=svc_name, category_id=cat.id)
        if qs.exists():
            continue

        svc = ServiceItem(category=cat)
        set_if_has(svc, "name", svc_name)
        set_if_has(svc, "default_price", price)
        set_if_has(svc, "price", price)  # au cas où le champ s'appelle price
        set_if_has(svc, "is_active", True)
        svc.save()

def backwards(apps, schema_editor):
    # optionnel : on ne supprime pas en rollback
    pass

class Migration(migrations.Migration):
    dependencies = [
        # ⚠️ Laisse la dépendance auto générée par Django ici
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
