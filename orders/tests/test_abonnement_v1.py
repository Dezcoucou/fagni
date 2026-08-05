"""
Tests des endpoints Abonnement V1 - FAGNI (24 juillet 2026).
"""
from decimal import Decimal
from django.test import TestCase

from orders.client_api import _make_token
from orders.models import Customer, Abonnement, AbonnementPricingRule


def _client_headers(customer):
    return {'HTTP_AUTHORIZATION': f'Bearer {_make_token(customer)}'}


class ApiAbonnementEstimerTests(TestCase):
    def setUp(self):
        AbonnementPricingRule.objects.create(
            pack="confort", taille_sac="M", prix_hebdomadaire=14800, is_active=True,
        )
        AbonnementPricingRule.objects.create(
            pack="essentiel", taille_sac="S", prix_hebdomadaire=7400, is_active=False,
        )

    def test_estimation_reussie(self):
        response = self.client.post(
            "/api/abonnement/estimer/", data={"pack": "confort", "taille_sac": "M"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["disponible"])
        self.assertEqual(data["prix"], 14800.0)

    def test_offre_inactive_refusee(self):
        response = self.client.post(
            "/api/abonnement/estimer/", data={"pack": "essentiel", "taille_sac": "S"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 422)

    def test_offre_non_configuree_503(self):
        response = self.client.post(
            "/api/abonnement/estimer/", data={"pack": "essentiel", "taille_sac": "M"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 503)

    def test_champ_manquant_400(self):
        response = self.client.post(
            "/api/abonnement/estimer/", data={"pack": "confort"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class ApiAbonnementReserverTests(TestCase):
    def setUp(self):
        AbonnementPricingRule.objects.create(
            pack="confort", taille_sac="M", prix_hebdomadaire=14800, is_active=True,
        )

    def _payload(self, **overrides):
        base = {
            "telephone": "0700000100", "nom": "Test Reservation",
            "pack": "confort", "taille_sac": "M", "jour_collecte": 0, "jour_livraison": 3,
        }
        base.update(overrides)
        return base

    def test_reservation_reussie_cree_customer_et_abonnement(self):
        response = self.client.post(
            "/api/abonnement/reserver/", data=self._payload(), content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["already_reserved"])

        self.assertTrue(Customer.objects.filter(phone="0700000100").exists())
        abonnement = Abonnement.objects.get(id=data["abonnement_id"])
        self.assertEqual(abonnement.prix_verrouille, 59200)  # 14800 x 4 (facturation mensuelle)

    def test_reservation_rejouee_ne_cree_pas_de_doublon(self):
        self.client.post("/api/abonnement/reserver/", data=self._payload(), content_type="application/json")
        response = self.client.post("/api/abonnement/reserver/", data=self._payload(), content_type="application/json")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["already_reserved"])
        self.assertEqual(Abonnement.objects.count(), 1)

    def test_prix_verrouille_ne_change_pas_si_tarif_modifie_apres(self):
        """BOS 4.1 - le prix engage ne doit jamais changer retroactivement."""
        self.client.post("/api/abonnement/reserver/", data=self._payload(), content_type="application/json")

        regle = AbonnementPricingRule.objects.get(pack="confort", taille_sac="M")
        regle.prix_hebdomadaire = 99999
        regle.save()

        abonnement = Abonnement.objects.first()
        self.assertEqual(abonnement.prix_verrouille, 59200)  # 14800 x 4 (facturation mensuelle)


class ApiMonAbonnementV1Tests(TestCase):
    def test_telephone_sans_abonnement_retourne_none(self):
        customer = Customer.objects.create(name="Sans Abo", phone="0700000101", address="")
        response = self.client.get(
            "/api/abonnement/mon-abonnement/?telephone=0700000101", **_client_headers(customer),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["abonnement"])

    def test_abonnement_existant_retourne_le_statut(self):
        customer = Customer.objects.create(name="Test", phone="0700000102", address="")
        Abonnement.objects.create(
            customer=customer, pack="confort", taille_sac="M",
            jour_collecte=0, jour_livraison=3, prix_verrouille=14800,
        )
        response = self.client.get(
            "/api/abonnement/mon-abonnement/?telephone=0700000102", **_client_headers(customer),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()["abonnement"]
        self.assertEqual(data["statut"], "Actif")
        self.assertEqual(data["prix"], 14800.0)

    def test_sans_jeton_refuse(self):
        """Preuve du correctif : avant, aucune authentification n'etait requise ici."""
        Customer.objects.create(name="Test", phone="0700000103", address="")
        response = self.client.get("/api/abonnement/mon-abonnement/?telephone=0700000103")
        self.assertEqual(response.status_code, 401)

    def test_jeton_dun_autre_client_refuse(self):
        """Un client authentifie ne doit jamais pouvoir consulter l'abonnement d'un autre."""
        victime = Customer.objects.create(name="Victime", phone="0700000104", address="")
        Abonnement.objects.create(
            customer=victime, pack="confort", taille_sac="M",
            jour_collecte=0, jour_livraison=3, prix_verrouille=14800,
        )
        attaquant = Customer.objects.create(name="Attaquant", phone="0700000105", address="")
        response = self.client.get(
            "/api/abonnement/mon-abonnement/?telephone=0700000104", **_client_headers(attaquant),
        )
        self.assertEqual(response.status_code, 403)


class TailleXLEtFacturationMensuelleTests(TestCase):
    def setUp(self):
        AbonnementPricingRule.objects.create(
            pack="confort", taille_sac="XL", prix_hebdomadaire=22000, is_active=True,
        )

    def test_prix_mensuel_calcule_automatiquement_si_non_configure(self):
        """Secours : prix_mensuel derive de prix_hebdomadaire x4 si jamais configure explicitement."""
        regle = AbonnementPricingRule.objects.get(pack="confort", taille_sac="XL")
        self.assertEqual(regle.prix_mensuel, Decimal("88000.00"))

    def test_prix_mensuel_explicite_jamais_ecrase(self):
        regle = AbonnementPricingRule.objects.create(
            pack="essentiel", taille_sac="S", prix_hebdomadaire=7400,
            prix_mensuel=25000,  # remise commerciale explicite, pas juste x4 (29600)
        )
        self.assertEqual(regle.prix_mensuel, Decimal("25000.00"))

    def test_estimation_taille_xl_reussie(self):
        response = self.client.post(
            "/api/abonnement/estimer/", data={"pack": "confort", "taille_sac": "XL"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["prix"], 22000.0)
        self.assertEqual(data["prix_mensuel"], 88000.0)

    def test_reservation_verrouille_le_prix_mensuel_pas_hebdomadaire(self):
        response = self.client.post(
            "/api/abonnement/reserver/",
            data={
                "telephone": "0700000900", "nom": "Test XL",
                "pack": "confort", "taille_sac": "XL",
                "jour_collecte": 0, "jour_livraison": 3,
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        abonnement = Abonnement.objects.get(customer__phone="0700000900")
        self.assertEqual(abonnement.prix_verrouille, Decimal("88000.00"))
        self.assertEqual(abonnement.taille_sac, "XL")
