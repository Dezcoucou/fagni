#!/usr/bin/env bash
set -euo pipefail
DOMAIN="dezcoucou80.pythonanywhere.com"
fail=0
for u in "/" "/public/ping/" "/commandes/ping/" "/clients/ping/" "/admin/login/"; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "https://$DOMAIN$u" || echo "000")
  echo "$(date '+%F %T')  $u -> $code"
  [ "$code" = "200" ] || fail=1
done
exit $fail
