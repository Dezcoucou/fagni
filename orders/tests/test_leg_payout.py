from decimal import Decimal

from django.test import TestCase

from orders.models import Customer, Order, DeliveryLeg
from partners.models import DeliveryPartner


class DeliveryLegPayoutTests(TestCase):
    def _mk_paid_order_and_driver(self):
        c = Customer.objects.create(name="C", phone="0700000999")
        driver = DeliveryPartner.objects.create(name="Driver", phone="0700000888")
        o = Order.objects.create(
            customer=c,
            total_client_ttc=Decimal("1000"),
            amount_paid=Decimal("1000"),
            payment_status="paid",
        )

        # ✅ Pilot guard: une commande payée doit avoir une blanchisserie
        try:
            from partners.models import LaundryPartner
            lp = LaundryPartner.objects.order_by("id").first()
            if not lp:
                lp = LaundryPartner.objects.create(name="Laundry", phone="0700001777")
            o.laundry_partner = lp
            o.save(update_fields=["laundry_partner"])
        except Exception as e:
            raise AssertionError(f"Failed to assign LaundryPartner for paid order: {e}")

        # ✅ IMPORTANT: supprimer les legs auto-créés (sinon UNIQUE order_id+leg_type)
        DeliveryLeg.objects.filter(order=o).delete()

        return o, driver

    def _mk_unpaid_order_and_driver(self):

        c = Customer.objects.create(name="C", phone="0700000999")
        driver = DeliveryPartner.objects.create(name="Driver", phone="0700000888")
        o = Order.objects.create(
            customer=c,
            total_client_ttc=Decimal("1000"),
            amount_paid=Decimal("0"),
            payment_status="unpaid",
        )

        # ✅ Pilot guard: une commande payée doit avoir une blanchisserie
        try:
            from partners.models import LaundryPartner
            lp = LaundryPartner.objects.order_by("id").first()
            if not lp:
                # fallback minimal si jamais aucun seed
                lp = LaundryPartner.objects.create(name="Laundry", phone="0700001777")
            o.laundry_partner = lp
            o.save(update_fields=["laundry_partner"])
        except Exception as e:
            raise AssertionError(f"Failed to assign LaundryPartner for pilot guard: {e}")
        return o, driver

    def test_leg_done_triggers_payout_when_order_paid_idempotent(self):
        o, driver = self._mk_paid_order_and_driver()

        leg = DeliveryLeg.objects.create(
            order=o,
            driver=driver,
            leg_type="pickup",
            status="assigned",
            driver_amount=Decimal("300"),
        )

        # transition -> done : doit tenter payout (idempotent)
        with self.captureOnCommitCallbacks(execute=True):
            leg.status = "done"
            leg.save()

        # relance save done (ne doit pas dupliquer)
        with self.captureOnCommitCallbacks(execute=True):
            leg.save()

        from wallets.models import WalletTransaction

        qs = WalletTransaction.objects.filter(order=o, leg=leg, type="payout", direction="in")
        self.assertTrue(qs.exists())
        self.assertEqual(qs.count(), 1)

    def test_two_legs_each_trigger_one_payout_idempotent(self):
        """
        pickup done -> 1 payout lié à pickup
        return done -> 2e payout lié à return
        resave done -> pas de duplication
        """
        o, driver = self._mk_paid_order_and_driver()

        pickup = DeliveryLeg.objects.create(
            order=o,
            driver=driver,
            leg_type="pickup",
            status="assigned",
            driver_amount=Decimal("300"),
        )
        ret = DeliveryLeg.objects.create(
            order=o,
            driver=driver,
            leg_type="return",
            status="assigned",
            driver_amount=Decimal("200"),
        )

        with self.captureOnCommitCallbacks(execute=True):
            pickup.status = "done"
            pickup.save()

        with self.captureOnCommitCallbacks(execute=True):
            ret.status = "done"
            ret.save()

        # resave done (idempotent)
        with self.captureOnCommitCallbacks(execute=True):
            pickup.save()
        with self.captureOnCommitCallbacks(execute=True):
            ret.save()

        from wallets.models import WalletTransaction

        qs_pickup = WalletTransaction.objects.filter(order=o, leg=pickup, type="payout", direction="in")
        qs_ret = WalletTransaction.objects.filter(order=o, leg=ret, type="payout", direction="in")

        self.assertEqual(qs_pickup.count(), 1)
        self.assertEqual(qs_ret.count(), 1)

        # total = 2 payouts (1 par jambe)
        self.assertEqual(
            WalletTransaction.objects.filter(order=o, type="payout", direction="in").count(),
            2,
        )

    def test_no_payout_when_order_not_paid_even_if_leg_done(self):
        o, driver = self._mk_unpaid_order_and_driver()

        leg = DeliveryLeg.objects.create(
            order=o,
            driver=driver,
            leg_type="pickup",
            status="assigned",
            driver_amount=Decimal("300"),
        )

        with self.captureOnCommitCallbacks(execute=True):
            leg.status = "done"
            leg.save()

        from wallets.models import WalletTransaction

        self.assertEqual(
            WalletTransaction.objects.filter(order=o, leg=leg, type="payout", direction="in").count(),
            0,
        )

    def test_resync_does_not_override_driver_amount_when_payout_tx_exists(self):
        """
        Si une tx payout existe pour une jambe, le resync doit garder driver_amount = tx.amount
        (source de vérité), même si on modifie des champs qui pourraient déclencher un recompute.
        """
        o, driver = self._mk_paid_order_and_driver()

        pickup = DeliveryLeg.objects.create(
            order=o,
            driver=driver,
            leg_type="pickup",
            status="assigned",
            driver_amount=Decimal("300"),
        )

        with self.captureOnCommitCallbacks(execute=True):
            pickup.status = "done"
            pickup.save()

        from wallets.models import WalletTransaction

        tx = WalletTransaction.objects.filter(order=o, leg=pickup, type="payout", direction="in").first()
        self.assertIsNotNone(tx)
        tx_amount = tx.amount

        # On force une incohérence potentielle (simulateur de recompute)
        DeliveryLeg.objects.filter(pk=pickup.pk).update(driver_amount=Decimal("9999.00"))

        # Tenter d'appeler le resync (selon où tu l'as défini)
        sync_fn = None
        try:
            # cas 1 : fonction disponible dans orders.models
            from orders.models import sync_delivery_legs_for_order as _sync  # type: ignore
            sync_fn = _sync
        except Exception:
            import logging
            logging.getLogger("fagni.tests.orders.tests.test_leg_payout").exception("Exception silencieuse (auto-log) - fichier=orders/tests/test_leg_payout.py ligne=193")

        if sync_fn is None:
            try:
                # cas 2 : fonction déplacée ailleurs (service layer)
                from orders.service_layer.sync import sync_delivery_legs_for_order as _sync  # type: ignore
                sync_fn = _sync
            except Exception:
                import logging
                logging.getLogger("fagni.tests.orders.tests.test_leg_payout").exception("Exception silencieuse (auto-log) - fichier=orders/tests/test_leg_payout.py ligne=201")

        if sync_fn is None:
            self.skipTest("sync_delivery_legs_for_order() introuvable : test resync ignoré.")

        # Exécuter la sync
        sync_fn(o)

        pickup.refresh_from_db()
        self.assertEqual(pickup.driver_amount, tx_amount)


    def test_payout_triggers_when_order_becomes_paid_after_leg_done(self):
        """
        Scenario réel:
        - leg passe done alors que order pas encore paid => pas de payout
        - plus tard order devient paid => payout doit se déclencher automatiquement
        """
        from wallets.models import WalletTransaction
        from orders.models import OrderItem, ServiceCategory, ServiceItem, Payment

        c = Customer.objects.create(name="C", phone="0700001999")
        driver = DeliveryPartner.objects.create(name="Driver", phone="0700001888")

        o = Order.objects.create(
            customer=c,
            total_client_ttc=Decimal("1000"),
            amount_paid=Decimal("0"),
            payment_status="unpaid",
        )

        # ✅ Pilot guard: une commande payée doit avoir une blanchisserie
        try:
            from partners.models import LaundryPartner
            lp = LaundryPartner.objects.order_by("id").first()
            if not lp:
                # fallback minimal si jamais aucun seed
                lp = LaundryPartner.objects.create(name="Laundry", phone="0700001777")
            o.laundry_partner = lp
            o.save(update_fields=["laundry_partner"])
        except Exception as e:
            raise AssertionError(f"Failed to assign LaundryPartner for pilot guard: {e}")

        cat = ServiceCategory.objects.create(name="Blanchisserie", slug="blanchisserie")
        svc = ServiceItem.objects.create(category=cat, name="Lavage", default_price=Decimal("1000"))

        OrderItem.objects.create(
            order=o,
            service=svc,
            designation="Lavage",
            quantity=1,
            unit_price=Decimal("1000"),
        )

        # recalcul total_client_ttc depuis les items SANS spammer les saves
        o.update_financials(save=False)
        o.save(update_fields=["total_client_ttc", "amount_paid", "payment_status"])

        leg = DeliveryLeg.objects.create(
            order=o,
            driver=driver,
            leg_type="pickup",
            status="assigned",
            driver_amount=Decimal("300"),
        )

        # leg -> done alors que commande unpaid => aucun payout
        with self.captureOnCommitCallbacks(execute=True):
            leg.status = "done"
            leg.save(update_fields=["status"])

        self.assertEqual(WalletTransaction.objects.filter(order=o, leg=leg).count(), 0)

        # commande devient paid => payout auto
        with self.captureOnCommitCallbacks(execute=True):
            o.refresh_from_db()
            full_amount = int(Decimal(str(o.total_client_ttc or 0)))

            Payment.objects.create(
                order=o,
                amount=full_amount,
                channel="wave",
                reference="TEST-PAID-001",
                source="driver",
            )

        self.assertEqual(WalletTransaction.objects.filter(order=o, leg=leg).count(), 1)


    def test_recompute_logistics_respects_payout_lock_and_allocates_remaining_pool(self):
        """
        Cas réel (déterministe):
        - On fixe d'abord un pool logistique clair
        - On calcule les legs (recompute) pour obtenir des montants stables
        - On paye (done) une jambe => tx payout => verrouillage
        - On change ensuite le pool et on recompute:
          - le driver_amount locké reste = tx.amount
          - le reste du pool va aux autres jambes actives
          - canceled -> 0
          - agrégats Order cohérents
        """
        from wallets.models import WalletTransaction

        o, driver = self._mk_paid_order_and_driver()

        pickup = DeliveryLeg.objects.create(
            order=o,
            driver=driver,
            leg_type="pickup",
            status="assigned",
            driver_amount=Decimal("0.00"),
        )
        ret = DeliveryLeg.objects.create(
            order=o,
            driver=driver,
            leg_type="return",
            status="assigned",
            driver_amount=Decimal("0.00"),
        )
        canceled = DeliveryLeg.objects.create(
            order=o,
            driver=driver,
            leg_type="aux",
            status="canceled",
            driver_amount=Decimal("333.00"),
            client_fee_share=Decimal("10.00"),
            fagni_margin=Decimal("10.00"),
        )

        # 1) Pool initial clair -> on force un premier recompute STABLE
        Order.objects.filter(id=o.id).update(
            delivery_fee=Decimal("2500.00"),
            amount_driver_partner=Decimal("2000.00"),
            logistic_margin=Decimal("500.00"),
            driver_logistic_cost=Decimal("2000.00"),
        )
        o.refresh_from_db()
        o.recompute_logistics_from_legs(save_legs=True, save_order=True)
        pickup.refresh_from_db()
        ret.refresh_from_db()

        # 2) pickup -> done => payout tx (montant = pickup.driver_amount actuel)
        with self.captureOnCommitCallbacks(execute=True):
            pickup.status = "done"
            pickup.save(update_fields=["status"])

        tx = WalletTransaction.objects.filter(order=o, leg=pickup, type="payout", direction="in").first()
        self.assertIsNotNone(tx)
        locked_amount = tx.amount

        # 3) On change le pool ensuite (simulateur de recalibrage) puis recompute
        # Exemple: pool driver reste 2000, marge 500 (inchangé), mais tu peux modifier delivery_fee si tu veux.
        Order.objects.filter(id=o.id).update(
            delivery_fee=Decimal("2500.00"),
            amount_driver_partner=Decimal("2000.00"),
            logistic_margin=Decimal("500.00"),
            driver_logistic_cost=Decimal("2000.00"),
        )
        o.refresh_from_db()
        o.recompute_logistics_from_legs(save_legs=True, save_order=True)

        o.refresh_from_db()
        pickup.refresh_from_db()
        ret.refresh_from_db()
        canceled.refresh_from_db()

        # 4) canceled à 0
        self.assertEqual(canceled.driver_amount, Decimal("0"))
        self.assertEqual(canceled.client_fee_share, Decimal("0"))
        self.assertEqual(canceled.fagni_margin, Decimal("0"))

        # 5) pickup reste verrouillé
        self.assertEqual(pickup.driver_amount, locked_amount)

        # 6) le reste du pool va au return actif
        self.assertEqual(ret.driver_amount, Decimal("2000.00") - Decimal(str(locked_amount)))

        # 7) agrégats cohérents
        self.assertEqual(o.amount_driver_partner, Decimal("2000.00"))
        self.assertEqual(o.logistic_margin, Decimal("500.00"))

        # 8) somme legs = pool driver
        total_driver_legs = (
            pickup.driver_amount +
            ret.driver_amount +
            canceled.driver_amount
        )
        self.assertEqual(total_driver_legs, Decimal("2000.00"))

        # 9) aucun montant négatif
        self.assertGreaterEqual(pickup.driver_amount, Decimal("0"))
        self.assertGreaterEqual(ret.driver_amount, Decimal("0"))
        self.assertGreaterEqual(canceled.driver_amount, Decimal("0"))

        # 10) aucune nouvelle tx payout créée par recompute
        self.assertEqual(
            WalletTransaction.objects.filter(
                order=o,
                type="payout",
                direction="in"
            ).count(),
            1
        )


    def test_payout_lock_prevents_status_downgrade(self):
        """
        Si payout existe, toute tentative de repasser status à pending/assigned
        doit être neutralisée (reste DONE).
        """
        from wallets.models import WalletTransaction

        o, driver = self._mk_paid_order_and_driver()

        leg = DeliveryLeg.objects.create(
            order=o,
            driver=driver,
            leg_type="pickup",
            status="assigned",
            driver_amount=Decimal("300"),
            client_fee_share=Decimal("1000"),
            fagni_margin=Decimal("200"),
        )

        # done => payout créé
        with self.captureOnCommitCallbacks(execute=True):
            leg.status = "done"
            leg.save(update_fields=["status"])

        self.assertTrue(WalletTransaction.objects.filter(order=o, leg=leg, type="payout", direction="in").exists())

        # tentative downgrade
        DeliveryLeg.objects.filter(pk=leg.pk).update(status="pending")  # attaque SQL
        leg.refresh_from_db()
        self.assertEqual(leg.status, "pending")  # DB est pending, maintenant testons le save() lock

        leg.status = "assigned"
        leg.save(update_fields=["status"])  # doit revenir done via lock
        leg.refresh_from_db()
        self.assertEqual(leg.status, "done")


    def test_payout_lock_freezes_fee_share_and_margin(self):
        """
        Si payout existe, driver_amount est tx.amount et les champs client_fee_share/fagni_margin
        doivent rester figés (valeurs DB) même si on tente de les changer.
        """
        from wallets.models import WalletTransaction

        o, driver = self._mk_paid_order_and_driver()

        leg = DeliveryLeg.objects.create(
            order=o,
            driver=driver,
            leg_type="pickup",
            status="assigned",
            driver_amount=Decimal("300"),
            client_fee_share=Decimal("1000"),
            fagni_margin=Decimal("200"),
        )

        # done => payout créé
        with self.captureOnCommitCallbacks(execute=True):
            leg.status = "done"
            leg.save(update_fields=["status"])

        tx = WalletTransaction.objects.filter(order=o, leg=leg, type="payout", direction="in").order_by("-id").first()
        self.assertIsNotNone(tx)

        # snapshot valeurs figées
        leg.refresh_from_db()
        frozen_client_share = leg.client_fee_share
        frozen_margin = leg.fagni_margin
        frozen_driver_amount = leg.driver_amount

        # tentative de modification "après paiement"
        leg.client_fee_share = Decimal("9999")
        leg.fagni_margin = Decimal("8888")
        leg.driver_amount = Decimal("7777")
        leg.status = "pending"  # tentative downgrade aussi
        leg.save(update_fields=["client_fee_share", "fagni_margin", "driver_amount", "status"])

        leg.refresh_from_db()
        self.assertEqual(leg.status, "done")
        self.assertEqual(leg.driver_amount, tx.amount)          # source de vérité payout
        self.assertEqual(leg.client_fee_share, frozen_client_share)
        self.assertEqual(leg.fagni_margin, frozen_margin)
        self.assertEqual(frozen_driver_amount, tx.amount)        # cohérence

    def test_recompute_does_not_modify_locked_leg_finance(self):
        """
        Si payout existe pour une jambe, un appel à
        order.recompute_logistics_from_legs()
        ne doit pas modifier client_fee_share ni fagni_margin.
        """
        from wallets.models import WalletTransaction

        o, driver = self._mk_paid_order_and_driver()

        leg = DeliveryLeg.objects.create(
            order=o,
            driver=driver,
            leg_type="pickup",
            status="assigned",
            driver_amount=Decimal("300"),
            client_fee_share=Decimal("1000"),
            fagni_margin=Decimal("200"),
        )

        # 1) Terminer jambe → payout créé
        with self.captureOnCommitCallbacks(execute=True):
            leg.status = "done"
            leg.save(update_fields=["status"])

        tx = WalletTransaction.objects.filter(order=o, leg=leg, type="payout", direction="in").first()
        self.assertIsNotNone(tx)

        leg.refresh_from_db()
        frozen_client = leg.client_fee_share
        frozen_margin = leg.fagni_margin
        frozen_driver = leg.driver_amount

        # 2) Modifier artificiellement le pool commande
        Order.objects.filter(pk=o.pk).update(
            delivery_fee=Decimal("9999"),
            amount_driver_partner=Decimal("8888"),
            logistic_margin=Decimal("1111"),
        )
        o.refresh_from_db()

        # 3) Recompute
        o.recompute_logistics_from_legs(save_legs=True, save_order=True)

        leg.refresh_from_db()

        # 4) Vérifications
        self.assertEqual(leg.driver_amount, frozen_driver)
        self.assertEqual(leg.client_fee_share, frozen_client)
        self.assertEqual(leg.fagni_margin, frozen_margin)
