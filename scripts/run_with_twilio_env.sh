#!/usr/bin/env bash
set -e

cd ~/projects/fagni
source venv/bin/activate
source ~/projects/fagni/.env.twilio.local

python manage.py shell -c "from django.conf import settings; print('TWILIO_VERIFY_ENABLED=', settings.TWILIO_VERIFY_ENABLED); print('TWILIO_ACCOUNT_SID=', bool(settings.TWILIO_ACCOUNT_SID)); print('TWILIO_VERIFY_SERVICE_SID=', bool(settings.TWILIO_VERIFY_SERVICE_SID))"

python manage.py runserver 8001
