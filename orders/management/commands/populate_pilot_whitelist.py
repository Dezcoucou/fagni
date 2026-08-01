from django.core.management.base import BaseCommand, CommandError

from orders.models import PilotWhitelist
from orders.phone_utils import normalize_phone


class Command(BaseCommand):
    help = (
        "Sprint P0, Wave 1 (BP2). Peuple PilotWhitelist à partir d'une liste "
        "EXPLICITE de numéros fournie par un humain (--phones ou --file) — "
        "jamais à partir de tous les Customer existants sans vérification. "
        "Voir d'abord audit_pilot_whitelist_candidates pour préparer cette liste. "
        "Idempotent : un numéro déjà présent n'est pas dupliqué."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--phones", type=str, default="",
            help="Numéros séparés par des virgules, n'importe quel format (07…, +225…, 225…).",
        )
        parser.add_argument(
            "--file", type=str, default="",
            help="Chemin d'un fichier texte, un numéro par ligne (les lignes vides et celles "
                 "commençant par # sont ignorées).",
        )
        parser.add_argument(
            "--note", type=str, default="",
            help="Note interne appliquée à toutes les entrées créées par cet appel.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="N'écrit rien, affiche seulement ce qui serait fait.",
        )

    def handle(self, *args, **options):
        raw_numbers = []
        if options["phones"]:
            raw_numbers += [p.strip() for p in options["phones"].split(",") if p.strip()]
        if options["file"]:
            try:
                with open(options["file"], "r", encoding="utf-8") as f:
                    raw_numbers += [
                        line.strip() for line in f
                        if line.strip() and not line.strip().startswith("#")
                    ]
            except OSError as e:
                raise CommandError(f"Impossible de lire {options['file']!r} : {e}")

        if not raw_numbers:
            raise CommandError("Fournir --phones et/ou --file avec au moins un numéro.")

        note = options["note"]
        dry_run = options["dry_run"]

        created, existing, skipped = 0, 0, []
        seen_normalized = set()

        for raw in raw_numbers:
            normalized = normalize_phone(raw)
            if not normalized or len(normalized) != 10 or not normalized.startswith("0"):
                skipped.append((raw, normalized, "forme normalisée invalide"))
                continue
            if normalized in seen_normalized:
                skipped.append((raw, normalized, "doublon dans la liste fournie"))
                continue
            seen_normalized.add(normalized)

            if dry_run:
                already = PilotWhitelist.objects.filter(phone_normalized=normalized).exists()
                self.stdout.write(
                    f"  [dry-run] {raw!r} -> {normalized} "
                    f"({'déjà présent' if already else 'serait créé'})"
                )
                continue

            obj, was_created = PilotWhitelist.objects.get_or_create(
                phone_normalized=normalized,
                defaults={"active": True, "note": note},
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"  + {normalized} ajouté"))
            else:
                existing += 1
                self.stdout.write(f"  = {normalized} déjà présent (inchangé)")

        if skipped:
            self.stdout.write(self.style.WARNING(f"\n{len(skipped)} numéro(s) ignoré(s) :"))
            for raw, normalized, reason in skipped:
                self.stdout.write(f"  {raw!r} -> {normalized!r} — {reason}")

        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nTerminé : {created} créé(s), {existing} déjà présent(s), {len(skipped)} ignoré(s)."
                )
            )
            self.stdout.write(
                "Rappel : la liste blanche n'est appliquée que si PILOT_WHITELIST_ENFORCED=true. "
                "Vérifier la liste (Django Admin) avant d'activer ce réglage."
            )
