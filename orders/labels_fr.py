from django.utils.translation import gettext_lazy as _

def _set_verbose(model, singular, plural):
    model._meta.verbose_name = singular
    model._meta.verbose_name_plural = plural

def _field(model, name, label):
    try:
        model._meta.get_field(name).verbose_name = label
    except Exception:
        pass

try:
    from .models import Order, OrderItem, ServiceCategory
except Exception:
    # on charge juste quand les modèles sont prêts
    Order = OrderItem = ServiceCategory = None

if Order:
    _set_verbose(Order, "Commande", "Commandes")
    # champs
    _field(Order, "code", _("Code"))
    _field(Order, "customer", _("Client"))
    _field(Order, "status", _("Statut"))
    _field(Order, "service_type", _("Catégorie de prestation"))
    _field(Order, "total_ht", _("Total HT"))
    _field(Order, "tva", _("TVA"))
    _field(Order, "total_ttc", _("Total TTC"))
    _field(Order, "total_weight_kg", _("Poids total (kg)"))
    _field(Order, "created_at", _("Créé le"))
    _field(Order, "updated_at", _("Modifié le"))

if OrderItem:
    _set_verbose(OrderItem, "Prestation", "Prestations")
    _field(OrderItem, "article", _("Article"))
    _field(OrderItem, "quantity", _("Quantité"))
    _field(OrderItem, "unit_price", _("Prix unitaire"))
    _field(OrderItem, "total_price", _("Total"))

if ServiceCategory:
    _set_verbose(ServiceCategory, "Catégorie de prestation", "Catégories de prestation")
    _field(ServiceCategory, "name", _("Nom"))
    _field(ServiceCategory, "is_active", _("Actif"))
