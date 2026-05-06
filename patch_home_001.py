#!/usr/bin/env python3
"""
FAGNI — Patch 001 : Homepage client premium
Scope   : {% block content %} uniquement
Backup  : automatique avant toute écriture
Auteur  : Désiré TONGBÉ
"""
import shutil
import sys
from datetime import datetime
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────────────────────
TEMPLATE = Path("orders/templates/orders/client_home.html")

# ── GARDE-FOU ─────────────────────────────────────────────────────────────────
if not TEMPLATE.exists():
    print(f"❌ Fichier introuvable : {TEMPLATE}")
    sys.exit(1)

# ── 1. BACKUP ─────────────────────────────────────────────────────────────────
stamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
backup = TEMPLATE.with_suffix(f".bak_{stamp}.html")
shutil.copy2(TEMPLATE, backup)
print(f"✅ Backup créé        : {backup}")

# ── 2. LECTURE ────────────────────────────────────────────────────────────────
raw = TEMPLATE.read_text(encoding="utf-8")
print(f"   Taille originale   : {len(raw):,} caractères / {raw.count(chr(10))+1} lignes")

# ── 3. NOUVEAU {% block content %} ───────────────────────────────────────────
# Règles appliquées :
#   • Aucune donnée fictive
#   • Routes uniquement celles confirmées dans urls.py
#   • Champs order uniquement ceux confirmés dans views.py + template actuel
#   • Pressing + Cordonnerie actifs, autres en Bientôt
#   • Numéro WhatsApp : remplace TON_NUMERO par le vrai numéro CI (ex: 2250700000000)

NEW_BLOCK = """\
{% block content %}
<div class="client-home-v2">

{# ══════════════════════════════════════════════════════════════════════════
   HERO — personnalisé avec données réelles
   Suppression de hero-v2--media (évite la dépendance à l'image statique)
   ══════════════════════════════════════════════════════════════════════════ #}
<section class="hero-v2">
  <div class="hero-v2__content">

    <div class="hero-v2__eyebrow">
      {% if customer %}
        Bonjour, {{ customer.name|title }} 👋
      {% else %}
        Bienvenue sur FAGNI
      {% endif %}
    </div>

    <h1 class="hero-v2__title">
      Collecte, traitement
      <span>livraison à domicile</span>
    </h1>

    <p class="hero-v2__text">
      FAGNI prend en charge ton pressing et ta cordonnerie —
      collectés chez toi, traités par nos partenaires vérifiés, livrés propres.
    </p>

    <div class="hero-v2__actions">
      <a href="{% url 'orders:client_new_order' %}" class="btn-main-v2">
        + Nouvelle commande
      </a>
      <a href="#recent-orders" class="btn-alt-v2">Mes commandes</a>
    </div>

    <div class="hero-v2__trust">
      <span>✓ Partenaires vérifiés</span>
      <span>✓ Collecte à domicile</span>
      <span>✓ Paiement Wave sécurisé</span>
    </div>

  </div>
</section>

{# ══════════════════════════════════════════════════════════════════════════
   KPIs — données réelles uniquement (plus de "12 min" ou "4.9/5" fictifs)
   Valeurs : orders_count_all, active_orders_count, wallet_balance
   ══════════════════════════════════════════════════════════════════════════ #}
<section class="kpis-v2">

  <div class="kpi-v2">
    <strong>{{ orders_count_all|default:"0" }}</strong>
    <small>Commande{{ orders_count_all|pluralize }}</small>
  </div>

  <div class="kpi-v2">
    <strong>{{ active_orders_count|default:"0" }}</strong>
    <small>En cours</small>
  </div>

  <div class="kpi-v2">
    {% if wallet_balance %}
      <strong>{{ wallet_balance|floatformat:0 }}</strong>
      <small>FCFA wallet</small>
    {% else %}
      <strong>Wave</strong>
      <small>Paiement sécurisé</small>
    {% endif %}
  </div>

</section>

{# ══════════════════════════════════════════════════════════════════════════
   ALERTE COMMANDES IMPAYÉES — affichée uniquement si unpaid_orders_count > 0
   ══════════════════════════════════════════════════════════════════════════ #}
{% if unpaid_orders_count > 0 %}
<div class="wallet-boost-banner" style="background:#FEF2F2;border-color:#DC2626;">
  ⚠️&nbsp;
  <span>
    Tu as
    <strong style="color:#DC2626;">
      {{ unpaid_orders_count }}
      commande{{ unpaid_orders_count|pluralize }}
      non payée{{ unpaid_orders_count|pluralize }}
    </strong>
    en attente de paiement.
  </span>
  <a href="#recent-orders" class="wallet-boost-btn" style="background:#DC2626;">
    Payer maintenant
  </a>
</div>
{% endif %}

{# ══════════════════════════════════════════════════════════════════════════
   WALLET BOOST — affiché uniquement si solde > 0 (données réelles)
   ══════════════════════════════════════════════════════════════════════════ #}
{% if wallet_balance > 0 %}
<div class="wallet-boost-banner">
  🎉&nbsp;Tu as
  <span class="wallet-boost-amount">{{ wallet_balance|floatformat:0 }} FCFA</span>
  disponible dans ton wallet !
  <a href="{% url 'orders:client_new_order' %}" class="wallet-boost-btn">
    Utiliser maintenant
  </a>
</div>
{% endif %}

{# ══════════════════════════════════════════════════════════════════════════
   ACTIONS RAPIDES
   Remplacement de "Commander vite" (doublon) par "Support WhatsApp"
   ══════════════════════════════════════════════════════════════════════════ #}
<section class="quick-actions-v2">
  <div class="section-head-v2">
    <div>
      <div class="section-kicker-v2">Actions rapides</div>
      <h2>Accède à l'essentiel</h2>
    </div>
  </div>

  <div class="quick-grid-v2">

    <a href="{% url 'orders:client_new_order' %}" class="quick-card-v2">
      <div class="quick-card-v2__icon">＋</div>
      <div>
        <strong>Nouvelle commande</strong>
        <p>Créer une nouvelle demande rapidement</p>
      </div>
    </a>

    <a href="#recent-orders" class="quick-card-v2">
      <div class="quick-card-v2__icon">📦</div>
      <div>
        <strong>Mes commandes</strong>
        <p>
          {% if orders_count_all %}
            {{ orders_count_all }} commande{{ orders_count_all|pluralize }} au total
          {% else %}
            Voir l'état et les détails
          {% endif %}
        </p>
      </div>
    </a>

    <a href="{% url 'orders:client_wallet' %}" class="quick-card-v2">
      <div class="quick-card-v2__icon">💼</div>
      <div>
        <strong>Mon wallet</strong>
        <p>
          {% if wallet_balance %}
            {{ wallet_balance|floatformat:0 }} FCFA disponibles
          {% else %}
            Suivre paiements et mouvements
          {% endif %}
        </p>
      </div>
    </a>

    {# Support WhatsApp — remplace TON_NUMERO par le vrai numéro CI ex: 2250700000000 #}
    <a href="https://wa.me/TON_NUMERO" target="_blank" rel="noopener" class="quick-card-v2">
      <div class="quick-card-v2__icon">💬</div>
      <div>
        <strong>Support WhatsApp</strong>
        <p>Un problème ? On répond rapidement</p>
      </div>
    </a>

  </div>
</section>

{# ══════════════════════════════════════════════════════════════════════════
   COMMANDES RÉCENTES — données réelles, logique inchangée
   Ajout : pickup_address_short (annoté dans la vue)
   Ajout : couleur inline sur statut paiement
   ══════════════════════════════════════════════════════════════════════════ #}
<section class="recent-orders-v2" id="recent-orders">
  <div class="section-head-v2">
    <div>
      <div class="section-kicker-v2">Commandes</div>
      <h2>Mes commandes récentes</h2>
    </div>
  </div>

  <div class="orders-stack-v2">
    {% for order in orders %}
    <div class="order-card-v2">

      <div class="order-header-v2">
        <div class="order-main-v2">
          <strong class="order-code-v2">{{ order.code|default:order.id }}</strong>
          <div class="order-date-v2">
            {% if order.created_at %}
              {{ order.created_at|date:"d/m/Y H:i" }}
            {% else %}
              —
            {% endif %}
          </div>
        </div>
        <div class="order-status-wrap-v2">
          <span class="status-badge-v2
            {% if order.status == 'completed' or order.status == 'ready' %}ready
            {% elif order.status == 'pending' or order.status == 'draft' %}pending
            {% else %}progress{% endif %}">
            {% if order.status == 'completed' or order.status == 'ready' %}
              Prêt
            {% elif order.status == 'pending' or order.status == 'draft' %}
              En attente
            {% else %}
              En cours
            {% endif %}
          </span>
        </div>
      </div>

      <div class="order-body-v2">
        <div class="order-col-v2">
          <div class="label-v2">Service</div>
          <div class="value-v2">
            {{ order.service_type|default:"Commande FAGNI"|capfirst }}
          </div>
        </div>

        <div class="order-col-v2">
          <div class="label-v2">Paiement</div>
          <div class="value-v2">
            {% if order.payment_status == "paid" %}
              <span style="color:#177245;font-weight:900;">✓ Payée</span>
            {% elif order.payment_status == "partial" %}
              <span style="color:#B65C13;font-weight:900;">Partielle</span>
            {% else %}
              <span style="color:#DC2626;font-weight:900;">Non payée</span>
            {% endif %}
          </div>
        </div>

        <div class="order-col-v2 order-total-v2">
          <div class="label-v2">Total</div>
          <div class="value-v2 price-v2">
            {% if order.total_client_ttc %}
              {{ order.total_client_ttc|floatformat:0 }}
            {% elif order.total %}
              {{ order.total|floatformat:0 }}
            {% else %}
              0
            {% endif %}
            FCFA
          </div>
        </div>
      </div>

      {# Adresse courte — annotée dans la vue, affichée si disponible #}
      {% if order.pickup_address_short and order.pickup_address_short != '—' %}
      <div style="margin-top:10px;padding-top:10px;border-top:1px solid #E8EDF5;">
        <span style="font-size:12px;color:#9AA4B2;font-weight:600;">
          📍 {{ order.pickup_address_short }}
        </span>
      </div>
      {% endif %}

      <div class="order-footer-v2">
        <div class="order-actions-v2">
          {% if order.payment_status != "paid" %}
            <a href="{% url 'orders:client_order_pay_wave_page' order.id %}"
               class="btn-pay-v2 full">
              💳 Payer maintenant
            </a>
          {% else %}
            <a href="{% url 'orders:client_order_detail' order.id %}"
               class="btn-track-v2 full">
              Suivre la commande →
            </a>
          {% endif %}
        </div>
      </div>

    </div>
    {% empty %}
    <div class="empty-box-v2">
      <strong>Aucune commande pour le moment</strong>
      <p>Crée ta première commande FAGNI — collecte à domicile, livraison garantie.</p>
      <a href="{% url 'orders:client_new_order' %}" class="btn-main-v2">
        Créer ma première commande
      </a>
    </div>
    {% endfor %}
  </div>

  {# Pagination — logique inchangée #}
  {% if is_paginated %}
  <div class="orders-pagination">
    {% if page_obj.has_previous %}
      <a class="pg-btn" href="?page={{ page_obj.previous_page_number }}#recent-orders">
        ← Précédent
      </a>
    {% else %}
      <span class="pg-btn pg-btn--disabled">← Précédent</span>
    {% endif %}

    <div class="pg-pages">
      <a class="pg-page {% if page_obj.number == 1 %}is-active{% endif %}"
         href="?page=1#recent-orders">1</a>

      {% if show_left_ellipsis %}
        <span class="pg-dots">…</span>
      {% endif %}

      {% for n in pages %}
        <a class="pg-page {% if page_obj.number == n %}is-active{% endif %}"
           href="?page={{ n }}#recent-orders">{{ n }}</a>
      {% endfor %}

      {% if page_obj.paginator.num_pages > 1 %}
        {% if show_right_ellipsis %}
          <span class="pg-dots">…</span>
        {% endif %}
        <a class="pg-page
             {% if page_obj.number == page_obj.paginator.num_pages %}is-active{% endif %}"
           href="?page={{ page_obj.paginator.num_pages }}#recent-orders">
          {{ page_obj.paginator.num_pages }}
        </a>
      {% endif %}
    </div>

    {% if page_obj.has_next %}
      <a class="pg-btn" href="?page={{ page_obj.next_page_number }}#recent-orders">
        Suivant →
      </a>
    {% else %}
      <span class="pg-btn pg-btn--disabled">Suivant →</span>
    {% endif %}
  </div>
  {% endif %}

</section>

{# ══════════════════════════════════════════════════════════════════════════
   SERVICES FAGNI
   Pressing     → ACTIF  (lien client_new_order)
   Cordonnerie  → ACTIF  (lien client_new_order)
   Retouche     → BIENTÔT (non cliquable)
   Ménage       → BIENTÔT (non cliquable)
   ══════════════════════════════════════════════════════════════════════════ #}
<section class="services-v2">
  <div class="section-head-v2">
    <div>
      <div class="section-kicker-v2">Services disponibles</div>
      <h2>Choisis ton service</h2>
    </div>
    <a href="{% url 'orders:client_new_order' %}" class="section-link-v2">Commander →</a>
  </div>

  <div class="services-grid-v2">

    {# ── PRESSING — ACTIF ── #}
    <a href="{% url 'orders:client_new_order' %}" class="service-card-v2">
      <div class="service-card-v2__icon">👔</div>
      <strong>Pressing</strong>
      <p>Chemises, pantalons, vêtements du quotidien.
         Collecte et livraison incluses.</p>
      <small>✅ Disponible · Délai 48h</small>
    </a>

    {# ── CORDONNERIE — ACTIF ── #}
    <a href="{% url 'orders:client_new_order' %}" class="service-card-v2">
      <div class="service-card-v2__icon">👟</div>
      <strong>Cordonnerie</strong>
      <p>Réparation, entretien et restauration de chaussures.
         Collecte à domicile.</p>
      <small>✅ Disponible · Sur devis</small>
    </a>

    {# ── RETOUCHE — BIENTÔT ── #}
    <div class="service-card-v2"
         style="opacity:.5;cursor:default;pointer-events:none;"
         aria-disabled="true">
      <div class="service-card-v2__icon">🧵</div>
      <strong>Retouche</strong>
      <p>Ourlets, fermetures, ajustements et réparations textiles.</p>
      <small>🔜 Bientôt disponible</small>
    </div>

    {# ── MÉNAGE — BIENTÔT ── #}
    <div class="service-card-v2"
         style="opacity:.5;cursor:default;pointer-events:none;"
         aria-disabled="true">
      <div class="service-card-v2__icon">🧹</div>
      <strong>Ménage</strong>
      <p>Nettoyage domicile, surfaces et entretien courant.</p>
      <small>🔜 Bientôt disponible</small>
    </div>

  </div>
</section>

{# ══════════════════════════════════════════════════════════════════════════
   SUPPORT WHATSAPP — remplace TON_NUMERO par le vrai numéro CI
   ══════════════════════════════════════════════════════════════════════════ #}
<div class="wallet-boost-banner"
     style="background:#F0FDF4;border-color:#16a34a;">
  💬&nbsp;
  <span style="color:#166534;font-weight:800;">
    Un problème ? Une question ? On répond sous 30 minutes.
  </span>
  <a href="https://wa.me/TON_NUMERO"
     target="_blank"
     rel="noopener"
     class="wallet-boost-btn"
     style="background:#25D366;">
    Contacter sur WhatsApp
  </a>
</div>

{# CTA FLOTTANT — inchangé #}
<a href="{% url 'orders:client_new_order' %}" class="floating-cta-v2">
  + Commander
</a>

</div>
{% endblock %}"""

# ── 4. LOCALISER ET REMPLACER LE BLOCK CONTENT ───────────────────────────────
MARKER_START = "{% block content %}"
MARKER_END   = "{% endblock %}"

start_idx = raw.find(MARKER_START)
# rfind → dernier {% endblock %} = fermeture de {% block content %}
end_idx   = raw.rfind(MARKER_END)

if start_idx == -1:
    print("❌ Marqueur '{% block content %}' introuvable. Annulé.")
    sys.exit(1)

if end_idx == -1 or end_idx <= start_idx:
    print("❌ Marqueur '{% endblock %}' introuvable ou mal positionné. Annulé.")
    sys.exit(1)

# Tout ce qui précède le block content (extends + loads + CSS) = intact
before = raw[:start_idx]
# Tout ce qui suit le dernier endblock (généralement rien ou \n)
after  = raw[end_idx + len(MARKER_END):]

patched = before + NEW_BLOCK + after

# ── 5. VALIDATION BASIQUE ─────────────────────────────────────────────────────
block_opens  = patched.count("{% block ")
block_closes = patched.count("{% endblock %}")
if block_opens != block_closes:
    print(f"⚠️  Déséquilibre blocks : {block_opens} opens / {block_closes} closes")
    print("   Vérification recommandée. Le fichier N'A PAS été écrit.")
    sys.exit(1)

# ── 6. ÉCRITURE ───────────────────────────────────────────────────────────────
TEMPLATE.write_text(patched, encoding="utf-8")

print(f"✅ Template patché      : {TEMPLATE}")
print(f"   Taille après patch   : {len(patched):,} chars / {patched.count(chr(10))+1} lignes")
print(f"   Blocs Django         : {block_opens} {% block %} / {block_closes} {% endblock %}")
print()
print("── Étapes suivantes ────────────────────────────────────────────")
print("   1. python manage.py check")
print("   2. Ouvrir /client/home/ dans le navigateur")
print("   3. Vérifier : nom client, KPIs réels, alertes, services")
print("   4. git add orders/templates/orders/client_home.html")
print("   5. git commit -m 'feat: homepage client premium patch 001'")
print("   6. ⚠️  Remplacer TON_NUMERO dans le template par le vrai numéro WhatsApp")
print("────────────────────────────────────────────────────────────────")

