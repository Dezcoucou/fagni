from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from .models import Zone, TypeService, Partenaire, Article, Commande


class APIFagniTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.zone = Zone.objects.create(nom="Cocody")
        cls.type_service = TypeService.objects.create(nom="Pressing", prix_unitaire=1000)
        cls.partenaire = Partenaire.objects.create(
            nom="Pressing VIP",
            telephone="0700000000",
            email="vip@example.com",
            zone=cls.zone,
        )
        cls.article = Article.objects.create(
            nom="Chemise",
            prix=1500,
            type_service=cls.type_service,
            partenaire=cls.partenaire,
        )

    # -------- ZONES --------
    def test_liste_zones(self):
        url = reverse("zone-list")
        r = self.client.get(url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(r.json()), 1)

    def test_creer_zone(self):
        url = reverse("zone-list")
        payload = {"nom": "Yopougon"}
        r = self.client.post(url, payload, format="json")
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Zone.objects.filter(nom="Yopougon").exists())

    # -------- TYPE SERVICE --------
    def test_liste_typeservice(self):
        url = reverse("typeservice-list")
        r = self.client.get(url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    # -------- PARTENAIRE --------
    def test_liste_partenaires(self):
        url = reverse("partenaire-list")
        r = self.client.get(url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    # -------- ARTICLE --------
    def test_liste_articles(self):
        url = reverse("article-list")
        r = self.client.get(url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    # -------- COMMANDE --------
    def test_creer_commande(self):
        url = reverse("commande-list")
        payload = {
            "client_nom": "Jean",
            "client_telephone": "0701010101",
            "adresse_livraison": "Angré 8ème tranche",
            "zone": self.zone.id,
            "partenaire": self.partenaire.id,
            "article": self.article.id,
            "statut_livraison": "en_attente",
        }
        r = self.client.post(url, payload, format="json")
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Commande.objects.count(), 1)