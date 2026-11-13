# Shoeverse

A footwear e-commerce application built with Django and PostgreSQL, with a
customer storefront and a custom administration dashboard.

[Live site](https://shoeverse.shaad.site/) · [Testing guide](TESTING.md)

## Features

**Shopping**

- Browse, search, sort, and filter footwear for men, women, and kids.
- Choose size variants, manage a cart, and save products to a wishlist.
- Apply coupons and product or category offers.
- Check out using Razorpay, a wallet balance, or cash on delivery.
- Track orders, download PDF invoices, cancel eligible items, and request returns.
- Review purchased products and contact support through ticket conversations.

**Accounts and administration**

- Email signup with OTP verification, Google sign-in, and password recovery.
- Customer profiles, delivery addresses, referral rewards, and wallet history.
- A custom dashboard for users, products, variants, categories, and stock.
- Order and return management, coupons, offers, and homepage banners.
- Sales analytics and PDF/Excel report exports.

## Technology

| Layer | Tools |
| --- | --- |
| Backend | Python, Django 5.2 |
| Database | PostgreSQL |
| Frontend | Django templates, Tailwind CSS via CDN, JavaScript |
| Authentication | Custom Django user model, django-allauth, Google OAuth |
| Payments | Razorpay, internal wallet, cash on delivery |
| Media | Cloudinary |
| Reports | ReportLab, xhtml2pdf, OpenPyXL |

The frontend is rendered by Django; there is no separate Node.js build step.

## Run locally

### 1. Prerequisites

Use Python 3.12, PostgreSQL, and a virtual environment. The full dependency list
includes PDF/image libraries that need Cairo development headers. On Ubuntu or
Debian, install the build prerequisites before installing Python packages:

```sh
sudo apt-get update
sudo apt-get install build-essential pkg-config python3-dev libcairo2-dev
```

Cloudinary credentials are needed for uploaded media. SMTP credentials are needed
for signup verification and recovery emails. Use Razorpay test credentials during
development; Google OAuth credentials are needed only for Google sign-in.

### 2. Install dependencies

```sh
git clone https://github.com/shaad4/shoeverse-ecommerce.git
cd shoeverse-ecommerce
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

On Windows, activate the environment with `.venv\Scripts\activate` and copy the
environment file using your shell's equivalent command.

### 3. Configure the database and environment

Create a PostgreSQL database and login role, then edit `.env` with their values.
For example, on a local PostgreSQL installation:

```sh
sudo -u postgres createuser --pwprompt shoeverse
sudo -u postgres createdb --owner=shoeverse shoeverse
```

Use the following local development settings, replacing the password and secret:

```dotenv
SECRET_KEY=replace-with-a-generated-secret
DJANGO_DEBUG=true
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
DB_NAME=shoeverse
DB_USER=shoeverse
DB_PASSWORD=your-local-database-password
DB_HOST=localhost
DB_PORT=5432
```

Generate a secret with:

```sh
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Copy the generated value into `.env`, quoting it so characters such as `#` are
preserved. Keep `.env` private; it is excluded from Git.

| Variables | Purpose |
| --- | --- |
| `SECRET_KEY` | Django signing key |
| `DJANGO_DEBUG` | Enables local debug mode; defaults to `false` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames, without schemes or ports |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated trusted origins, including schemes |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | PostgreSQL connection |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` | Razorpay API credentials |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | Gmail SMTP address and app password |
| `client_id`, `secret` | Google OAuth client ID and secret; names are case-sensitive |
| `CLOUD_NAME`, `API_KEY`, `API_SECRET` | Cloudinary media storage |
| `HOMEPAGE_FEATURED_PRODUCT_IDS` | Optional comma-separated product IDs |

### 4. Initialize and start

```sh
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open [localhost:8000](http://localhost:8000/). There is no bundled catalog seed:
add categories, products, images, and size variants using the admin dashboard.
The homepage handles an empty catalog and shows up to four latest active
products, unless featured product IDs are configured.

| Page | Path |
| --- | --- |
| Storefront | `/` |
| Catalog | `/products/` |
| Cart | `/cart/` |
| Custom admin login | `/adminpanel/login/` |
| Custom admin dashboard | `/adminpanel/dashboard/` |
| Django admin | `/admin/` |

The superuser command creates an account with the custom `admin` role. Customer
pages require the `user` role, so create a separate customer account to test the
shopping flow.

For Google sign-in, configure the Django Sites entry with ID `1` for your host
and register the matching OAuth redirect URI, for example
`http://localhost:8000/accounts/google/login/callback/`.

## Payments and order handling

Razorpay checkout saves the items, prices, totals, and delivery address before
payment. The callback checks payment ownership and purpose, verifies the
signature and captured INR amount, and links each payment to a single order.
Stock and payment records are locked during fulfillment. Wallet top-ups update
the balance, ledger, and payment status in one transaction.

Return refunds allocate the discounted merchandise payment and tax across items;
shipping is excluded. Repeated customer cancellations do not restore stock twice.

If stock becomes unavailable after capture, fulfillment stops and the customer
receives a support message containing the payment reference. Support must arrange
fulfillment or a refund. Automatic provider refunds and webhook reconciliation
are not implemented by these fixes.

## Tests

The focused regression suite runs without SMTP, Cloudinary, or Razorpay
credentials and avoids the native PDF/Cairo dependencies:

```sh
pip install -r requirements-test.txt
python manage.py test shop wallet users --settings=shoeverse.test_settings
python manage.py makemigrations --check --dry-run --settings=shoeverse.test_settings
```

It covers checkout snapshots, payment replay, payment ownership and capture
checks, stock changes, discounted refunds, cancellations, wallet rollback, and
the homepage. SQLite is used by default. Three concurrency tests require
PostgreSQL and are skipped on SQLite; the CI workflow runs them on PostgreSQL 16.
See [TESTING.md](TESTING.md) for PostgreSQL settings, test scope, and rollout notes.

## Project layout

```text
shoeverse/    Django settings, root URLs, and focused test configuration
users/        Accounts, profiles, addresses, and storefront homepage
products/     Catalog, variants, images, reviews, and offers
shop/         Cart, wishlist, checkout, orders, cancellations, and returns
payments/     Payment records and captured-payment verification
wallet/       Balances, transaction history, and top-ups
coupons/      Coupon definitions and usage records
support/      Support tickets and conversations
adminpanel/   Custom administration, analytics, and report exports
templates/    Shared layouts and partials
static/       Images, JavaScript, and other static assets
```

## Deployment

Set `DJANGO_DEBUG=false`, provide a strong `SECRET_KEY`, and configure the actual
hosts and HTTPS CSRF origins. HTTPS redirects and secure session/CSRF cookies are
enabled when debug mode is off. Serve Django through a production WSGI/ASGI server
and configure your proxy's forwarded headers correctly.

Apply migrations, collect static assets with `python manage.py collectstatic`,
and configure static file serving and Cloudinary storage. Set up the SMTP,
Razorpay, and optional Google OAuth credentials for the deployment.

Before rolling out the payment migrations, drain active checkout sessions.
Legacy payments have no checkout snapshot and need manual reconciliation if
already captured. Read the [rollout notes](TESTING.md#deploying-these-fixes) before
upgrading an existing installation.
