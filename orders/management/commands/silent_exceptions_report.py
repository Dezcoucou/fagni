"""
Commande Django : python3 manage.py silent_exceptions_report [--hours 24] [--log-path /chemin]

Lit le fichier de log serveur et resume les occurrences des logs d'exceptions
silencieuses ajoutes le 03/07/2026 (marqueurs "Echec silencieux:" et
"Exception silencieuse (auto-log)"), groupees par origine, avec compteur
et derniere occurrence.
"""
import re
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand

DEFAULT_LOG_PATH = "/var/log/dezcoucou80.pythonanywhere.com.error.log"
TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d{3}: (.*)$")
MARKERS = ("Echec silencieux:", "Exception silencieuse (auto-log)")


class Command(BaseCommand):
    help = "Resume les exceptions silencieuses loggees depuis le fichier de log serveur"

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=24, help="Fenetre de temps en heures (defaut: 24)")
        parser.add_argument("--log-path", type=str, default=DEFAULT_LOG_PATH, help="Chemin du fichier de log")

    def handle(self, *args, **options):
        hours = options["hours"]
        log_path = options["log_path"]
        cutoff = datetime.now() - timedelta(hours=hours)

        try:
            with open(log_path, encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except FileNotFoundError:
            self.stderr.write(f"Fichier introuvable: {log_path}")
            return

        counts = {}
        last_seen = {}
        total_matches = 0

        for line in lines:
            m = TIMESTAMP_RE.match(line)
            if not m:
                continue
            ts_str, message = m.groups()
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if ts < cutoff:
                continue
            if not any(marker in message for marker in MARKERS):
                continue

            total_matches += 1
            key = message.strip()
            counts[key] = counts.get(key, 0) + 1
            if key not in last_seen or ts > last_seen[key]:
                last_seen[key] = ts

        self.stdout.write(f"\n=== Rapport exceptions silencieuses (derniere{'s' if hours>1 else ''} {hours}h) ===\n")
        if total_matches == 0:
            self.stdout.write(self.style.SUCCESS("Aucune exception silencieuse detectee sur cette periode."))
            return

        self.stdout.write(f"Total occurrences: {total_matches}\n")
        for key, n in sorted(counts.items(), key=lambda x: -x[1]):
            self.stdout.write(f"  [{n}x] derniere: {last_seen[key]} — {key}")
