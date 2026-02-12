#!/usr/bin/env bash
set -euo pipefail

USER="${USER:-DesireTongbe}"
BASE="${BASE:-http://127.0.0.1:8000}"
COOKIES="${COOKIES:-/tmp/fagni_cookies.txt}"
ORDER_ID="${ORDER_ID:-462}"

read -s -p "Password for $USER: " PASS
echo

rm -f "$COOKIES"

# 1) GET login -> csrftoken
curl -sS -c "$COOKIES" "$BASE/accounts/login/" >/dev/null

CSRF="$(python - <<'PY'
import http.cookiejar
cj=http.cookiejar.MozillaCookieJar("/tmp/fagni_cookies.txt")
cj.load(ignore_discard=True, ignore_expires=True)
print(next((c.value for c in cj if c.name=="csrftoken"), ""))
PY
)"

# 2) POST login -> sessionid
curl -sS -b "$COOKIES" -c "$COOKIES" \
  -X POST "$BASE/accounts/login/" \
  -H "Referer: $BASE/accounts/login/" \
  --data "csrfmiddlewaretoken=$CSRF&username=$USER&password=$PASS" \
  >/dev/null

echo "== Ops dashboard status =="
curl -sS -o /dev/null -w "%{http_code}\n" -b "$COOKIES" "$BASE/orders/ops-dashboard/"

echo "== Weighing page status ($ORDER_ID) =="
curl -sS -o /dev/null -w "%{http_code}\n" -b "$COOKIES" "$BASE/orders/laundry/weighing/$ORDER_ID/"

CSRF_COOKIE="$(python - <<'PY'
import http.cookiejar
cj=http.cookiejar.MozillaCookieJar("/tmp/fagni_cookies.txt")
cj.load(ignore_discard=True, ignore_expires=True)
print(next((c.value for c in cj if c.name=="csrftoken"), ""))
PY
)"

echo "== POST confirm ($ORDER_ID) =="
curl -sS -i -b "$COOKIES" -c "$COOKIES" \
  -X POST "$BASE/orders/laundry/weighing/$ORDER_ID/confirm/" \
  -H "Referer: $BASE/orders/laundry/weighing/$ORDER_ID/" \
  -H "X-CSRFToken: $CSRF_COOKIE" \
  --data "csrfmiddlewaretoken=$CSRF_COOKIE" \
  | sed -n '1,18p'

echo "== POST dispute ($ORDER_ID) =="
curl -sS -i -b "$COOKIES" -c "$COOKIES" \
  -X POST "$BASE/orders/laundry/weighing/$ORDER_ID/dispute/" \
  -H "Referer: $BASE/orders/laundry/weighing/$ORDER_ID/" \
  -H "X-CSRFToken: $CSRF_COOKIE" \
  -F "csrfmiddlewaretoken=$CSRF_COOKIE" \
  -F "reason=test_dispute" \
  | sed -n '1,18p'
