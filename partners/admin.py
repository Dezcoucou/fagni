from django.contrib import admin
from .models import LaundryPartner, DeliveryPartner, RelayPointPartner


@admin.register(LaundryPartner)
class LaundryPartnerAdmin(admin.ModelAdmin):
    list_display  = ['name', 'partner_type', 'city', 'partner_score', 'level',
                     'score_delai', 'score_litiges', 'score_dispo', 'score_refus',
                     'wave_number', 'is_active']
    list_filter   = ['partner_type', 'level', 'is_active', 'city']
    search_fields = ['name', 'phone', 'wave_number']
    readonly_fields = ['partner_score', 'level', 'score_updated_at']
    fieldsets = [
        ('Identité', {'fields': [
            'name', 'partner_type', 'phone', 'email', 'wave_number',
            'address', 'city', 'latitude', 'longitude', 'is_active'
        ]}),
        ('Partner Score', {'fields': [
            'partner_score', 'level', 'score_updated_at',
            'score_delai', 'score_litiges', 'score_dispo', 'score_refus'
        ]}),
        ('Rémunération', {'fields': [
            'remuneration_collecte', 'remuneration_livraison'
        ]}),
        ('Notes', {'fields': ['notes']}),
    ]
    actions = ['recalculate_scores']

    def recalculate_scores(self, request, queryset):
        from django.utils import timezone
        for p in queryset:
            p.recalculate_score()
            p.score_updated_at = timezone.now()
            p.save()
        self.message_user(request, f"{queryset.count()} score(s) recalculé(s).")
    recalculate_scores.short_description = "Recalculer les Partner Scores"


@admin.register(DeliveryPartner)
class DeliveryPartnerAdmin(admin.ModelAdmin):
    list_display  = ['name', 'vehicle_type', 'city', 'wave_number', 'is_active']
    list_filter   = ['vehicle_type', 'is_active']
    search_fields = ['name', 'phone', 'wave_number']


@admin.register(RelayPointPartner)
class RelayPointPartnerAdmin(admin.ModelAdmin):
    list_display  = ['name', 'relay_type', 'city', 'opening_hours', 'is_active']
    list_filter   = ['relay_type', 'is_active']
    search_fields = ['name', 'city']
