#!/usr/bin/env bash
# Render build script: install, collect static, migrate, seed demo data, create admin
set -o errexit
pip install -r requirements.txt
python manage.py collectstatic --no-input
python manage.py migrate
python manage.py seed_demo
# Creates the admin only if DJANGO_SUPERUSER_* env vars are set (ignore error if it already exists)
python manage.py createsuperuser --no-input || true
