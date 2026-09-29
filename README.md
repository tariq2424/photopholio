# PhotoPholio: Book Professional Photographers Online

A full-stack Django web app where users can search photographers by city, style and rating, view portfolios,
book an event date, pay (demo checkout) and leave reviews. Built from my GTU summer internship project
(InfoLabz IT Services, July 2025) and rebuilt with a complete customer-facing site on top of the original data model.

> **Note:** The original internship version focused on the data model and a customised Jazzmin admin.
> This repository is a rebuilt and extended version: same 12-table schema, plus a public website, real authentication,
> booking rules, tests and deployment setup.

## Features
- Search and filter photographers (keyword, city, style, minimum rating, availability, sort, pagination)
- Photographer profile with portfolios, photo gallery lightbox and client reviews
- Register / log in / log out using Django's built-in authentication (hashed passwords)
- Booking with validation (no past dates, no double-booking of the same photographer and day)
- Demo payment flow that confirms the booking; cancel bookings; leave a review after a completed booking
- Photographer rating auto-updates from reviews
- Contact form; user profile with photo upload
- Customised admin (Jazzmin): filters, search, inline editing, image previews, "mark completed" action
- Responsive UI (Bootstrap 5), keyboard-focus styles, reduced-motion support
- 7 automated tests

## Tech stack
Python 3.10+, Django 5.2, SQLite (PostgreSQL via `DATABASE_URL`), Pillow, Bootstrap 5, WhiteNoise, Gunicorn.

## Run locally
```bash
git clone <your-repo-url> && cd photopholio
python -m venv venv
venv\Scripts\activate          # Windows   (Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo         # demo photographers, portfolios, reviews
python manage.py createsuperuser   # your own admin account
python manage.py runserver
```
Open http://127.0.0.1:8000/ . Admin panel: http://127.0.0.1:8000/admin/ .
Demo customer login created by the seed command: `demo` / `demo12345` (for local demo only).

Run tests: `python manage.py test`

## Data model (12 tables)
`User` (Django built-in) - `UserProfile` - `Country` > `State` > `City` - `Photographer` > `Portfolio` > `Photo`
- `Booking` > `Payment`, `Feedback` - `ContactUs`

Differences from the original data dictionary (deliberate):
- `User` uses Django's auth model, so passwords are hashed instead of stored as plain text.
- `Photographer` gained `city`, `image`, `price_per_day`, `is_available` (needed for search and booking).
- `Photo.order_status` became `status` (published / hidden) to control public visibility.
- `Payment` keeps separate `payment_method` and `status` fields (the original document mixed them up).

## Deploy on Render (free)
1. Push this repo to GitHub.
2. Render > New > Web Service > connect the repo.
3. Build command: `./build.sh`   Start command: `gunicorn config.wsgi:application`
4. Environment variables: `SECRET_KEY` (random string), `DEBUG=False`,
   `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL`, `DJANGO_SUPERUSER_PASSWORD`, optionally `DATABASE_URL`.

Free-tier note: the disk is wiped on every deploy, so with SQLite the demo data is re-seeded at each build.
For permanent data attach a Render PostgreSQL and use S3 or Cloudinary for uploaded images.

## Project structure
```
config/                 project settings and root URLs
photopholio/
  models.py             database tables
  views.py              request handling logic
  forms.py              forms and validation
  urls.py               URL routes
  admin.py              admin customisation
  templates/photopholio HTML templates
  static/photopholio    CSS and JS
  management/commands/seed_demo.py   demo data generator
  tests.py              automated tests
```
