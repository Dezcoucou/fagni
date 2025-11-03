#!/usr/bin/env bash
set -euo pipefail

DOMAIN="dezcoucou80.pythonanywhere.com"

echo "[1/4] Purge caches py + reload webapp"
find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find . -name '*.pyc' -delete 2>/dev/null || true
touch ~/.reload-webapps

# Laisse aux workers le temps de redémarrer
sleep 3

echo "[2/4] Sanity check Django (import settings + urls)"
python - <<'PY'
import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE","fagni.settings")
django.setup()
from django.conf import settings
from django.urls import get_resolver
print("DEBUG:", settings.DEBUG)
print("ALLOWED_HOSTS:", settings.ALLOWED_HOSTS)
print("STATIC_ROOT:", settings.STATIC_ROOT)
print("URL patterns:", len(get_resolver().url_patterns))
PY

echo "[3/4] Migrations et collectstatic"
python manage.py migrate --noinput
python manage.py collectstatic --noinput | tail -n +1 >/dev/null || true

# Attente active jusqu'à ce que l'admin réponde 200 (max ~30s)
echo "[4/4] Smoke tests HTTP"
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -L -w "%{http_code}" "https://$DOMAIN/admin/login/")
  if [ "$code" = "200" ]; then break; fi
  sleep 1
done

ok=true
for u in "/" "/admin/login/" "/public/ping/" "/commandes/ping/" "/clients/ping/"; do
  # -L pour suivre les éventuels 301/302, -f pour flagger les 4xx/5xx
  code=$(curl -s -o /dev/null -L -w "%{http_code}" "https://$DOMAIN$u" || echo "ERR")
  printf "%-16s -> HTTP %s\n" "$u" "$code"
  [ "$code" = "200" ] || ok=false
done

$ok && echo "✅ Terminé." || { echo "⚠️  Terminé avec des routes non-200"; exit 1; }
