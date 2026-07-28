"""
Test de bout en bout du parcours Routine complet - FAGNI V1 (Lot 6, 28 juillet 2026).
Simule le vrai parcours : diagnostic (frontend, non teste ici) -> essai ->
satisfaction OPS -> proposition -> conversion en abonnement, en verifiant
l'integrite des donnees a chaque etape, pas juste le succes de chaque appel.
"""
import jwt
from decimal import Decimal
from django.conf import settings
from django.test import TestCase

from orders.models import Customer, Order, Abonnement, AbonnementPricingRule, EvenementRoutine


def _headers_ops():
    token = jwt.encode({'ops': True, 'name': 'Test OPS'}, settings.SECRET_KEY, algorithm='HS256')
    return {'HTTP_AUTHORIZATION': f'Bearer {token}'}


class ParcoursRoutineBoutEnBoutTests(TestCase):
    def setUp(self):
        AbonnementPricingRule.objects.create(
            pack="confort", taille_sac="M", prix_hebdomadaire=14800, is_active=True,
        )

    def test_parcours_complet_diagnostic_a_abonnement_actif(self):
        """
        Simule le parcours reel complet d'un client, verifie l'integrite
        des donnees a CHAQUE etape - pas seulement le statut final.
        """
        # ---------- ETAPE 1 : essai (le diagnostic frontend a deja
        # determine routine='duo', pack='confort', taille_sac='M') ----------
        r1 = self.client.post(
            "/api/routine/essai/",
            data={
                "telephone": "0700000800", "nom": "Client Bout En Bout",
                "routine": "duo", "pack": "confort", "taille_sac": "M",
                "adresse": "Riviera 3",
            },
            content_type="application/json",
        )
        self.assertEqual(r1.status_code, 201)
        order_id = r1.json()["order_id"]

        order = Order.objects.get(id=order_id)
        self.assertEqual(order.order_origin, "routine_trial")
        self.assertEqual(order.routine_proposee, "duo")
        self.assertEqual(order.satisfaction_reponse, "pending")
        self.assertEqual(Abonnement.objects.count(), 0)  # aucun abonnement a ce stade

        # ---------- ETAPE 2 : la commande est livree (simulation manuelle,
        # normalement fait par le livreur via l'app driver) ----------
        order.status = "delivered"
        order.save()

        # ---------- ETAPE 3 : OPS confirme la satisfaction ----------
        r2 = self.client.post(
            f"/api/ops/routine-essais/{order_id}/satisfaction/",
            data={"reponse": "positive"}, content_type="application/json", **_headers_ops(),
        )
        self.assertEqual(r2.status_code, 200)

        order.refresh_from_db()
        self.assertEqual(order.satisfaction_reponse, "positive")
        self.assertIsNotNone(order.satisfaction_contactee_le)
        self.assertTrue(
            EvenementRoutine.objects.filter(type_evenement="satisfaction_confirmee").exists()
        )

        # ---------- ETAPE 4 : OPS propose l'abonnement ----------
        r3 = self.client.post(
            f"/api/ops/routine-essais/{order_id}/proposer-abonnement/",
            content_type="application/json", **_headers_ops(),
        )
        self.assertEqual(r3.status_code, 200)
        lien = r3.json()["lien_a_partager"]
        self.assertIn(str(order_id), lien)
        self.assertTrue(
            EvenementRoutine.objects.filter(type_evenement="abonnement_propose").exists()
        )

        # ---------- ETAPE 5 : le client consulte le detail de l'essai
        # (ce que fait l'ecran client en arrivant sur le lien) ----------
        r4 = self.client.get(f"/api/routine/essai/{order_id}/")
        self.assertEqual(r4.status_code, 200)
        detail = r4.json()
        self.assertEqual(detail["routine"], "duo")
        self.assertEqual(detail["prix"], 14800.0)
        self.assertEqual(detail["telephone"], "0700000800")

        # ---------- ETAPE 6 : le client confirme la recurrence -
        # conversion reelle en abonnement ----------
        r5 = self.client.post(
            "/api/abonnement/reserver/",
            data={
                "telephone": detail["telephone"], "nom": "Client Bout En Bout",
                "pack": detail["pack"], "taille_sac": detail["taille_sac"],
                "jour_collecte": 0, "jour_livraison": 3,
                "essai_origine": order_id,
            },
            content_type="application/json",
        )
        self.assertEqual(r5.status_code, 200)
        self.assertFalse(r5.json()["already_reserved"])

        # ---------- VERIFICATIONS FINALES : integrite complete de la chaine ----------
        abonnement = Abonnement.objects.get(essai_origine=order)
        self.assertEqual(abonnement.statut, "actif")
        self.assertEqual(abonnement.pack, "confort")
        self.assertEqual(abonnement.taille_sac, "M")
        self.assertEqual(abonnement.prix_verrouille, Decimal("14800.00"))
        self.assertEqual(abonnement.essai_origine_id, order_id)
        self.assertEqual(abonnement.customer.phone, "0700000800")

        self.assertTrue(
            EvenementRoutine.objects.filter(type_evenement="abonnement_active").exists()
        )

        # Un seul abonnement, jamais de doublon meme apres tout ce parcours
        self.assertEqual(Abonnement.objects.filter(customer=abonnement.customer).count(), 1)

    def test_parcours_bloque_si_client_insatisfait(self):
        """L'insatisfaction doit bloquer toute la suite de la chaine, jusqu'a la conversion."""
        r1 = self.client.post(
            "/api/routine/essai/",
            data={
                "telephone": "0700000801", "nom": "Client Insatisfait",
                "routine": "solo", "pack": "confort", "taille_sac": "M",
                "adresse": "Riviera 3",
            },
            content_type="application/json",
        )
        order_id = r1.json()["order_id"]

        order = Order.objects.get(id=order_id)
        order.status = "delivered"
        order.save()

        self.client.post(
            f"/api/ops/routine-essais/{order_id}/satisfaction/",
            data={"reponse": "negative"}, content_type="application/json", **_headers_ops(),
        )

        # La proposition doit etre refusee
        r_propose = self.client.post(
            f"/api/ops/routine-essais/{order_id}/proposer-abonnement/",
            content_type="application/json", **_headers_ops(),
        )
        self.assertEqual(r_propose.status_code, 422)

        # Le detail public doit rester inaccessible
        r_detail = self.client.get(f"/api/routine/essai/{order_id}/")
        self.assertEqual(r_detail.status_code, 422)

        # Aucun abonnement ne doit jamais exister pour ce client
        self.assertEqual(Abonnement.objects.filter(customer__phone="0700000801").count(), 0)
