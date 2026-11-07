# Gunicorn reads this file by itself (from the project folder), so the Render Start Command can stay
# "gunicorn config.wsgi:application". Several threads per worker: a slow request (photo upload, Cloudinary,
# the database) no longer makes every other visitor wait behind it.
import os

worker_class = "gthread"
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = 120          # photographers upload many photos in one request; the default 30 s would kill it
keepalive = 5
