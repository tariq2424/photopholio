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
- Request, accept, pay: the customer requests a date, the photographer accepts or declines, then the customer pays within 24 hours to confirm (demo checkout)
- Cancellation policy applied by the app: full refund 2+ days before the event, 50% one day before, none on the day, 100% if the photographer cancels. Refunds are tracked separately and marked processed by the admin
- Photographers can accept, decline, cancel (with a reason) and mark shoots completed; the admin sees every photographer cancellation
- No double-booking: accepting a date automatically declines other requests for it
- Demo payment flow that confirms the booking; cancel bookings; leave a review after a completed booking
- Photographer rating auto-updates from reviews
- Two account types chosen at signup: customers (book) and photographers (list their work). Each has its own login page
- Photographer area: create a profile, manage portfolios and photos, see bookings, submit for review
- Admin approval: a photographer's profile stays hidden from customers until an admin approves it (with a reject reason the photographer can read)
- Emails for every booking step, approval and refund (printed in the terminal until real email settings are added)
- Forgot password with an emailed reset link
- Admin analytics page at `/admin/analytics/`: revenue, bookings per month, approvals and refunds waiting, top photographers
- Availability calendar on every photographer page (green = free, click a day to fill in the booking). Photographers block days they cannot work
- Search by date: only photographers who are free on that day are shown (home page and the filters)
- Packages: photographers can offer Basic, Standard and Premium. The customer picks one when booking and the booking keeps that price, even if the photographer changes the package later
- `python manage.py add_sample_packages` gives photographers without packages a starting set (easy to edit)
- Rescheduling: once a request is accepted, the customer can ask to move it to another free day (event at least 2 days away, once per booking). The photographer accepts or declines; the price and payment stay the same, and until accepted the old date is kept
- **Travel charges (India)**: the customer pays travel, the photographer sets the price. Each photographer chooses where they take events (their city, their state or all of India), a travel charge for another city or state, and a stay-and-food charge per day away. Events in the photographer's own city have no travel charge. The customer sees the full price (service + travel) before sending the request, and the booking keeps that price even if the policy changes later. Photographers set this under Travel charges.
- **Commission and payouts**: PhotoPholio keeps 10% of the photographer's SERVICE price (travel is passed on in full; change the default with the `PLATFORM_COMMISSION_PERCENT` environment variable, or set a percent per photographer in the admin). Example: a 4-day booking of Rs.22,000 gives the photographer Rs.19,800 and PhotoPholio Rs.2,200. After the customer pays, the money is *held*; when the shoot is completed it becomes *ready to pay*; the admin pays the photographer by UPI or bank transfer and records the reference on Admin > Payouts (CSV export included). Cancellations and refunds adjust the split automatically. This is a ledger only: checkout is a demo and no real money moves.
- Photographers see what they earn (Earnings page, and the split before they accept) and save their payout details (UPI or bank) in their profile.
- Admin analytics shows money cards (booking value, commission earned and pending, payouts due, travel collected), multi-day and outstation share, and top event cities.
- After creating an account you are sent to the login page with your username filled in (never the password); after logging in, customers land on the home page (My bookings is one click away in the menu) and photographers on their dashboard.
- Photographers can add keywords (outdoor, candid, pre-wedding...) in their profile; they show as small tags on the public profile
- Saved photographers (wishlist): customers tap the heart on a card or profile and find them under Saved photographers
- Verified badge: the admin can give or remove it (Photographers > Actions); customers can filter by Verified only
- Review replies: a photographer answers each review once (editable) and it shows on the public page; the admin can hide a review, which also removes it from the rating
- Photo delivery: after a booking is completed the photographer pastes a link (Drive, Dropbox, anything https) and the customer sees "Open your photos"
- My bookings is now a small dashboard: next shoot, what to pay, refunds on the way, payment history
- Contact form; user profile with photo upload
- Customised admin (Jazzmin): filters, search, inline editing, image previews, "mark completed" action
- Responsive UI (Bootstrap 5), keyboard-focus styles, reduced-motion support
- 70 automated tests

- **Event venue and multi-day bookings**: a booking request carries the venue address, city, state, pincode and start time. An event can last several days (up to 15): the price is one day's price x the number of days, every day in the range is checked against the photographer's calendar, and a rescheduled booking keeps its length. India only for now.

- **Shoot tracking and disputes**: on the event day the photographer taps "I've arrived", "Start shoot" and "Finish today's shoot" (in order, once each, only on that date; multi-day events get one row per day). The customer is emailed on arrival and can tap "Yes, they are here". When the last day is finished the booking becomes completed and the customer is asked to confirm; the photographer's payout stays on hold until they confirm, 48 hours pass in silence (auto-confirm), or the admin decides. The customer can also report a problem (no-show, quality, other) from the first day until the payout is sent: the money is frozen and the admin sees it on the Disputes page, where they pay, refund in full, or dismiss, with a note both people receive. Every tap and decision is stored in an audit trail (`ShootEvent`) shown inside the booking in the admin. The taps are self-reported evidence, not GPS; checkout and payouts are a demo ledger.

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

## Data model (14 tables)
`User` (Django built-in) - `UserProfile` - `Country` > `State` > `City` - `Photographer` > `Portfolio` > `Photo`
- `Booking` > `Payment`, `Feedback`, `ShootDay`, `ShootEvent` - `ContactUs`

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
  travel.py             travel charges: who pays, how much
  urls.py               URL routes
  admin.py              admin customisation
  templates/photopholio HTML templates
  static/photopholio    CSS and JS
  management/commands/seed_demo.py   demo data generator
  tests.py              automated tests
```

## Email settings
There are three ways, chosen automatically from environment variables (never put keys in the code or on GitHub):

1. **Nothing set (default, local):** emails are printed in the terminal that runs the server.
2. **Gmail SMTP (local or a normal server):** `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` (a Gmail App Password) and `SITE_URL`.
3. **Brevo over HTTPS (for free hosts like Render, which block SMTP ports 25/465/587):** `BREVO_API_KEY`, `BREVO_SENDER_EMAIL` (a sender verified inside Brevo) and `SITE_URL`. The free Brevo plan allows 300 emails a day. Set these only in the host's Environment settings. If `BREVO_API_KEY` and `BREVO_SENDER_EMAIL` are both set, Brevo is used before Gmail SMTP.

`SITE_URL` is the address used in links inside emails, for example `https://photopholio-xxxx.onrender.com`.
Check it with `python manage.py send_test_email you@example.com`. A failed email never stops a booking or payment.
`python manage.py expire_bookings` cancels accepted bookings that were not paid within 24 hours (optional daily task).
