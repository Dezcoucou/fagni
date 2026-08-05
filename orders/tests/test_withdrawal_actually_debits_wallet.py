"""
Audit de stabilite - point d'arret "debit wallet au retrait OPS" :
api_ops_enregistrer_paiement marquait un WithdrawalRequest comme 'paid'
via .update() direct sur le queryset, sans jamais appeler apply_payout() -
le wallet.balance n'etait donc jamais reellement debite, meme si le retrait
apparaissait "paye" cote OPS.
"""
from decimal import Decimal

import jwt
from django.conf import settings
from django.test import TestCase

from partners.models import DeliveryPartner
from wallets.models import Wallet, WithdrawalRequest, WalletTransaction


def _headers_ops():
    token = jwt.encode({'ops': True, 'name': 'Test OPS'}, settings.SECRET_KEY, algorithm='HS256')
    return {'HTTP_AUTHORIZATION': f'Bearer {token}'}


class WithdrawalActuallyDebitsWalletTests(TestCase):
    def setUp(self):
        self.driver = DeliveryPartner.objects.create(name="Livreur Test", phone="0700009900", is_active=True)
        self.wallet = Wallet.objects.create(owner_type='driver', delivery_partner=self.driver, balance=Decimal('5000.00'))
        self.wr = WithdrawalRequest.objects.create(wallet=self.wallet, amount=Decimal('2000.00'), status='pending')

    def _valider(self):
        return self.client.post(
            "/api/ops/paiements/enregistrer/",
            data={
                'partenaire_type': 'livreur',
                'partenaire_id': self.wr.id,
                'note': 'Retrait valide par OPS',
            },
            **_headers_ops(),
        )

    def test_validation_ops_debite_reellement_le_wallet(self):
        resp = self._valider()
        self.assertEqual(resp.status_code, 200)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('3000.00'))

        self.wr.refresh_from_db()
        self.assertEqual(self.wr.status, 'paid')
        self.assertIsNotNone(self.wr.processed_at)

    def test_validation_ops_cree_une_wallettransaction_payout(self):
        self._valider()
        tx = WalletTransaction.objects.filter(wallet=self.wallet, type='payout', direction='out').first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.amount, Decimal('2000.00'))

    def test_rejouer_la_validation_ne_debite_pas_une_deuxieme_fois(self):
        self._valider()
        self._valider()

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal('3000.00'))
        self.assertEqual(
            WalletTransaction.objects.filter(wallet=self.wallet, type='payout', direction='out').count(),
            1,
        )
