"""
Mission BC1 - Diagnostic et reparation complete.

Cause racine reproduite ici : AssignmentSettings (orders/config_models.py),
un reglage independant et anterieur a BC1, deja utilise par les routes OPS
"suggestion" (ops_suggest_pressing/ops_suggest_driver ne le consultent pas,
mais pick_best_laundry/pick_best_driver - le moteur reutilise par BC1 - le
consultent depuis toujours). Si laundry_selection_mode="manual" et/ou
driver_assignment_mode="manual" (etat qui peut dater d'avant l'activation du
flag AUTO_ASSIGN_ON_CLIENT_ORDER, quand toute l'affectation etait manuelle
cote OPS), pick_best_laundry/pick_best_driver renvoient systematiquement
(None, "...manuel...") - AVANT meme de regarder les partenaires actifs, le
GPS ou la charge. C'est la seule condition testee ici qui reproduit a
l'identique le symptome production : pressing ET livreur jamais affectes
simultanement, avec des partenaires actifs et des coordonnees valides,
pendant que l'affectation manuelle OPS (qui ne consulte jamais ce reglage)
continue de fonctionner normalement.

Le correctif livre ici n'est PAS de forcer ce reglage en production (c'est
un choix produit qui appartient au CTO - le repasser sur "auto"/"closest"
change reellement le comportement vecu par de vrais clients/partenaires) :
c'est de rendre cette precondition impossible a manquer, via une commande
non technique (`check_bc1_readiness`) qui donne un verdict clair sans
lecture de log.
"""
import json

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from io import StringIO

from orders.client_api import _make_token
from orders.config_models import AssignmentSettings
from orders.models import Customer, Order
from partners.models import DeliveryPartner, LaundryPartner


RIVIERA_LAT = 5.360
RIVIERA_LNG = -3.950


def _customer(phone="0700009001"):
    return Customer.objects.create(name="Client BC1", phone=phone, address="Riviera 3")


def _payload():
    return {
        'accepted_cgu': '1',
        'bag_size': 'medium',
        'pickup_address': 'Riviera 3',
        'pickup_lat': RIVIERA_LAT,
        'pickup_lng': RIVIERA_LNG,
        'pickup_slot': 'demain_matin',
        'articles': [{'id': 'a1', 'name': 'Chemise', 'quantity': 2}],
    }


def _run_check():
    out = StringIO()
    call_command("check_bc1_readiness", stdout=out)
    return out.getvalue()


class Bc1ReadinessRootCauseTests(TestCase):
    """Reproduit la cause racine confirmee : AssignmentSettings en mode manuel."""

    def _make_active_partners(self):
        LaundryPartner.objects.create(
            name="Pressing 1", phone="0700000001", is_active=True,
            latitude=RIVIERA_LAT, longitude=RIVIERA_LNG,
        )
        LaundryPartner.objects.create(
            name="Pressing 2", phone="0700000002", is_active=True,
            latitude=RIVIERA_LAT, longitude=RIVIERA_LNG,
        )
        for i in range(3):
            DeliveryPartner.objects.create(
                name=f"Livreur {i+1}", phone=f"070000001{i}", is_active=True,
                latitude=RIVIERA_LAT, longitude=RIVIERA_LNG,
            )

    @override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=True)
    def test_mode_manuel_bloque_pressing_et_livreur_malgre_partenaires_actifs_et_gps(self):
        self._make_active_partners()
        cfg = AssignmentSettings.get_solo()
        cfg.driver_assignment_mode = "manual"
        cfg.laundry_selection_mode = "manual"
        cfg.save()

        customer = _customer()
        resp = self.client.post(
            reverse('api-client-create-order'),
            data=json.dumps(_payload()),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {_make_token(customer)}',
        )

        self.assertEqual(resp.status_code, 201, "la commande doit se creer normalement (symptome production)")
        order = Order.objects.get(id=resp.json()['order_id'])
        self.assertIsNone(order.laundry_partner, "aucun pressing affecte : reproduit le symptome production")
        self.assertIsNone(order.pickup_driver, "aucun livreur affecte : reproduit le symptome production")

    @override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=True)
    def test_mode_auto_closest_fonctionne_avec_les_memes_donnees(self):
        """Preuve que le probleme est bien le mode, pas les partenaires/GPS : memes donnees, mode par defaut -> ca marche."""
        self._make_active_partners()
        # AssignmentSettings.get_solo() cree la ligne avec les defauts modele : auto/closest.

        customer = _customer(phone="0700009002")
        resp = self.client.post(
            reverse('api-client-create-order'),
            data=json.dumps(_payload()),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {_make_token(customer)}',
        )

        self.assertEqual(resp.status_code, 201)
        order = Order.objects.get(id=resp.json()['order_id'])
        self.assertIsNotNone(order.laundry_partner)
        self.assertIsNotNone(order.pickup_driver)

    @override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=True)
    def test_affectation_manuelle_ops_continue_de_fonctionner_en_mode_manuel(self):
        """Preuve du garde-fou explicitement demande : le fallback OPS n'est jamais touche par ce reglage."""
        self._make_active_partners()
        cfg = AssignmentSettings.get_solo()
        cfg.driver_assignment_mode = "manual"
        cfg.laundry_selection_mode = "manual"
        cfg.save()

        customer = _customer(phone="0700009003")
        resp = self.client.post(
            reverse('api-client-create-order'),
            data=json.dumps(_payload()),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {_make_token(customer)}',
        )
        order = Order.objects.get(id=resp.json()['order_id'])
        self.assertIsNone(order.laundry_partner)

        partner = LaundryPartner.objects.first()
        token_ops = _make_token_ops()
        assign_resp = self.client.post(
            f"/api/ops/orders/{order.id}/assign/",
            data=json.dumps({'partner_id': partner.id}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {token_ops}',
        )
        self.assertEqual(assign_resp.status_code, 200, "l'affectation manuelle OPS doit continuer de fonctionner")
        order.refresh_from_db()
        self.assertEqual(order.laundry_partner_id, partner.id)


def _make_token_ops():
    import jwt
    from django.conf import settings
    return jwt.encode({'ops': True, 'name': 'Test OPS'}, settings.SECRET_KEY, algorithm='HS256')


class CheckBc1ReadinessCommandTests(TestCase):
    """La commande non technique doit donner le bon verdict, sans qu'on ait besoin de lire un log."""

    @override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=False)
    def test_verdict_bloque_si_flag_desactive(self):
        output = _run_check()
        self.assertIn("DESACTIVE", output)
        self.assertIn("BC1 BLOQUE", output)

    @override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=True)
    def test_verdict_bloque_si_mode_manuel_meme_avec_partenaires_actifs(self):
        LaundryPartner.objects.create(
            name="Pressing 1", phone="0700000001", is_active=True,
            latitude=RIVIERA_LAT, longitude=RIVIERA_LNG,
        )
        DeliveryPartner.objects.create(
            name="Livreur 1", phone="0700000010", is_active=True,
            latitude=RIVIERA_LAT, longitude=RIVIERA_LNG,
        )
        cfg = AssignmentSettings.get_solo()
        cfg.driver_assignment_mode = "manual"
        cfg.laundry_selection_mode = "manual"
        cfg.save()

        output = _run_check()
        self.assertIn("BC1 BLOQUE", output)
        self.assertIn("sélection manuelle forcée", output)
        self.assertIn("assignation manuelle forcée", output)

    @override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=True)
    def test_verdict_pret_si_partenaires_actifs_et_modes_auto(self):
        LaundryPartner.objects.create(
            name="Pressing 1", phone="0700000001", is_active=True,
            latitude=RIVIERA_LAT, longitude=RIVIERA_LNG,
        )
        DeliveryPartner.objects.create(
            name="Livreur 1", phone="0700000010", is_active=True,
            latitude=RIVIERA_LAT, longitude=RIVIERA_LNG,
        )
        output = _run_check()
        self.assertIn("BC1 PRET", output)

    @override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=True)
    def test_verdict_bloque_si_aucun_partenaire_actif(self):
        output = _run_check()
        self.assertIn("BC1 BLOQUE", output)
        self.assertIn("aucun pressing actif", output)
        self.assertIn("aucun livreur actif", output)
