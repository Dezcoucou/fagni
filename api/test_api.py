from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from api.models import ServiceType, Zone, Partner, Article, Commande, LigneCommande

User = get_user_model()

class OrdersApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # on se connecte en superuser (SessionAuthentication)
        self.user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="pass1234"
        )
        self.client.force_login(self.user)

        # Données minimales
        self.st = ServiceType.objects.create(name="Pressing", slug="pressing")
        self.zone = Zone.objects.create(name="Cocody")
        self.partner = Partner.objects.create(
            name="Blanchisserie A",
            contact="070000000",
            zone=self.zone,
            service_type=self.st,
            is_active=True,
        )
        self.art_chemise = Article.objects.create(
            name="Chemise", price=Decimal("1000.00"), service_type=self.st, is_active=True
        )
        self.art_pantalon = Article.objects.create(
            name="Pantalon", price=Decimal("2000.00"), service_type=self.st, is_active=True
        )

    def test_create_order_then_add_lines_and_check_total(self):
        # 1) Créer une commande via l'API
        payload = {
            "partner": self.partner.id,
            "customer_name": "Jean API",
            "customer_phone": "070000001",
            "address": "Abidjan",
        }
        r = self.client.post("/api/orders/", payload, format="json")
        self.assertEqual(r.status_code, 201, r.content)

        # l'API doit renvoyer un id
        order_id = r.data.get("id") or r.data.get("pk")
        self.assertTrue(order_id, f"Réponse inattendue: {r.data}")

        # 2) Total initial = 0.00
        r = self.client.get(f"/api/orders/{order_id}/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn("total", r.data)
        self.assertEqual(Decimal(str(r.data["total"])), Decimal("0.00"))

        # 3) Ajouter une ligne: article DOIT être le champ 'article'
        line1 = {
            "commande": order_id,
            "article": self.art_chemise.id,   # <- important: 'article', pas 'article_id'
            "quantite": 3
        }
        r = self.client.post("/api/order-lines/", line1, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data.get("article"), self.art_chemise.id, r.data)

        # 4) Total attendu après ligne 1 = 3000.00
        r = self.client.get(f"/api/orders/{order_id}/")
        self.assertEqual(Decimal(str(r.data["total"])), Decimal("3000.00"))

        # 5) Ajouter une seconde ligne (2 x 2000 = +4000 -> 7000)
        line2 = {
            "commande": order_id,
            "article": self.art_pantalon.id,
            "quantite": 2
        }
        r = self.client.post("/api/order-lines/", line2, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data.get("article"), self.art_pantalon.id, r.data)

        r = self.client.get(f"/api/orders/{order_id}/")
        self.assertEqual(Decimal(str(r.data["total"])), Decimal("7000.00"))

    def test_update_and_delete_line_recomputes_total(self):
        # Créer commande + une ligne (2 x 2000 = 4000)
        cmd = Commande.objects.create(
            partner=self.partner, customer_name="Update Test", customer_phone="070000010"
        )
        lc = LigneCommande.objects.create(commande=cmd, article=self.art_pantalon, quantite=2)
        cmd.refresh_from_db()
        self.assertEqual(cmd.total, Decimal("4000.00"))

        # 1) PATCH (quantité 3 -> 6000)
        payload = {"quantite": 3}
        r = self.client.patch(f"/api/order-lines/{lc.id}/", payload, format="json")
        self.assertIn(r.status_code, (200, 202), r.content)
        cmd.refresh_from_db()
        self.assertEqual(cmd.total, Decimal("6000.00"))

        # 2) DELETE -> total doit passer à 0
        r = self.client.delete(f"/api/order-lines/{lc.id}/")
        self.assertIn(r.status_code, (204, 200), r.content)
        cmd.refresh_from_db()
        self.assertEqual(cmd.total, Decimal("0.00"))