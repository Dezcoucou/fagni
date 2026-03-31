#!/usr/bin/env bash
set -e
cd ~/projects/fagni
source venv/bin/activate

export TWILIO_ACCOUNT_SID="AC...vrai..."
export TWILIO_AUTH_TOKEN="...vrai..."
export TWILIO_WHATSAPP_FROM="whatsapp:+14155238886"
export TWILIO_WHATSAPP_CONTENT_SID="HX229f5a04fd0510ce1b071852155d3e75"
export TWILIO_WHATSAPP_ENABLED="true"

python manage.py runserver 8001
