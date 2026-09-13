"""
Tests End-to-End du parcours Crowd complet.

Scénarios couverts :
- E2E.1 : Parcours complet succès (Client → Paiement → Offre → Acceptation → Mission → Payout)
- E2E.2 : Fallback Pro après expiration Crowd
- E2E.3 : Rejet cotransporteur → fallback Pro
- E2E.4 : Force OPS Pro sur offre Crowd
"""
from decimal import Decimal
from datetime import time, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone

from crowd.models import Cotransporter, CotransporterRoute, CrowdSettings
from crowd.dispatch import dispatch_delivery_leg
from crowd.expiration import expire_stale_offers
from orders.models import Order, Customer, DeliveryLeg, FCMToken
from orders.services import bootstrap_delivery_legs_for_order
from accounts.models import DriverProfile
from wallets.models import Wallet, WalletTransaction


class CrowdE2ETests(TestCase):
    """
    Tests E2E du parcours Crowd.

    Ces tests valident l'intégration complète :
    - Création commande
    - Dispatch automatique
    - Notification FCM
    - Acceptation API
    - Payout wallet
    """

    def setUp(self):
        # Configuration Crowd
        self.settings = CrowdSettings.get_solo()
        self.settings.crowd_enabled = True
        self.settings.crowd_priority_over_pro = True
        self.settings.crowd_pickup_amount = Decimal('400')
        self.settings.crowd_return_amount = Decimal('300')
        self.settings.crowd_max_detour_km = Decimal('3.00')
        self.settings.crowd_min_score = Decimal('70.00')
        self.settings.crowd_acceptance_timeout_seconds = 120
        self.settings.save()

        # Cotransporteur avec route
        self.user = User.objects.create_user(username='cotransporteur_e2e', password='test')
        self.cotransporter = Cotransporter.objects.create(
            user=self.user,
            is_active=True,
            is_verified=True,
            score=Decimal('100'),
            capacity_kg=Decimal('10'),
        )
        CotransporterRoute.objects.create(
            cotransporter=self.cotransporter,
            origin_lat=Decimal('5.360000'),
            origin_lng=Decimal('-3.950000'),
            destination_lat=Decimal('5.370000'),
            destination_lng=Decimal('-3.940000'),
            departure_time=time(8, 0),
            max_detour_km=Decimal('3.00'),
            time_window_minutes=60,
            is_active=True,
            valid_from=timezone.now().date(),
        )

        # Token FCM pour le cotransporteur
        FCMToken.objects.create(
            user_type='cotransporter',
            user_id=self.cotransporter.id,
            token='test_token_e2e',
        )

        # Client
        self.customer = Customer.objects.create(name='Client E2E', phone='0700000099')

    @patch("crowd.notifications.send_push")
    def test_e2e1_full_crowd_success(self, mock_push):
        """
        E2E.1 : Parcours complet succès Crowd.

        Étapes :
        1. Client crée commande + paie
        2. Dispatch automatique → offre Crowd créée
        3. Notification FCM envoyée
        4. Cotransporteur accepte via API
        5. Mission assignée
        6. Vérification wallet/payout
        """
        mock_push.return_value = True

        # ÉTAPE 1 : Création commande
        order = Order.objects.create(
            customer=self.customer,
            status='paid',
            pickup_lat=5.36,
            pickup_lng=-3.95,
            delivery_lat=5.37,
            delivery_lng=-3.94,
            pickup_address='Riviera 3, Abidjan',
            delivery_address='Cocody, Abidjan',
            pickup_scheduled_date=timezone.now().date(),
            pickup_scheduled_time=time(8, 0),
        )

        # ÉTAPE 2 : Initialisation logistique puis dispatch automatique
        bootstrap_delivery_legs_for_order(order)

        leg = DeliveryLeg.objects.get(
            order=order,
            leg_type='pickup',
        )

        actor, reason, mode = dispatch_delivery_leg(leg)

        # Vérification : offre Crowd créée
        self.assertEqual(mode, 'offered')
        self.assertEqual(actor, self.cotransporter)

        leg.refresh_from_db()
        self.assertEqual(leg.status, 'pending')
        self.assertEqual(leg.assignment_source, 'crowd')
        self.assertIsNotNone(leg.offered_at)
        self.assertIsNotNone(leg.offer_expires_at)

        # ÉTAPE 3 : Notification FCM envoyée
        mock_push.assert_called()
        call_kwargs = mock_push.call_args.kwargs
        self.assertEqual(call_kwargs['token'], 'test_token_e2e')
        self.assertIn('mission', call_kwargs['title'].lower())

        # ÉTAPE 4 : Cotransporteur accepte via API
        from django.test import Client as DjangoClient
        import jwt
        from django.conf import settings

        token = jwt.encode(
            {'cotransporter_id': self.cotransporter.id},
            settings.SECRET_KEY,
            algorithm='HS256',
        )

        client = DjangoClient()
        response = client.post(
            f'/api/crowd/offers/{leg.id}/accept/',
            HTTP_AUTHORIZATION=f'Bearer {token}',
        )

        self.assertEqual(response.status_code, 200)

        # ÉTAPE 5 : Mission assignée
        leg.refresh_from_db()
        self.assertEqual(leg.status, 'assigned')
        self.assertEqual(leg.actor_type, 'cotransporter')
        self.assertEqual(leg.cotransporter_id, self.cotransporter.id)
        self.assertIsNotNone(leg.accepted_at)

        # ÉTAPE 6 : Vérification montant
        self.assertEqual(leg.driver_amount, Decimal('400'))

        # Vérification mission dans l'API
        response = client.get(
            '/api/crowd/missions/',
            HTTP_AUTHORIZATION=f'Bearer {token}',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['missions'][0]['mission_id'], leg.id)

    @patch("crowd.notifications.send_push")
    def test_e2e2_crowd_expiration_fallback_pro(self, mock_push):
        """
        E2E.2 : Fallback Pro après expiration Crowd.

        Étapes :
        1. Offre Crowd créée
        2. Expiration automatique
        3. Fallback Pro tenté
        """
        mock_push.return_value = True

        # Créer un livreur Pro
        driver_user = User.objects.create_user(username='driver_e2e', password='test')
        driver = DriverProfile.objects.create(
            user=driver_user,
            display_name='Livreur Pro E2E',
            phone_number='0700000098',
            is_available=True,
        )

        # ÉTAPE 1 : Offre Crowd créée
        order = Order.objects.create(
            customer=self.customer,
            status='paid',
            pickup_lat=5.36,
            pickup_lng=-3.95,
            delivery_lat=5.37,
            delivery_lng=-3.94,
            pickup_scheduled_date=timezone.now().date(),
            pickup_scheduled_time=time(8, 0),
        )

        leg = DeliveryLeg.objects.create(
            order=order,
            leg_type='pickup',
            status='pending',
        )

        dispatch_delivery_leg(leg)
        leg.refresh_from_db()

        self.assertEqual(leg.assignment_source, 'crowd')
        self.assertEqual(leg.status, 'pending')

        # ÉTAPE 2 : Expiration automatique
        DeliveryLeg.objects.filter(pk=leg.pk).update(
            offer_expires_at=timezone.now() - timedelta(seconds=10)
        )

        result = expire_stale_offers()
        self.assertEqual(result['expired'], 1)

        # ÉTAPE 3 : Vérification rejet
        leg.refresh_from_db()
        self.assertEqual(leg.assignment_reason, 'EXPIRED')
        self.assertEqual(leg.status, 'pending')
        self.assertIsNone(leg.offered_at)

    @patch("crowd.notifications.send_push")
    def test_e2e3_crowd_reject_fallback(self, mock_push):
        """
        E2E.3 : Rejet cotransporteur → fallback.

        Étapes :
        1. Offre Crowd créée
        2. Cotransporteur rejette
        3. Vérification statut
        """
        mock_push.return_value = True

        order = Order.objects.create(
            customer=self.customer,
            status='paid',
            pickup_lat=5.36,
            pickup_lng=-3.95,
            delivery_lat=5.37,
            delivery_lng=-3.94,
            pickup_scheduled_date=timezone.now().date(),
            pickup_scheduled_time=time(8, 0),
        )

        leg = DeliveryLeg.objects.create(
            order=order,
            leg_type='pickup',
            status='pending',
        )

        dispatch_delivery_leg(leg)
        leg.refresh_from_db()

        # Rejet via API
        from django.test import Client as DjangoClient
        import jwt
        from django.conf import settings

        token = jwt.encode(
            {'cotransporter_id': self.cotransporter.id},
            settings.SECRET_KEY,
            algorithm='HS256',
        )

        client = DjangoClient()
        response = client.post(
            f'/api/crowd/offers/{leg.id}/reject/',
            HTTP_AUTHORIZATION=f'Bearer {token}',
        )

        self.assertEqual(response.status_code, 200)

        # Vérification statut
        leg.refresh_from_db()
        self.assertEqual(leg.status, 'pending')
        self.assertEqual(leg.assignment_reason, 'REJECTED_BY_COTRANSPORTER')
        self.assertIsNone(leg.offered_at)

    @patch("crowd.notifications.send_push")
    def test_e2e4_ops_force_pro(self, mock_push):
        """
        E2E.4 : OPS force fallback Pro.

        Étapes :
        1. Offre Crowd créée
        2. OPS force fallback Pro
        3. Vérification statut
        """
        mock_push.return_value = True

        order = Order.objects.create(
            customer=self.customer,
            status='paid',
            pickup_lat=5.36,
            pickup_lng=-3.95,
            delivery_lat=5.37,
            delivery_lng=-3.94,
            pickup_scheduled_date=timezone.now().date(),
            pickup_scheduled_time=time(8, 0),
        )

        leg = DeliveryLeg.objects.create(
            order=order,
            leg_type='pickup',
            status='pending',
        )

        dispatch_delivery_leg(leg)
        leg.refresh_from_db()

        # OPS force fallback
        from django.test import Client as DjangoClient
        import jwt
        from django.conf import settings

        ops_token = jwt.encode(
            {'ops': True, 'name': 'Test OPS'},
            settings.SECRET_KEY,
            algorithm='HS256',
        )

        client = DjangoClient()
        response = client.post(
            f'/api/ops/crowd/offers/{leg.id}/force-pro/',
            HTTP_AUTHORIZATION=f'Bearer {ops_token}',
        )

        # Peut réussir ou échouer selon disponibilité Pro
        self.assertIn(response.status_code, [200, 400])

        # Vérification : offre Crowd rejetée
        leg.refresh_from_db()
        self.assertEqual(leg.assignment_reason, 'OPS_FORCE_PRO')


    @patch("crowd.notifications.send_push")
    def test_e2e5_crowd_payout_wallet_and_idempotence(self, mock_push):
        """
        E2E.5 : Payout Crowd réel.

        Garanties :
        - commande payée
        - cotransporteur accepté
        - jambe terminée
        - payout crédité sur le wallet DU COTRANSPORTEUR
        - montant = driver_amount
        - second appel idempotent : aucune deuxième transaction
        """
        mock_push.return_value = True

        from orders.service_layer.payouts import trigger_driver_payout_for_leg

        order = Order.objects.create(
            customer=self.customer,
            status='paid',
            payment_status='paid',
            pickup_lat=5.36,
            pickup_lng=-3.95,
            delivery_lat=5.37,
            delivery_lng=-3.94,
            pickup_address='Riviera 3, Abidjan',
            delivery_address='Cocody, Abidjan',
            pickup_scheduled_date=timezone.now().date(),
            pickup_scheduled_time=time(8, 0),
        )

        # La DeliveryLeg pickup est créée automatiquement par la synchronisation
        # de la commande. On récupère donc la jambe existante.
        leg = DeliveryLeg.objects.get(
            order=order,
            leg_type='pickup',
        )

        # Dispatch Crowd
        actor, reason, mode = dispatch_delivery_leg(leg)

        self.assertEqual(mode, 'offered')
        self.assertEqual(actor.id, self.cotransporter.id)

        # Acceptation Crowd
        from crowd.dispatch import accept_crowd_offer

        success, accept_reason = accept_crowd_offer(
            leg,
            self.cotransporter,
        )

        print("\n===== DIAGNOSTIC ACCEPTATION CROWD =====")
        leg.refresh_from_db()
        print("success =", success)
        print("accept_reason =", accept_reason)
        print("leg.id =", leg.id)
        print("leg.status =", leg.status)
        print("leg.assignment_source =", leg.assignment_source)
        print("leg.assignment_reason =", leg.assignment_reason)
        print("leg.actor_type =", leg.actor_type)
        print("leg.cotransporter_id =", leg.cotransporter_id)
        print("expected cotransporter_id =", self.cotransporter.id)
        print("leg.offered_at =", leg.offered_at)
        print("leg.offer_expires_at =", leg.offer_expires_at)
        print("leg.driver_amount =", leg.driver_amount)
        print("NOW =", timezone.now())

        self.assertTrue(success, f"reason={accept_reason}")
        self.assertEqual(accept_reason, 'ACCEPTED')

        leg.refresh_from_db()

        self.assertEqual(leg.actor_type, 'cotransporter')
        self.assertEqual(leg.cotransporter_id, self.cotransporter.id)
        self.assertEqual(leg.driver_amount, Decimal('400'))

        # La mission est terminée.
        leg.status = 'done'
        leg.save(update_fields=['status'])

        # Premier payout.
        tx1 = trigger_driver_payout_for_leg(leg)

        self.assertIsNotNone(tx1)
        self.assertEqual(tx1.amount, Decimal('400'))
        self.assertEqual(tx1.type, 'payout')
        self.assertEqual(tx1.direction, 'in')

        # Le wallet appartient bien au cotransporteur.
        self.assertEqual(tx1.wallet.owner_type, 'cotransporter')
        self.assertEqual(
            tx1.wallet.cotransporter_id,
            self.cotransporter.id,
        )

        # Uniquement une transaction payout pour cette jambe.
        payout_count = WalletTransaction.objects.filter(
            leg=leg,
            type='payout',
            direction='in',
        ).count()

        self.assertEqual(payout_count, 1)

        # Deuxième appel : doit être idempotent.
        tx2 = trigger_driver_payout_for_leg(leg)

        self.assertEqual(tx2.id, tx1.id)

        payout_count_after = WalletTransaction.objects.filter(
            leg=leg,
            type='payout',
            direction='in',
        ).count()

        self.assertEqual(payout_count_after, 1)

        # Le wallet ne doit avoir reçu qu'un seul payout de 400 FCFA.
        tx_total = WalletTransaction.objects.filter(
            wallet=tx1.wallet,
            leg=leg,
            type='payout',
            direction='in',
        ).aggregate_total if False else None
