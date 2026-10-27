#!/usr/bin/env bash
# Render build script: install, collect static, migrate, seed demo data, create admin
set -o errexit
pip install -r requirements.txt
python manage.py collectstatic --no-input
# Fail loudly in the build log if the photo-storage packages did not install
python -c "import cloudinary, cloudinary_storage; print('[photopholio] cloudinary packages installed')"
python manage.py migrate
if [ "$RESEED_DEMO" = "1" ]; then
  # One-off: rebuild only the demo photographers (e.g. after switching photo storage). Remove RESEED_DEMO afterwards.
  python manage.py seed_demo --reset-demo
else
  python manage.py seed_demo
fi
# Creates the admin only if DJANGO_SUPERUSER_* env vars are set (ignore error if it already exists)
python manage.py createsuperuser --no-input || true
