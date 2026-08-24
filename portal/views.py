from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, Http404

from orders.models import (
    Order,
    Customer,
    OrderItem,
    OrderItemPhoto,
    ServiceCategory,
    ServiceItem,
)
from orders.utils import auto_assign_laundry, auto_assign_delivery
from mlm.services import attach_customer_to_sponsor
from services.services import finalize_commercial_order
from services.resolution import ServiceCatalogResolutionError


def public_create_order(request):
    """
    Portail client FAGNI (public, sans login) :
    - Client (nom, téléphone, adresse, lat/lng)
    - Lignes de prestations issues du catalogue
    - Photos multiples par ligne (photos_0, photos_1, ...)
    - Finalisation commerciale V2 (ServiceExecution)
    - Assignation automatique blanchisserie + livreur APRÈS finalisation
    - Calcul des frais de livraison
    - Rattachement éventuel à un code affilié (MLM)
    """

    service_categories = ServiceCategory.objects.all()
    service_items = ServiceItem.objects.select_related("category").all()

    logi = getattr(settings, "FAGNI_LOGISTICS", {})

    context = {
        "service_categories": service_categories,
        "service_items": service_items,
        "error": None,
        # pré-remplissage en cas d'erreur
        "client_phone": request.POST.get("client_phone", "") if request.method == "POST" else "",
        "client_name": request.POST.get("client_name", "") if request.method == "POST" else "",
        "client_address": request.POST.get("client_address", "") if request.method == "POST" else "",
        "client_lat": request.POST.get("client_lat", "") if request.method == "POST" else "",
        "client_lng": request.POST.get("client_lng", "") if request.method == "POST" else "",
        "affiliate_code": request.POST.get("affiliate_code", "")
            if request.method == "POST"
            else (request.GET.get("aff", "") or request.GET.get("ref", "")),
        "GOOGLE_MAPS_API_KEY": getattr(settings, "GOOGLE_MAPS_API_KEY", ""),
        "delivery_min_fee": logi.get("client_min_fee", 1000),
        "delivery_price_per_km": logi.get("client_price_per_km", 150),
        "delivery_fixed_fee": logi.get("client_fixed_fee", 300),
    }

    if request.method == "POST":
        phone = (request.POST.get("client_phone") or "").strip()
        name = (request.POST.get("client_name") or "").strip()
        address = (request.POST.get("client_address") or "").strip()
        lat_raw = (request.POST.get("client_lat") or "").strip()
        lng_raw = (request.POST.get("client_lng") or "").strip()

        affiliate_code = (
            (request.POST.get("affiliate_code") or "").strip()
            or (request.GET.get("aff") or "").strip()
            or (request.GET.get("ref") or "").strip()
        )

        # 1) Validation minimale client
        if not phone or not name:
            context["error"] = "Merci de renseigner au moins le nom et le téléphone."
            return render(request, "portal/create_order.html", context)

        # 2) Création / mise à jour client
        customer, created = Customer.objects.get_or_create(
            phone=phone,
            defaults={
                "name": name,
                "address": address,
            },
        )
        customer.name = name
        customer.address = address

        # lat / lng (facultatif)
        try:
            if lat_raw:
                customer.latitude = Decimal(lat_raw)
            if lng_raw:
                customer.longitude = Decimal(lng_raw)
        except Exception:
            customer.latitude = None
            customer.longitude = None

        customer.save()

        # 3) Rattachement MLM éventuel
        if affiliate_code:
            attach_customer_to_sponsor(customer, affiliate_code)

        # 4) Collecte des lignes de commande AVANT transaction
        service_ids = request.POST.getlist("service_id[]")
        designations = request.POST.getlist("designation[]")
        quantities = request.POST.getlist("quantity[]")
        unit_prices = request.POST.getlist("unit_price[]")

        items_data = []
        photos_by_idx = {}

        for idx, (sid, desc, qty_str, pu_str) in enumerate(
            zip(service_ids, designations, quantities, unit_prices)
        ):
            # =====================================================
            # SOURCE DE VÉRITÉ COMMERCIALE V2
            # =====================================================
            # Le navigateur peut afficher le prix du catalogue,
            # mais il ne doit jamais pouvoir le fixer.
            #
            # La source de vérité est exclusivement :
            # ServiceItem.default_price côté serveur.
            # =====================================================

            try:
                service_obj = ServiceItem.objects.select_related(
                    "category"
                ).get(
                    pk=int(sid),
                    is_active=True,
                )
            except (
                ServiceItem.DoesNotExist,
                ValueError,
                TypeError,
            ):
                context["error"] = (
                    "Une prestation sélectionnée n'est plus disponible."
                )
                return render(
                    request,
                    "portal/create_order.html",
                    context,
                )

            try:
                qty = int(qty_str or "0")
            except (TypeError, ValueError):
                qty = 0

            if qty <= 0:
                continue

            # IMPORTANT :
            # pu_raw / unit_price[] provenant du navigateur est
            # volontairement ignoré.
            pu = service_obj.default_price

            if pu is None or pu <= 0:
                context["error"] = (
                    f"La prestation « {service_obj.name} » "
                    "n'a pas de prix catalogue valide."
                )
                return render(
                    request,
                    "portal/create_order.html",
                    context,
                )

            # La désignation commerciale provient également du catalogue.
            designation = service_obj.name

            items_data.append({
                "service": service_obj,
                "designation": designation,
                "quantity": qty,
                "unit_price": pu,
            })

            # Photos multiples pour cette ligne.
            photos_field_name = f"photos_{idx}"
            files = request.FILES.getlist(photos_field_name)
            if files:
                photos_by_idx[len(items_data) - 1] = files

        if not items_data:
            context["error"] = (
                "Ajoute au moins une prestation avec quantité et prix unitaire > 0."
            )
            return render(request, "portal/create_order.html", context)

        # 5) Transaction atomique : Order + OrderItem + finalisation V2
        try:
            with transaction.atomic():
                # Créer Order en mode draft
                order = Order.objects.create(
                    customer=customer,
                    status="pending",
                    is_draft=True,
                    pricing_mode="item",
                )

                # Créer les OrderItem
                for idx, item_data in enumerate(items_data):
                    item = OrderItem.objects.create(
                        order=order,
                        service=item_data["service"],
                        designation=item_data["designation"],
                        quantity=item_data["quantity"],
                        unit_price=item_data["unit_price"],
                    )

                    # Créer les photos
                    if idx in photos_by_idx:
                        for f in photos_by_idx[idx]:
                            OrderItemPhoto.objects.create(
                                order_item=item,
                                image=f,
                            )

                # Finalisation commerciale V2
                # Cette étape crée les ServiceExecution et passe is_draft=False
                try:
                    finalize_commercial_order(order=order)
                except ServiceCatalogResolutionError as e:
                    # Rollback complet : aucune commande partiellement créée
                    raise

                # Refresh pour récupérer l'état post-finalisation
                order.refresh_from_db()

                # 6) Assignation blanchisserie + livreur APRÈS finalisation
                laundry_partner = auto_assign_laundry(order)
                if laundry_partner:
                    order.laundry_partner = laundry_partner

                delivery_partner = auto_assign_delivery(order)
                if delivery_partner:
                    order.delivery_partner = delivery_partner

                # 7) Modèle pressing pilote FAGNI : livraison aller-retour fixe
                if order.laundry_partner:
                    order.delivery_fee = 2000

                # 8) Sauvegarde finale
                order.save()

        except ServiceCatalogResolutionError:
            # Message utilisateur propre, aucune commande en base
            context["error"] = (
                "Cette commande ne peut pas encore être confirmée : "
                "le service sélectionné n'est pas disponible."
            )
            return render(request, "portal/create_order.html", context)

        # Redirection vers page "merci"
        return redirect("portal:public_thanks", order_code=order.code)

    # GET
    return render(request, "portal/create_order.html", context)


def public_thanks(request, order_code: str):
    """
    Page de remerciement après création de commande client.
    On affiche un petit récap, sans infos sensibles.
    """
    order = get_object_or_404(
        Order.objects.select_related("customer"),
        code=order_code,
    )

    context = {
        "order": order,
    }
    return render(request, "portal/thanks.html", context)
