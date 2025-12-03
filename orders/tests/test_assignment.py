from decimal import Decimal
from django.test import TestCase
from orders.models import Customer, DeliveryPartner, LaundryPartner
from orders.views import assign_best_driver, assign_best_laundry


class TestAssignment(TestCase):
    def setUp(self):
        """Création d'un client fixe pour tous les tests."""
        self.customer = Customer.objects.create(
            name="Client Test",
            phone="0100000000",
            address="Cocody",
            latitude=Decimal("5.3500"),
            longitude=Decimal("-3.9900"),
        )

    def test_assign_best_driver_pick_closest_active(self):
        """Le livreur le plus proche doit être choisi."""
        d1 = DeliveryPartner.objects.create(
            name="Livreur Proche",
            is_active=True,
            latitude=Decimal("5.3510"),
            longitude=Decimal("-3.9910"),
        )
        d2 = DeliveryPartner.objects.create(
            name="Livreur Loin",
            is_active=True,
            latitude=Decimal("5.4000"),
            longitude=Decimal("-3.9500"),
        )

        best = assign_best_driver(self.customer.latitude, self.customer.longitude)
        self.assertIsNotNone(best)
        self.assertEqual(best.id, d1.id)

    def test_assign_best_driver_returns_none_if_no_active(self):
        """Si aucun livreur actif, on doit retourner None."""
        DeliveryPartner.objects.create(
            name="Livreur Inactif",
            is_active=False,
            latitude=Decimal("5.3510"),
            longitude=Decimal("-3.9910"),
        )

        best = assign_best_driver(self.customer.latitude, self.customer.longitude)
        self.assertIsNone(best)

    def test_assign_best_laundry_pick_closest(self):
        """La blanchisserie la plus proche doit être choisie."""
        l1 = LaundryPartner.objects.create(
            name="Blanchisserie Proche",
            is_active=True,
            latitude=Decimal("5.3550"),
            longitude=Decimal("-3.9950"),
        )
        l2 = LaundryPartner.objects.create(
            name="Blanchisserie Lointaine",
            is_active=True,
            latitude=Decimal("5.5000"),
            longitude=Decimal("-3.8000"),
        )

        best = assign_best_laundry(self.customer)
        self.assertIsNotNone(best)
        self.assertEqual(best.id, l1.id)

    def test_assign_best_laundry_returns_none_if_no_active(self):
        """Si aucune blanchisserie active, retour None."""
        LaundryPartner.objects.create(
            name="Inactive",
            is_active=False,
            latitude=Decimal("5.3550"),
            longitude=Decimal("-3.9950"),
        )

        best = assign_best_laundry(self.customer)
        self.assertIsNone(best)

