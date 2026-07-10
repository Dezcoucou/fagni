# orders/admin.py
from decimal import Decimal

from django import forms
from django.contrib import admin
from django.contrib.admin import ModelAdmin as UnfoldModelAdmin
from django.contrib import messages
from django.contrib.admin.helpers import ActionForm
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from .models import (
    Customer,
    DeliveryLeg,
    LogisticsConfig,
    Order,
    OrderItem,
    Payment,
    OrderItemPhoto,
    ServiceCategory,
    ServiceItem,
    Subscription,
    SubscriptionCycle,
)
from .config_models import (
    AssignmentSettings,
    GlobalPricingSettings,
    InvoiceSettings,
    WorkflowSettings,
)


from orders.service_layer.payouts import trigger_driver_payout_for_leg
from logistics.orchestrator import run_minimal_v2_flow

try:
    from orders.service_layer.subscriptions import materialize_cycle_to_order, complete_cycle_order_pricing
except ModuleNotFoundError:
    materialize_cycle_to_order = None
    complete_cycle_order_pricing = None


# ============================================================
#  LOGISTICS CONFIG
# ============================================================


@admin.register(LogisticsConfig)
class LogisticsConfigAdmin(UnfoldModelAdmin):
    list_display = ("id",)


# ============================================================
#  INLINES
# ============================================================


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ("service", "designation", "quantity", "unit_price", "total")
    readonly_fields = ("total",)



class DeliveryLegInline(admin.TabularInline):
    model = DeliveryLeg
    extra = 0
    can_delete = False
    fields = (
        "id",
        "leg_type",
        "status",
        "driver",
        "otp_bucket_inline",
        "signature_bucket_inline",
        "started_at",
        "finished_at",
        "created_at",
    )
    readonly_fields = (
        "id",
        "leg_type",
        "status",
        "otp_bucket_inline",
        "signature_bucket_inline",
        "started_at",
        "finished_at",
        "created_at",
    )
    raw_id_fields = ("driver",)

    def otp_bucket_inline(self, obj):
        lt = (getattr(obj, "leg_type", "") or "").lower()
        if lt == "return":
            return "OK" if getattr(obj, "delivery_otp_verified_at", None) else "KO"
        if lt == "pickup":
            return "OK" if getattr(obj, "pickup_otp_verified_at", None) else "KO"
        return "-"

    otp_bucket_inline.short_description = "OTP"

    def signature_bucket_inline(self, obj):
        lt = (getattr(obj, "leg_type", "") or "").lower()
        if lt == "return":
            return "OK" if getattr(obj, "client_signature", None) else "KO"
        if lt == "pickup":
            return "OK" if getattr(obj, "pickup_signature", None) else "KO"
        return "-"

    signature_bucket_inline.short_description = "Signature"

# ============================================================
#  CUSTOM FILTERS
# ============================================================


class PaymentBucketFilter(admin.SimpleListFilter):
    title = "Paiement (bucket)"
    parameter_name = "pay_bucket"

    def lookups(self, request, model_admin):
        return (
            ("paid", "Payées"),
            ("partial", "Partiellement payées"),
            ("unpaid", "Impayées"),
        )

    def queryset(self, request, queryset):
        val = self.value()
        if not val:
            return queryset

        if val == "paid":
            return queryset.filter(payment_status="paid")
        if val == "unpaid":
            return queryset.filter(payment_status="unpaid")
        if val == "partial":
            return queryset.exclude(payment_status="paid").filter(amount_paid__gt=0)
        return queryset


class InvoiceBucketFilter(admin.SimpleListFilter):
    """
    Facture (OK/KO) :
    OK = invoice_number + invoice_date + invoice_status == 'paid'
    KO = il manque au moins un élément
    """

    title = "Facture (bucket)"
    parameter_name = "invoice_bucket"

    def lookups(self, request, model_admin):
        return (
            ("ok", "Facture OK"),
            ("ko", "Facture KO"),
        )

    def queryset(self, request, queryset):
        val = self.value()
        if not val:
            return queryset

        ok_qs = (
            queryset.filter(invoice_number__isnull=False)
            .exclude(invoice_number="")
            .filter(invoice_date__isnull=False, invoice_status="paid")
        )

        if val == "ok":
            return ok_qs
        if val == "ko":
            return queryset.exclude(pk__in=ok_qs.values_list("pk", flat=True))
        return queryset


class WalletsDistributedFilter(admin.SimpleListFilter):
    title = "Wallets distribués"
    parameter_name = "wallets_bucket"

    def lookups(self, request, model_admin):
        return (("yes", "Oui"), ("no", "Non"))

    def queryset(self, request, queryset):
        val = self.value()
        if not val:
            return queryset
        if val == "yes":
            return queryset.filter(wallets_distributed=True)
        if val == "no":
            return queryset.filter(wallets_distributed=False)
        return queryset


class CycleOrderLinkedFilter(admin.SimpleListFilter):
    title = "Commande liée"
    parameter_name = "order_linked"

    def lookups(self, request, model_admin):
        return (("yes", "Oui"), ("no", "Non"))

    def queryset(self, request, queryset):
        val = self.value()
        if not val:
            return queryset
        if val == "yes":
            return queryset.filter(related_order__isnull=False)
        if val == "no":
            return queryset.filter(related_order__isnull=True)
        return queryset


# ============================================================
#  CLIENTS
# ============================================================



def notify_partner_whatsapp_action(modeladmin, request, queryset):
    from django.contrib import messages
    links = []
    for order in queryset:
        notes = order.notes or ""
        for line in notes.split("\n"):
            if line.startswith("WA_PARTNER:"):
                link = line.replace("WA_PARTNER:", "").strip()
                links.append(f"Commande {order.code} : {link}")
    if links:
        messages.info(request, " | ".join(links[:5]))
    else:
        messages.warning(request, "Aucun lien WhatsApp. Assigne une blanchisserie d'abord.")

notify_partner_whatsapp_action.short_description = "Envoyer notification WhatsApp partenaire"

@admin.register(Customer)
class CustomerAdmin(UnfoldModelAdmin):
    list_display  = ("name", "phone", "address", "nb_commandes", "montant_total")
    search_fields = ("name", "phone", "address")
    list_per_page = 50
    fields        = ("name", "phone", "address")  # masque lat/lng inutiles
    save_on_top   = True  # bouton Save aussi en haut du formulaire

    def nb_commandes(self, obj):
        return obj.orders.count()
    nb_commandes.short_description = "Nb commandes"

    def montant_total(self, obj):
        agg = obj.orders.aggregate(total=Sum("total"))
        return f"{agg['total'] or 0:,.0f} FCFA"
    montant_total.short_description = "Montant total"


# ============================================================
#  ABONNEMENTS (PILOTE) — ADMIN
# ============================================================


class SubscriptionActionForm(ActionForm):
    weeks_ahead = forms.ChoiceField(
        label="Semaines à générer",
        choices=[("2", "2"), ("4", "4"), ("6", "6"), ("8", "8"), ("12", "12")],
        initial="6",
        required=True,
    )


class SubscriptionCycleInline(admin.TabularInline):
    model = SubscriptionCycle
    extra = 0
    fields = (
        "week_start",
        "pickup_date",
        "delivery_date",
        "bag_size",
        "status",
        "related_order_link",
        "created_at_display",
    )
    readonly_fields = ("created_at_display", "related_order_link")
    ordering = ("-pickup_date",)

    def related_order_link(self, obj):
        if not obj.related_order_id:
            return "-"
        url = reverse("admin:orders_order_change", args=[obj.related_order_id])
        return format_html('<a href="{}">Order #{}</a>', url, obj.related_order_id)

    def created_at_display(self, obj):
        if not getattr(obj, "created_at", None):
            return "-"
        return timezone.localtime(obj.created_at).strftime("%Y-%m-%d %H:%M")

    created_at_display.short_description = "Créé le"


@admin.register(Subscription)
class SubscriptionAdmin(UnfoldModelAdmin):
    action_form = SubscriptionActionForm

    list_display = (
        "id",
        "customer",
        "pack",
        "bag_size",
        "pickup_weekday",
        "delivery_weekday",
        "start_date",
        "is_active",
        "created_at",
        "updated_at",
    )
    list_filter = (
        "is_active",
        "pack",
        "bag_size",
        "pickup_weekday",
        "delivery_weekday",
        "created_at",
    )
    search_fields = ("customer__name", "customer__phone", "notes")
    list_select_related = ("customer",)
    date_hierarchy = "created_at"
    inlines = [SubscriptionCycleInline]

    actions = ("generate_cycles_param", "generate_cycles_4w", "generate_cycles_8w")

    @admin.action(description="📦 Générer les cycles (paramétrable)")
    def generate_cycles_param(self, request, queryset):
        weeks = int(request.POST.get("weeks_ahead") or 6)
        total_created = 0

        for sub in queryset:
            try:
                total_created += int(sub.generate_cycles(weeks_ahead=weeks, save=True))
            except Exception as e:
                self.message_user(request, f"❌ Abonnement #{sub.pk}: {e}", level=messages.ERROR)

        self.message_user(
            request,
            f"✅ Cycles créés: {total_created} (semaines={weeks})",
            level=messages.SUCCESS,
        )

    @admin.action(description="📦 Générer les cycles (4 semaines)")
    def generate_cycles_4w(self, request, queryset):
        total_created = 0
        for sub in queryset:
            total_created += int(sub.generate_cycles(weeks_ahead=4, save=True))
        self.message_user(request, f"✅ Cycles créés: {total_created}", level=messages.SUCCESS)

    @admin.action(description="📦 Générer les cycles (8 semaines)")
    def generate_cycles_8w(self, request, queryset):
        total_created = 0
        for sub in queryset:
            total_created += int(sub.generate_cycles(weeks_ahead=8, save=True))
        self.message_user(request, f"✅ Cycles créés: {total_created}", level=messages.SUCCESS)


@admin.register(SubscriptionCycle)
class SubscriptionCycleAdmin(UnfoldModelAdmin):
    list_display = ("id", "customer", "pickup_date", "delivery_date", "status", "related_order_link", "created_at")
    list_filter = (CycleOrderLinkedFilter, "status", "pickup_date", "delivery_date")
    search_fields = ("customer__name", "customer__phone")
    actions = ("materialize_to_order", "action_complete_cycles", "action_recalc_cycles")

    def related_order_link(self, obj):
        if not obj.related_order_id:
            return "-"
        url = reverse("admin:orders_order_change", args=[obj.related_order_id])
        return format_html('<a href="{}">Order #{}</a>', url, obj.related_order_id)

    @admin.action(description="🧾 Créer une commande (Order) depuis le(s) cycle(s)")
    def materialize_to_order(self, request, queryset):
        created = 0
        skipped = 0
        errors = 0

        if materialize_cycle_to_order is None:
            self.message_user(
                request,
                "Module subscriptions manquant (orders.service_layer.subscriptions). Action désactivée.",
                level=messages.ERROR,
            )
            return

        qs = queryset.select_related("customer", "subscription", "related_order")
        for cycle in qs:
            try:
                res = materialize_cycle_to_order(cycle)
                if res.skipped:
                    skipped += 1
                elif res.created:
                    created += 1
            except Exception as e:
                errors += 1
                self.message_user(request, f"❌ Cycle #{cycle.pk}: {e}", level=messages.ERROR)

        self.message_user(
            request,
            f"✅ Orders créées: {created} | ignorés (déjà liés): {skipped} | erreurs: {errors}",
            level=messages.SUCCESS if errors == 0 else messages.WARNING,
        )

    @admin.action(description="✅ Compléter la commande (pricing + legs) depuis cycles")
    def action_complete_cycles(self, request, queryset):
        if complete_cycle_order_pricing is None:
            self.message_user(
                request,
                "❌ Module manquant: orders.service_layer.subscriptions (action indisponible).",
                level=messages.ERROR,
            )
            return

        updated = 0
        skipped = 0
        reasons = {}

        for c in queryset:
            r = complete_cycle_order_pricing(c)
            if r.updated:
                updated += 1
            else:
                skipped += 1
                reasons[r.reason] = reasons.get(r.reason, 0) + 1

        msg = f"✅ Complétés: {updated} | ⏭️ Skippés: {skipped}"
        if reasons:
            msg += " | " + ", ".join([f"{k}={v}" for k, v in sorted(reasons.items())])
        self.message_user(request, msg, level=messages.SUCCESS)

    @admin.action(description="🔁 Recalculer pricing + legs (sans toucher aux items)")
    def action_recalc_cycles(self, request, queryset):
        from orders.service_layer.subscriptions import recalc_cycle_order_pricing_and_legs

        updated = 0
        skipped = 0
        reasons = {}

        for c in queryset:
            try:
                r = recalc_cycle_order_pricing_and_legs(c)
                if r.updated:
                    updated += 1
                else:
                    skipped += 1
                    reasons[r.reason] = reasons.get(r.reason, 0) + 1
            except Exception as e:
                skipped += 1
                reasons["exception"] = reasons.get("exception", 0) + 1
                self.message_user(request, f"❌ Cycle #{getattr(c,'pk',None)}: {e}", level=messages.ERROR)

        msg = f"✅ Recalc: {updated} | ⏭️ Skippés: {skipped}"
        if reasons:
            msg += " | " + ", ".join([f"{k}={v}" for k, v in sorted(reasons.items())])
        self.message_user(request, msg, level=messages.SUCCESS)


# ============================================================
#  DELIVERY LEGS (POD/POP / drivers)
# ============================================================

@admin.register(DeliveryLeg)
class DeliveryLegAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "order_link",
        "leg_type",
        "status",
        "driver",
        "started_at",
        "finished_at",
        "otp_bucket",
        "signature_bucket",
        "created_at",
    )
    list_filter = ("leg_type", "status", "created_at")
    search_fields = ("id", "order__id", "order__customer__name", "order__customer__phone", "driver__user__username")
    autocomplete_fields = ("order", "driver")
    ordering = ("-id",)
    list_per_page = 50

    def order_link(self, obj):
        if not getattr(obj, "order_id", None):
            return "-"
        url = reverse("admin:orders_order_change", args=[obj.order_id])
        return format_html('<a href="{}">Order #{}</a>', url, obj.order_id)
    order_link.short_description = "Commande"

    def otp_bucket(self, obj):
        # POD OTP (return) ou POP OTP (pickup)
        if (getattr(obj, "leg_type", "") or "").lower() == "return":
            return "OK" if getattr(obj, "delivery_otp_verified_at", None) else "KO"
        if (getattr(obj, "leg_type", "") or "").lower() == "pickup":
            return "OK" if getattr(obj, "pickup_otp_verified_at", None) else "KO"
        return "-"
    otp_bucket.short_description = "OTP"

    def signature_bucket(self, obj):
        if (getattr(obj, "leg_type", "") or "").lower() == "return":
            return "OK" if getattr(obj, "client_signature", None) else "KO"
        if (getattr(obj, "leg_type", "") or "").lower() == "pickup":
            return "OK" if getattr(obj, "pickup_signature", None) else "KO"
        return "-"
    signature_bucket.short_description = "Signature"


@admin.register(Order)
class OrderAdmin(UnfoldModelAdmin):

    def get_actions(self, request):
        actions = super().get_actions(request)

        # 🔒 CLEAN FIX UNFOLD (corrige crash root cause)
        for name, (func, code, desc) in actions.items():
            if hasattr(func, "attrs"):
                if not isinstance(func.attrs, dict):
                    func.attrs = {}

        return actions

    # 🔒 TEMP FIX UNFOLD — désactive les boutons submit_line qui crashent
    change_form_show_cancel_button = False
    show_full_result_count = False
    def render_change_form(self, request, context, *args, **kwargs):
        # On garde les boutons Django standards via templates/admin/submit_line.html
        context["show_save"] = True
        context["show_save_and_continue"] = True
        context["show_save_and_add_another"] = False
        return super().render_change_form(request, context, *args, **kwargs)

    list_display = (
        "code",
        "customer",
        "pricing_mode_badge",
        "bag_size_display",
        "status",
        "payment_badge",
        "payment_trust_badge",
        "invoice_badge",
        "total_client_ttc_display",
        "amount_paid",
        "amount_due_display",
        "invoice_display",
        "wallets_distributed_display",
        "laundry_partner",
        "delivery_partner",
        "created_at",
        "front_detail_link",
        "customer_wallet_link",
    )

    list_filter = (
        PaymentBucketFilter,
        "payment_status",
        "pricing_mode",
        "status",
        "payment_method",
        InvoiceBucketFilter,
        WalletsDistributedFilter,
        "laundry_partner",
        "delivery_partner",
        "relay_partner",
        "created_at",
    )

    search_fields = (
        "code",
        "invoice_number",
        "payment_reference",
        "customer__name",
        "customer__phone",
    )

    date_hierarchy = "created_at"

    list_select_related = (
        "customer",
        "laundry_partner",
        "delivery_partner",
        "relay_partner",
    )
    inlines = [DeliveryLegInline, OrderItemInline]
    actions = (
        "admin_validate_paid_psp",
        "admin_normalize_invoices_paid",
        "admin_recompute_financials",
        "admin_catchup_wallets_paid",
        "admin_catchup_driver_payout_legs",
        "admin_run_v2_flow",
        "admin_run_v2_flow_with_incident",
        "mark_payment_declared",
        "mark_payment_verified",
        "reject_payment_declared",
    )


    def payment_trust_badge(self, obj):
        try:
            return obj.payment_trust_label()
        except Exception:
            return "🔴 Risque"
    payment_trust_badge.short_description = "Confiance paiement"

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if obj:
            from django.apps import apps

            WalletTransaction = apps.get_model("wallets", "WalletTransaction")
            if WalletTransaction.objects.filter(order_id=obj.pk).exists():
                if "payment_status" not in ro:
                    ro.append("payment_status")
        return ro

    def save_model(self, request, obj, form, change):
        try:
            super().save_model(request, obj, form, change)
        except ValidationError as e:
            self.message_user(request, "❌ " + "; ".join(e.messages), level=messages.ERROR)

    @admin.action(description="Marquer comme paiement déclaré")
    def mark_payment_declared(self, request, queryset):
        updated = 0
        for obj in queryset:
            fields = []

            if getattr(obj, "payment_status", None) != "declared":
                obj.payment_status = "declared"
                fields.append("payment_status")

            if getattr(obj, "payment_verification_status", None) != "pending_review":
                obj.payment_verification_status = "pending_review"
                fields.append("payment_verification_status")

            if not getattr(obj, "payment_declared_at", None):
                obj.payment_declared_at = timezone.now()
                fields.append("payment_declared_at")

            if not getattr(obj, "payment_declared_channel", ""):
                obj.payment_declared_channel = "admin"
                fields.append("payment_declared_channel")

            if fields:
                obj.save(update_fields=list(dict.fromkeys(fields)))
                updated += 1

        self.message_user(request, f"{updated} commande(s) marquée(s) comme paiement déclaré.")

    @admin.action(description="Marquer comme paiement vérifié")
    def mark_payment_verified(self, request, queryset):
        from decimal import Decimal
        from orders.views import build_order_finance_summary, apply_order_payment

        updated = 0

        for obj in queryset:
            try:
                fs = build_order_finance_summary(obj)
                total = Decimal(str(fs.get("total_client_ttc", 0) or 0))
                paid = Decimal(str(getattr(obj, "amount_paid", 0) or 0))
                remaining = total - paid

                if remaining > 0:
                    apply_order_payment(
                        obj,
                        remaining,
                        channel="admin",
                        reference=f"ADMIN-VERIFY-{obj.id}",
                        note="Validation paiement depuis admin",
                    )

                obj.refresh_from_db()

                fields = []

                if getattr(obj, "payment_verification_status", None) != "verified":
                    obj.payment_verification_status = "verified"
                    fields.append("payment_verification_status")

                if not getattr(obj, "payment_declared_at", None):
                    obj.payment_declared_at = timezone.now()
                    fields.append("payment_declared_at")

                obj.payment_verified_at = timezone.now()
                fields.append("payment_verified_at")

                if getattr(obj, "payment_verified_by_id", None) != getattr(request.user, "id", None):
                    obj.payment_verified_by = request.user
                    fields.append("payment_verified_by")

                if fields:
                    obj.save(update_fields=list(dict.fromkeys(fields)))

                updated += 1

            except Exception as e:
                self.message_user(request, f"Erreur commande {getattr(obj, 'id', '?')} : {e}", level=messages.ERROR)

        self.message_user(request, f"{updated} commande(s) marquée(s) comme paiement vérifié.")

    @admin.action(description="Rejeter la déclaration de paiement")
    def reject_payment_declared(self, request, queryset):
        updated = 0

        for obj in queryset:
            fields = []

            if getattr(obj, "payment_verification_status", None) != "rejected":
                obj.payment_verification_status = "rejected"
                fields.append("payment_verification_status")

            if getattr(obj, "payment_status", None) != "paid" and getattr(obj, "payment_status", None) != "pending":
                obj.payment_status = "pending"
                fields.append("payment_status")

            if getattr(obj, "payment_verified_by_id", None):
                obj.payment_verified_by = None
                fields.append("payment_verified_by")

            if getattr(obj, "payment_verified_at", None):
                obj.payment_verified_at = None
                fields.append("payment_verified_at")

            if fields:
                obj.save(update_fields=list(dict.fromkeys(fields)))
                updated += 1

        self.message_user(request, f"{updated} déclaration(s) de paiement rejetée(s).")

    # ============================================================
    #  ACTIONS
    # ============================================================

    @admin.action(description="✅ Valider paiement PSP")
    def admin_validate_paid_psp(self, request, queryset):
        updated = 0
        skipped = 0

        with transaction.atomic():
            for order in queryset.select_for_update():
                # déjà payé
                if order.payment_status == "paid":
                    skipped += 1
                    continue

                # PSP sans référence -> skip
                if not order.payment_reference:
                    skipped += 1
                    continue

                # Recalcul financier AVANT paiement
                try:
                    order.update_financials(save=False)
                except Exception:
                    import logging
                    logging.getLogger("fagni.orders.admin").exception("Exception silencieuse (auto-log) - fichier=orders/admin.py ligne=757")

                paid_at = timezone.now()

                # garder méthode / date / facture
                order.payment_method = "psp"
                order.payment_date = paid_at

                if not getattr(order, "invoice_date", None):
                    order.invoice_date = paid_at
                if not getattr(order, "invoice_status", None) or order.invoice_status in ("draft", "issued"):
                    order.invoice_status = "paid"

                order.save(update_fields=["payment_method", "payment_date", "invoice_date", "invoice_status"])

                # marquer payé
                order.mark_paid(
                    method="psp",
                    reference=order.payment_reference,
                    paid_at=paid_at,
                    save=True,
                )
                updated += 1

        messages.success(request, f"Paiement PSP validé : {updated} commande(s). Ignorées : {skipped}.")

    @admin.action(description="🧾 Normaliser facture sur commandes payées")
    def admin_normalize_invoices_paid(self, request, queryset):
        updated = 0
        skipped = 0

        with transaction.atomic():
            for order in queryset.select_for_update():
                if order.payment_status != "paid":
                    skipped += 1
                    continue

                changed_fields = []

                paid_at = order.payment_date or order.invoice_date or order.updated_at or timezone.now()

                if order.payment_date is None:
                    order.payment_date = paid_at
                    changed_fields.append("payment_date")

                if getattr(order, "invoice_date", None) is None:
                    order.invoice_date = paid_at
                    changed_fields.append("invoice_date")

                inv_status = getattr(order, "invoice_status", None)
                if not inv_status or inv_status in ("draft", "issued"):
                    order.invoice_status = "paid"
                    changed_fields.append("invoice_status")

                # invoice_number : si absent, on laisse update_financials le générer
                if not getattr(order, "invoice_number", None):
                    try:
                        order.update_financials(save=False)
                        changed_fields.append("invoice_number")
                    except Exception:
                        import logging
                        logging.getLogger("fagni.orders.admin").exception("Exception silencieuse (auto-log) - fichier=orders/admin.py ligne=817")

                if changed_fields:
                    order.save(update_fields=list(set(changed_fields)))
                    updated += 1
                else:
                    skipped += 1

        messages.success(request, f"Normalisation factures : {updated} modifiée(s). Ignorées : {skipped}.")

    @admin.action(description="🧾 Rattraper distribution wallets (paid)")
    def admin_catchup_wallets_paid(self, request, queryset):
        updated = 0
        skipped = 0

        with transaction.atomic():
            for order in queryset.select_for_update():
                if order.payment_status != "paid":
                    skipped += 1
                    continue
                if getattr(order, "wallets_distributed", False):
                    skipped += 1
                    continue

                try:
                    order.mark_as_paid_and_distribute()
                    updated += 1
                except Exception:
                    skipped += 1

        messages.success(request, f"Rattrapage wallets : {updated} OK. Ignorées/erreurs : {skipped}.")

    @admin.action(description="🧮 Recalculer montants financiers")
    def admin_recompute_financials(self, request, queryset):
        updated = 0
        skipped = 0

        with transaction.atomic():
            for order in queryset.select_for_update():
                try:
                    order.update_financials(save=True)
                    updated += 1
                except Exception:
                    skipped += 1

        messages.success(request, f"Recalcul : {updated} OK. Erreurs : {skipped}.")

    @admin.action(description="🚚 Rattraper payout livreur (legs done)")
    def admin_catchup_driver_payout_legs(self, request, queryset):
        updated = 0
        skipped = 0

        with transaction.atomic():
            for order in queryset.select_for_update():
                if order.payment_status != "paid":
                    skipped += 1
                    continue

                try:
                    legs_done = DeliveryLeg.objects.filter(order=order, status="done").select_related("driver")
                    for leg in legs_done:
                        trigger_driver_payout_for_leg(leg)
                    updated += 1
                except Exception:
                    skipped += 1

        messages.success(request, f"Rattrapage payout legs : {updated} OK. Ignorées/erreurs : {skipped}.")


    @admin.action(description="🧪 Exécuter flux V2 minimal")
    def admin_run_v2_flow(self, request, queryset):
        success_count = 0
        error_count = 0

        for order in queryset:
            try:
                result = run_minimal_v2_flow(order=order, create_incident_flag=False)
                mission = result.get("mission")
                partner_job = result.get("partner_job")
                quote = result.get("quote")

                self.message_user(
                    request,
                    (
                        f"✅ Order #{order.pk} → "
                        f"Mission={getattr(mission, 'code', '-')}, "
                        f"PartnerJob={getattr(partner_job, 'code', '-')}, "
                        f"Quote=#{getattr(quote, 'id', '-')}"
                    ),
                    level=messages.SUCCESS,
                )
                success_count += 1
            except Exception as e:
                self.message_user(
                    request,
                    f"❌ Order #{order.pk} : {e}",
                    level=messages.ERROR,
                )
                error_count += 1

        if success_count:
            self.message_user(
                request,
                f"Flux V2 minimal exécuté sur {success_count} commande(s).",
                level=messages.SUCCESS,
            )
        if error_count:
            self.message_user(
                request,
                f"{error_count} commande(s) en erreur pendant le flux V2.",
                level=messages.WARNING,
            )

    @admin.action(description="🧪 Exécuter flux V2 minimal + incident")
    def admin_run_v2_flow_with_incident(self, request, queryset):
        success_count = 0
        error_count = 0

        for order in queryset:
            try:
                result = run_minimal_v2_flow(order=order, create_incident_flag=True)
                mission = result.get("mission")
                partner_job = result.get("partner_job")
                quote = result.get("quote")
                incident = result.get("incident")

                self.message_user(
                    request,
                    (
                        f"✅ Order #{order.pk} → "
                        f"Mission={getattr(mission, 'code', '-')}, "
                        f"PartnerJob={getattr(partner_job, 'code', '-')}, "
                        f"Quote=#{getattr(quote, 'id', '-')}, "
                        f"Incident=#{getattr(incident, 'id', '-')}"
                    ),
                    level=messages.SUCCESS,
                )
                success_count += 1
            except Exception as e:
                self.message_user(
                    request,
                    f"❌ Order #{order.pk} : {e}",
                    level=messages.ERROR,
                )
                error_count += 1

        if success_count:
            self.message_user(
                request,
                f"Flux V2 + incident exécuté sur {success_count} commande(s).",
                level=messages.SUCCESS,
            )
        if error_count:
            self.message_user(
                request,
                f"{error_count} commande(s) en erreur pendant le flux V2 + incident.",
                level=messages.WARNING,
            )


    # ============================================================
    #  READONLY / FORM
    # ============================================================

    readonly_fields = (
        "created_at",
        "updated_at",
        # timeline
        "pickup_time",
        "dropoff_time",
        "wash_complete_time",
        "return_time",
        "delivered_time",
        # montants
        "total",
        "service_fee",
        "distance_km",
        "delivery_fee",
        "driver_logistic_cost",
        "logistic_margin",
        # TVA / revenue
        "vat_rate",
        "vat_base",
        "vat_fagni",
        "fagni_revenue_ht",
        # total client
        "total_client_ttc",
        # distribution
        "wallets_distributed",
        "mlm_distributed",
        # blocs custom
        "mlm_info_display",
        "wallet_info_display",
    )

    autocomplete_fields = (
        "customer",
        "laundry_partner",
        "delivery_partner",
        "relay_partner",
    )

    fieldsets = (
        (
            "Informations générales",
            {
                "fields": (
                    "code",
                    "status",
                    "pricing_mode",
                    "bag_size",
                    "customer",
                    "notes",
                    "created_at",
                    "updated_at",
                )
            },
        ),
        (
            "Adresses & géolocalisation",
            {
                "fields": (
                    "pickup_address",
                    ("pickup_lat", "pickup_lng"),
                    "delivery_address",
                    ("delivery_lat", "delivery_lng"),
                )
            },
        ),
        (
            "Partenaires",
            {"fields": ("laundry_partner", "delivery_partner", "relay_partner")},
        ),
        (
            "Paiement & facturation client",
            {
                "fields": (
                    "payment_status",
                    "payment_method",
                    "payment_reference",
                    "payment_date",
                    "amount_paid",
                    "invoice_number",
                    "invoice_date",
                    "invoice_status",
                )
            },
        ),
        (
            "Totaux client (calculés)",
            {"fields": ("total_client_ttc", "total", "service_fee")},
        ),
        (
            "Distribution (wallets / MLM)",
            {
                "fields": (
                    "wallets_distributed",
                    "mlm_distributed",
                    "mlm_info_display",
                    "wallet_info_display",
                )
            },
        ),
    )

    # ============================================================
    #  BADGES / HELPERS LISTE
    # ============================================================

    def payment_badge(self, obj):
        st = (obj.payment_status or "").lower()
        label = obj.get_payment_status_display()
        if st == "paid":
            return format_html("<b style='color:#16a34a'>✅ {}</b>", label)
        if st in ("partial", "partially_paid"):
            return format_html("<b style='color:#f59e0b'>🟠 {}</b>", label)
        return format_html("<b style='color:#dc2626'>❌ {}</b>", label)

    payment_badge.short_description = "Paiement"

    def invoice_badge(self, obj):
        inv_no = getattr(obj, "invoice_number", None)
        inv_date = getattr(obj, "invoice_date", None)
        inv_status = getattr(obj, "invoice_status", None)

        ok = bool(inv_no) and (inv_date is not None) and (inv_status == "paid")
        if ok:
            return format_html("<b style='color:#16a34a'>✅ OK</b>")

        # pas payé -> neutre
        if (obj.payment_status or "").lower() != "paid":
            return format_html("<span style='color:#6b7280'>—</span>")

        return format_html("<b style='color:#f59e0b'>⚠️ KO</b>")

    invoice_badge.short_description = "Facture OK ?"

    def total_client_ttc_display(self, obj):
        return obj.total_client_ttc or obj.total or Decimal("0")

    total_client_ttc_display.short_description = "Total client TTC"

    def amount_due_display(self, obj):
        total_ttc = obj.total_client_ttc or obj.total or Decimal("0")
        paid = obj.amount_paid or Decimal("0")
        due = total_ttc - paid
        return max(due, Decimal("0"))

    amount_due_display.short_description = "Reste à payer"

    def invoice_display(self, obj):
        if not obj.invoice_number:
            return "-"
        return format_html("<code>{}</code>", obj.invoice_number)

    invoice_display.short_description = "N° Facture"

    def wallets_distributed_display(self, obj):
        return (
            format_html("<b style='color:#16a34a'>✅ Oui</b>")
            if getattr(obj, "wallets_distributed", False)
            else format_html("<span style='color:#6b7280'>—</span>")
        )

    wallets_distributed_display.short_description = "Wallets distribués"

    def pricing_mode_badge(self, obj):
        mode = (getattr(obj, "pricing_mode", "") or "").lower()
        if mode == "bag":
            return format_html("<b style='color:#E87722'>🧺 Sac</b>")
        if mode == "item":
            return format_html("<b style='color:#1E3A5F'>🧾 À la pièce</b>")
        return format_html("<span style='color:#6b7280'>—</span>")

    pricing_mode_badge.short_description = "Mode"

    def bag_size_display(self, obj):
        if (getattr(obj, "pricing_mode", "") or "").lower() != "bag":
            return "—"
        size = (getattr(obj, "bag_size", "") or "").lower()
        if size == "small":
            return "Petit sac"
        if size == "large":
            return "Grand sac"
        if size == "medium":
            return "Sac moyen"
        return "—"

    bag_size_display.short_description = "Taille sac"

    def front_detail_link(self, obj):
        try:
            url = reverse("orders:detail", args=[obj.pk])
            return format_html('<a href="{}" target="_blank">🔍 Voir</a>', url)
        except Exception:
            return "-"

    front_detail_link.short_description = "Fiche front"

    def customer_wallet_link(self, obj):
        if not obj.customer:
            return "-"
        try:
            url = reverse("wallets:customer_wallet_detail", args=[obj.customer.pk])
            return format_html('<a href="{}" target="_blank">💰 Wallet</a>', url)
        except Exception:
            return "-"

    customer_wallet_link.short_description = "Wallet client"

    # ============================================================
    #  BLOCS INFOS (MLM / WALLETS) — read-only
    # ============================================================

    def mlm_info_display(self, obj):
        customer = getattr(obj, "customer", None)
        if not customer:
            return "Aucun client associé."

        profiles_manager = getattr(customer, "referral_profiles", None)
        profile = profiles_manager.first() if profiles_manager else None
        if not profile:
            return "Aucun profil de parrainage pour ce client."

        sponsor = getattr(profile, "sponsor", None)
        if sponsor and getattr(sponsor, "customer", None):
            sponsor_txt = f"Parrain : {sponsor.customer.name} ({sponsor.referral_code})"
        else:
            sponsor_txt = "Parrain : aucun parrain enregistré."

        try:
            affiliate_url = reverse("mlm:affiliate_detail", args=[profile.referral_code])
            link = format_html(" – <a href='{}' target='_blank'>Voir fiche affilié MLM</a>", affiliate_url)
        except Exception:
            link = ""

        return format_html("Code affilié client : {} – {}{}", profile.referral_code, sponsor_txt, link)

    mlm_info_display.short_description = "Programme Parrainage (MLM)"

    def wallet_info_display(self, obj):
        from wallets.models import Wallet

        customer = getattr(obj, "customer", None)
        if not customer:
            return "Aucun client associé."

        try:
            wallet = Wallet.objects.get(customer=customer, owner_type="customer")
            solde = wallet.balance
        except Wallet.DoesNotExist:
            wallet = None
            solde = Decimal("0.00")

        if wallet:
            try:
                wallet_url = reverse("wallets:customer_wallet_detail", args=[customer.pk])
                link = format_html(" – <a href='{}' target='_blank'>Voir portefeuille client</a>", wallet_url)
            except Exception:
                link = ""
        else:
            link = " – Aucun portefeuille ouvert pour ce client."

        return format_html("Solde portefeuille client : {} FCFA{}", solde, link)

    wallet_info_display.short_description = "Portefeuille client"


# ============================================================
#  MODELS CONFIG (si tu les exposes dans l'admin)
# ============================================================


@admin.register(GlobalPricingSettings)
class GlobalPricingSettingsAdmin(UnfoldModelAdmin):
    list_display = ("id",)


@admin.register(WorkflowSettings)
class WorkflowSettingsAdmin(UnfoldModelAdmin):
    list_display = ("id",)


@admin.register(AssignmentSettings)
class AssignmentSettingsAdmin(UnfoldModelAdmin):
    list_display = ("id",)


@admin.register(InvoiceSettings)
class InvoiceSettingsAdmin(UnfoldModelAdmin):
    list_display = ("id",)


# === Enregistrement catalogue pressing (catégories + articles) ===
from django.contrib import admin
from .models import ServiceCategory, ServiceItem, Coupon, CouponUsage

@admin.register(Coupon)
class CouponAdmin(UnfoldModelAdmin):
    list_display = ("code", "discount_type", "discount_value", "is_active", "usage_count", "max_total_uses", "valid_from", "valid_until")
    list_filter = ("is_active", "discount_type", "first_order_only")
    search_fields = ("code", "description")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)

    def usage_count(self, obj):
        return obj.usages.count()
    usage_count.short_description = "Utilisations"


@admin.register(CouponUsage)
class CouponUsageAdmin(UnfoldModelAdmin):
    list_display = ("coupon", "customer", "order", "discount_amount", "used_at")
    list_filter = ("coupon",)
    search_fields = ("coupon__code", "customer__phone", "customer__name", "order__code")
    ordering = ("-used_at",)
    readonly_fields = ("coupon", "customer", "order", "discount_amount", "used_at")


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(UnfoldModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}

@admin.register(ServiceItem)
class ServiceItemAdmin(UnfoldModelAdmin):
    list_display = ("name", "category", "default_price", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("name", "code", "category__name")
    ordering = ("category__name", "name")


@admin.register(Payment)
class PaymentAdmin(UnfoldModelAdmin):
    list_display = ["order", "amount", "channel", "source", "created_at"]
    list_filter = ["channel", "source"]
    search_fields = ["order__code", "reference"]
    ordering = ["-created_at"]
    readonly_fields = ["order", "amount", "channel", "reference", "source", "created_at"]



# === PAYMENT REVIEW / ADMIN BADGES AUTO PATCH ===
try:
    from decimal import Decimal
    from django.utils.html import format_html
except Exception:
    Decimal = None
    def format_html(s, *args, **kwargs):
        return s.format(*args, **kwargs)

def _fmt_fcfa_admin(value):
    try:
        if Decimal is not None:
            v = Decimal(str(value or 0))
            return f"{int(v):,}".replace(",", " ") + " FCFA"
        return f"{int(value or 0):,}".replace(",", " ") + " FCFA"
    except Exception:
        return "0 FCFA"

def _order_admin_payment_status_badge(self, obj):
    status = (getattr(obj, "payment_status", "") or "").strip().lower()
    labels = {
        "pending": ("En attente", "#FFF7ED", "#C2410C", "#FED7AA"),
        "declared": ("Déclaré", "#EFF6FF", "#1D4ED8", "#BFDBFE"),
        "partial": ("Partiel", "#FEF3C7", "#92400E", "#FDE68A"),
        "paid": ("Payé", "#ECFDF5", "#065F46", "#BBF7D0"),
        "unpaid": ("Non payé", "#F8FAFC", "#475569", "#E2E8F0"),
    }
    label, bg, fg, bd = labels.get(status, (status or "—", "#F8FAFC", "#475569", "#E2E8F0"))
    return format_html(
        '<span style="display:inline-flex;align-items:center;min-height:28px;padding:0 10px;'
        'border-radius:999px;background:{};color:{};border:1px solid {};font-weight:800;font-size:12px;">'
        '{}'
        '</span>',
        bg, fg, bd, label
    )

def _order_admin_payment_verification_badge(self, obj):
    status = (getattr(obj, "payment_verification_status", "") or "").strip().lower()
    labels = {
        "none": ("Aucune", "#F8FAFC", "#475569", "#E2E8F0"),
        "pending_review": ("En revue", "#EFF6FF", "#1D4ED8", "#BFDBFE"),
        "verified": ("Vérifié", "#ECFDF5", "#065F46", "#BBF7D0"),
        "rejected": ("Rejeté", "#FEF2F2", "#991B1B", "#FECACA"),
    }
    label, bg, fg, bd = labels.get(status, (status or "—", "#F8FAFC", "#475569", "#E2E8F0"))
    return format_html(
        '<span style="display:inline-flex;align-items:center;min-height:28px;padding:0 10px;'
        'border-radius:999px;background:{};color:{};border:1px solid {};font-weight:800;font-size:12px;">'
        '{}'
        '</span>',
        bg, fg, bd, label
    )

def _order_admin_amount_remaining(self, obj):
    try:
        total = Decimal(str(getattr(obj, "total_client_ttc", 0) or 0))
        paid = Decimal(str(getattr(obj, "amount_paid", 0) or 0))
        remain = total - paid
        if remain < 0:
            remain = Decimal("0")
        return _fmt_fcfa_admin(remain)
    except Exception:
        return "0 FCFA"

def _order_admin_payment_declared_at(self, obj):
    return getattr(obj, "payment_declared_at", None)

_order_admin_payment_status_badge.short_description = "Statut paiement"
_order_admin_payment_status_badge.admin_order_field = "payment_status"

_order_admin_payment_verification_badge.short_description = "Revue paiement"
_order_admin_payment_verification_badge.admin_order_field = "payment_verification_status"

_order_admin_amount_remaining.short_description = "Reste à payer"

_order_admin_payment_declared_at.short_description = "Déclaré le"
_order_admin_payment_declared_at.admin_order_field = "payment_declared_at"

try:
    if "payment_status_badge" not in OrderAdmin.__dict__:
        OrderAdmin.payment_status_badge = _order_admin_payment_status_badge

    if "payment_verification_badge" not in OrderAdmin.__dict__:
        OrderAdmin.payment_verification_badge = _order_admin_payment_verification_badge

    if "amount_remaining_admin" not in OrderAdmin.__dict__:
        OrderAdmin.amount_remaining_admin = _order_admin_amount_remaining

    if "payment_declared_at_admin" not in OrderAdmin.__dict__:
        OrderAdmin.payment_declared_at_admin = _order_admin_payment_declared_at

    # list_display
    existing = list(getattr(OrderAdmin, "list_display", ()) or ())
    wanted_front = [
        "payment_status_badge",
        "payment_verification_badge",
    ]
    wanted_tail = [
        "amount_remaining_admin",
        "payment_declared_at_admin",
    ]
    new_display = []
    for x in wanted_front + existing + wanted_tail:
        if x and x not in new_display:
            new_display.append(x)
    OrderAdmin.list_display = tuple(new_display)

    # list_filter
    existing_filters = list(getattr(OrderAdmin, "list_filter", ()) or ())
    for x in ["payment_status", "payment_verification_status", "payment_declared_channel"]:
        if x not in existing_filters:
            existing_filters.append(x)
    OrderAdmin.list_filter = tuple(existing_filters)

    # readonly_fields
    existing_ro = list(getattr(OrderAdmin, "readonly_fields", ()) or ())
    for x in [
        "payment_declared_at",
        "payment_declared_channel",
        "payment_declared_reference",
            "payment_proof",
        "payment_verified_at",
        "payment_verified_by",
    ]:
        if x not in existing_ro:
            existing_ro.append(x)
    OrderAdmin.readonly_fields = tuple(existing_ro)

    # fieldsets
    fieldsets = getattr(OrderAdmin, "fieldsets", None)
    if fieldsets:
        has_payment_review = False
        for fs in fieldsets:
            title = ""
            try:
                title = (fs[0] or "").strip().lower()
            except Exception:
                title = ""
            if "paiement" in title or "payment" in title:
                try:
                    fields = list(fs[1].get("fields", ()))
                    for x in (
                        "payment_status",
                        "payment_verification_status",
                        "payment_declared_at",
                        "payment_declared_channel",
                        "payment_declared_reference",
                        "payment_verified_at",
                        "payment_verified_by",
                    ):
                        if x not in fields:
                            fields.append(x)
                    fs[1]["fields"] = tuple(fields)
                    has_payment_review = True
                    break
                except Exception:
                    import logging
                    logging.getLogger("fagni.orders.admin").exception("Exception silencieuse (auto-log) - fichier=orders/admin.py ligne=1454")

        if not has_payment_review:
            fieldsets = list(fieldsets)
            fieldsets.append((
                "Revue paiement",
                {
                    "fields": (
                        "payment_status",
                        "payment_verification_status",
                        "payment_declared_at",
                        "payment_declared_channel",
                        "payment_declared_reference",
                        "payment_verified_at",
                        "payment_verified_by",
                    )
                }
            ))
            OrderAdmin.fieldsets = tuple(fieldsets)

except NameError:
    print("OrderAdmin introuvable dans orders/admin.py")
    raise

from orders.models import PricingConfig

@admin.register(PricingConfig)
class PricingConfigAdmin(admin.ModelAdmin):
    list_display  = ['bag_size', 'label', 'pressing_amount', 'delivery_fee', 'is_active']
    list_editable = ['pressing_amount', 'delivery_fee', 'is_active']
    ordering      = ['pressing_amount']

from orders.models import ArticleConfig

@admin.register(ArticleConfig)
class ArticleConfigAdmin(admin.ModelAdmin):
    list_display  = ['emoji', 'label', 'category', 'slots', 'weight_kg', 'max_quantity', 'sur_devis', 'prix_unitaire', 'is_active', 'sort_order']
    list_editable = ['slots', 'weight_kg', 'max_quantity', 'sur_devis', 'prix_unitaire', 'is_active', 'sort_order']
    list_filter   = ['category', 'is_active']
    search_fields = ['label', 'article_id']
    ordering      = ['category', 'sort_order']
