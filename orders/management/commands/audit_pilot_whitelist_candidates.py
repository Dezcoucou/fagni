import re
from collections import defaultdict

from django.core.management.base import BaseCommand

from orders.models import Customer, PilotWhitelist
from orders.phone_utils import normalize_phone

# Sprint P0, Wave 1 (BP2). Ce motif detecte les faux numeros evidents
# (repetition d'un seul chiffre, suites triviales) - une heuristique de
# bon sens, pas une validation d'operateur telecom.
_SUSPICIOUS_PATTERNS = (
    re.compile(r'^0(\d)\1{8}$'),          # ex: 0111111111, 0000000000
    re.compile(r'^0123456789$'),
    re.compile(r'^0987654321$'),
)
_TEST_NAME_HINTS = ('test', 'fake', 'demo', 'essai', 'ignore')


class Command(BaseCommand):
    help = (
        "Sprint P0, Wave 1 (BP2). Audit EN LECTURE SEULE des Customer existants, "
        "en vue de peupler PilotWhitelist. N'ecrit jamais rien : sert uniquement "
        "a preparer la liste a fournir a populate_pilot_whitelist --phones/--file. "
        "Ne bascule PAS automatiquement les Customer existants en participants "
        "autorises - ce choix reste humain."
    )

    def handle(self, *args, **options):
        customers = list(Customer.objects.all().order_by('id'))
        self.stdout.write(f"Total Customer en base : {len(customers)}\n")

        by_normalized = defaultdict(list)
        for c in customers:
            by_normalized[normalize_phone(c.phone)].append(c)

        already_whitelisted = set(
            PilotWhitelist.objects.values_list('phone_normalized', flat=True)
        )

        suspects = []
        duplicates = []
        candidates = []

        for normalized, group in sorted(by_normalized.items()):
            if len(group) > 1:
                duplicates.append((normalized, group))
                continue

            c = group[0]
            reasons = []
            if not normalized or len(normalized) != 10 or not normalized.startswith('0'):
                reasons.append("forme normalisée invalide (pas 10 chiffres commençant par 0)")
            if any(p.match(normalized or '') for p in _SUSPICIOUS_PATTERNS):
                reasons.append("motif suspect (répétition/suite triviale)")
            if any(hint in (c.name or '').lower() for hint in _TEST_NAME_HINTS):
                reasons.append(f"nom évoquant un compte de test : « {c.name} »")

            if reasons:
                suspects.append((c, normalized, reasons))
            else:
                candidates.append((c, normalized))

        self.stdout.write(self.style.WARNING(f"\n=== DOUBLONS APRÈS NORMALISATION ({len(duplicates)}) ==="))
        self.stdout.write("Même numéro réel, formes différentes en base — à fusionner ou trancher manuellement.\n")
        for normalized, group in duplicates:
            for c in group:
                self.stdout.write(f"  id={c.id:<6} nom={c.name!r:<25} phone brut={c.phone!r:<20} -> {normalized}")

        self.stdout.write(self.style.WARNING(f"\n=== SUSPECTS ({len(suspects)}) ==="))
        self.stdout.write("À exclure très probablement du peuplement de la whitelist.\n")
        for c, normalized, reasons in suspects:
            already = " [déjà whitelisté]" if normalized in already_whitelisted else ""
            self.stdout.write(f"  id={c.id:<6} nom={c.name!r:<25} -> {normalized}{already}  — {', '.join(reasons)}")

        self.stdout.write(self.style.SUCCESS(f"\n=== CANDIDATS PLAUSIBLES ({len(candidates)}) ==="))
        self.stdout.write(
            "Numéros au format attendu, sans signal suspect — à faire valider par un humain\n"
            "(liste réelle des participants sélectionnés) avant peuplement, pas à importer tel quel.\n"
        )
        for c, normalized in candidates:
            already = " [déjà whitelisté]" if normalized in already_whitelisted else ""
            self.stdout.write(f"  id={c.id:<6} nom={c.name!r:<25} -> {normalized}{already}")

        self.stdout.write(
            "\nProchaine étape : une fois la vraie liste des participants confirmée, "
            "utiliser `manage.py populate_pilot_whitelist --phones 0700000001,0700000002,... "
            "--note \"cohorte pilote Riviera 3\"` (ou --file) — jamais un import automatique de tout ce qui précède."
        )
