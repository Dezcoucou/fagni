# seed_data.py
from decimal import Decimal
from api.models import ServiceType, Zone, Partner, Article, Commande, LigneCommande

def get_one_or_create(model, lookup: dict, defaults: dict = None):
    obj = model.objects.filter(**lookup).first()
    if obj:
        return obj
    params = {**lookup, **(defaults or {})}
    return model.objects.create(**params)

print("=== SEED: création de données de démo ===")

# --- Référentiels sans doublons ---
st  = get_one_or_create(ServiceType, {"name":"Pressing"}, {"slug":"pressing"})
z   = get_one_or_create(Zone, {"name":"Plateau"}, {})
p   = get_one_or_create(Partner, {"name":"Fagni Admin"}, {"contact":"0700000000", "zone":z, "service_type":st})

chemise  = get_one_or_create(Article, {"name":"Chemise"},  {"price":Decimal("1000.00"), "service_type":st})
pantalon = get_one_or_create(Article, {"name":"Pantalon"}, {"price":Decimal("2000.00"), "service_type":st})
veste    = get_one_or_create(Article, {"name":"Veste"},    {"price":Decimal("3000.00"), "service_type":st})

# --- COMMANDE #1 ---
c1 = Commande.objects.create(
    partner=p, customer_name="Jean Dupont",
    customer_phone="070000001", address="Abidjan",
    status=Commande.PENDING
)
LigneCommande.objects.create(commande=c1, article=chemise,  quantite=2)  # 2 x 1000
LigneCommande.objects.create(commande=c1, article=pantalon, quantite=1)  # 1 x 2000
c1.refresh_from_db(); print("Commande #1 total =", c1.total)             # 4000.00

# --- COMMANDE #2 ---
c2 = Commande.objects.create(
    partner=p, customer_name="Awa Koné",
    customer_phone="070000002", address="Cocody",
    status=Commande.PROCESSING
)
LigneCommande.objects.create(commande=c2, article=veste,    quantite=1)  # 1 x 3000
LigneCommande.objects.create(commande=c2, article=pantalon, quantite=2)  # 2 x 2000
c2.refresh_from_db(); print("Commande #2 total =", c2.total)             # 7000.00

# --- COMMANDE #3 ---
c3 = Commande.objects.create(
    partner=p, customer_name="Koffi Marc",
    customer_phone="070000003", address="Yopougon",
    status=Commande.READY
)
LigneCommande.objects.create(commande=c3, article=chemise, quantite=3)   # 3 x 1000
c3.refresh_from_db(); print("Commande #3 total =", c3.total)             # 3000.00

ids = list(Commande.objects.values_list("id", flat=True).order_by("-id")[:3])
print("IDs créés (récent → ancien) :", ids)
print("=== SEED OK ===")