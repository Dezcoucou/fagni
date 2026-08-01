from django.test import TestCase, override_settings
from django.urls import reverse

from orders.models import Customer, PilotWhitelist
from orders.phone_utils import normalize_phone


class NormalizePhoneTests(TestCase):
    """Sprint P0, Wave 1 (BP2) — un numero ne doit jamais creer plusieurs
    identites distinctes selon la forme sous laquelle il est saisi."""

    def test_formes_equivalentes_convergent_vers_le_meme_canonique(self):
        formes = [
            '0700000001',
            '07 00 00 00 01',
            '07-00-00-00-01',
            '2250700000001',
            '+2250700000001',
            '00225 0700000001',
            '225700000001',      # indicatif pays, zero initial omis
        ]
        normalisees = {normalize_phone(f) for f in formes}
        self.assertEqual(normalisees, {'0700000001'})

    def test_vide_ou_none(self):
        self.assertEqual(normalize_phone(''), '')
        self.assertEqual(normalize_phone(None), '')


@override_settings(PILOT_WHITELIST_ENFORCED=True)
class PilotWhitelistGateTests(TestCase):
    """Sprint P0, Wave 1 (BP2) — les 8 scenarios exiges avant fusion."""

    def setUp(self):
        self.login_url = reverse('api-client-login')
        self.register_url = reverse('api-client-register')

    def test_invite_existant_connexion_autorisee(self):
        PilotWhitelist.objects.create(phone_normalized='0700000001')
        Customer.objects.create(name='Aya Kone', phone='0700000001', address='Riviera 3')

        r = self.client.post(self.login_url, {'phone': '0700000001'}, content_type='application/json')

        self.assertEqual(r.status_code, 200)
        self.assertIn('access', r.json())

    def test_invite_nouveau_inscription_autorisee(self):
        PilotWhitelist.objects.create(phone_normalized='0700000009')

        r = self.client.post(
            self.register_url,
            {'phone': '0700000009', 'name': 'Nouveau Participant'},
            content_type='application/json',
        )

        self.assertEqual(r.status_code, 201)
        self.assertIn('access', r.json())
        self.assertTrue(Customer.objects.filter(phone='0700000009').exists())

    def test_numero_absent_connexion_refusee(self):
        Customer.objects.create(name='Non Invite', phone='0700000099', address='x')

        r = self.client.post(self.login_url, {'phone': '0700000099'}, content_type='application/json')

        self.assertEqual(r.status_code, 403)
        self.assertEqual(
            r.json()['error'],
            "Le pilote FAGNI est actuellement accessible uniquement sur invitation. "
            "Contactez notre équipe si vous souhaitez participer.",
        )

    def test_numero_absent_inscription_refusee(self):
        r = self.client.post(
            self.register_url,
            {'phone': '0700000098', 'name': 'Non Invite'},
            content_type='application/json',
        )

        self.assertEqual(r.status_code, 403)
        self.assertEqual(
            r.json()['error'],
            "Le pilote FAGNI est actuellement accessible uniquement sur invitation. "
            "Contactez notre équipe si vous souhaitez participer.",
        )

    def test_numero_revoque_refuse(self):
        PilotWhitelist.objects.create(phone_normalized='0700000002', active=False)
        Customer.objects.create(name='Revoque', phone='0700000002', address='x')

        r = self.client.post(self.login_url, {'phone': '0700000002'}, content_type='application/json')

        self.assertEqual(r.status_code, 403)

    def test_formats_equivalents_reconnus_comme_identiques(self):
        # La whitelist est peuplee sous une forme (ex: saisie internationale
        # par un OPS depuis WhatsApp) differente de celle sous laquelle le
        # client s'est reellement inscrit (forme locale) — les deux doivent
        # etre reconnues comme le meme numero par la whitelist elle-meme.
        PilotWhitelist.objects.create(phone_normalized='+225 07 00 00 00 05')
        Customer.objects.create(name='Format Different', phone='0700000005', address='x')

        r = self.client.post(self.login_url, {'phone': '0700000005'}, content_type='application/json')

        self.assertEqual(r.status_code, 200)
        self.assertEqual(PilotWhitelist.objects.get().phone_normalized, '0700000005')

    def test_aucune_creation_de_customer_apres_un_refus(self):
        count_avant = Customer.objects.count()

        r = self.client.post(
            self.register_url,
            {'phone': '0700000097', 'name': 'Devrait Etre Bloque'},
            content_type='application/json',
        )

        self.assertEqual(r.status_code, 403)
        self.assertEqual(Customer.objects.count(), count_avant)
        self.assertFalse(Customer.objects.filter(phone='0700000097').exists())

    def test_aucun_token_delivre_apres_un_refus(self):
        r = self.client.post(self.login_url, {'phone': '0700000096'}, content_type='application/json')

        self.assertEqual(r.status_code, 403)
        self.assertNotIn('access', r.json())

    def test_participants_existants_selectionnes_acces_preserve_apres_activation(self):
        # Simule la migration sans interruption : le Customer existe deja
        # AVANT que la whitelist et le gate n'existent, exactement comme les
        # vrais participants du pilote aujourd'hui.
        existing = Customer.objects.create(name='Participant Pilote', phone='0700000010', address='Riviera 3')
        PilotWhitelist.objects.create(phone_normalized='0700000010', note='backfill migration sans interruption')

        r = self.client.post(self.login_url, {'phone': '0700000010'}, content_type='application/json')

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['customer']['id'], existing.id)


class PilotWhitelistGateDisabledByDefaultTests(TestCase):
    """Sans override_settings : PILOT_WHITELIST_ENFORCED doit valoir False par
    defaut, pour ne jamais interrompre l'acces existant tant que la liste
    n'a pas ete verifiee et le gate explicitement active."""

    def test_gate_inactif_par_defaut(self):
        from django.conf import settings
        self.assertFalse(settings.PILOT_WHITELIST_ENFORCED)

    def test_numero_non_whitelliste_passe_si_gate_desactive(self):
        Customer.objects.create(name='Client Existant', phone='0700000050', address='x')

        r = self.client.post(
            reverse('api-client-login'), {'phone': '0700000050'}, content_type='application/json',
        )

        self.assertEqual(r.status_code, 200)
