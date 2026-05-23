web: python manage.py migrate --run-syncdb && gunicorn fagni.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 120
