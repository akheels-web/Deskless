#!/bin/sh
set -e
# Wait-free: migrate runs on start; Postgres in compose is health-gated.
python manage.py migrate --noinput
exec gunicorn helpdesk.wsgi:application --bind 0.0.0.0:8000 --workers 3
