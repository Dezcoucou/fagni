#!/usr/bin/env bash
set -euo pipefail

cd "$HOME/fagni"

# Horodatage & branche
TS="$(date +'%Y-%m-%d_%H-%M-%S')"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

# Identité Git (sécurité)
git config user.email "desiretongbe@gmail.com"
git config user.name  "Dezcoucou80"

# Ajout et commit si modification
git add -A
if ! git diff --cached --quiet; then
  git commit -m "Auto-backup: ${TS}"
  git push -u origin "${BRANCH}"
  echo "[OK] Poussé sur origin/${BRANCH} à ${TS}"
else
  echo "[INFO] Aucun changement à sauvegarder (${TS})."
fi

# Entretien du dépôt
git gc --prune=now --quiet || true
