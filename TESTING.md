# Commerce regression tests

Create a virtual environment, install `requirements-test.txt`, and run:

```sh
python manage.py test products shop wallet users --settings=shoeverse.test_settings
python manage.py makemigrations --check --dry-run --settings=shoeverse.test_settings
```

This focused suite uses the real customer routes and templates, mocked Razorpay
API responses, in-memory file storage, and no email or Cloudinary credentials.
It excludes the admin/PDF routes and their native Cairo dependencies. It does
not replace a full deployment smoke test or a Razorpay sandbox integration test.

SQLite is the default for local checks. Three concurrency tests require
PostgreSQL and are skipped on SQLite. To run them, set `TEST_POSTGRES=true` plus
`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, and `DB_PORT`; the database user
must be allowed to create a test database. The GitHub Actions workflow runs the
suite against PostgreSQL 16. A second CI job builds the full Docker image,
starts the Compose stack, checks the storefront health endpoint, and runs all
commerce tests against the container's PostgreSQL database. See the
[Docker instructions](README.md#run-with-docker-compose) to reproduce this locally.

## Deploying these fixes

- Apply both new payment migrations with `python manage.py migrate`.
- Configure `.env` using `.env.example`. Debug mode defaults to false. Set
  `DJANGO_DEBUG=true` only for local HTTP development; production uses HTTPS
  redirects and secure cookies. Configure allowed hosts and CSRF origins for
  the deployment's actual domains.
- Checkout payments created before this deployment have no saved checkout
  snapshot and cannot be fulfilled by the new callback. Drain active checkout
  sessions before rollout and reconcile any captured legacy payments manually.
  Existing successful payments without an order link cannot be replayed.
- If stock becomes unavailable after capture, the callback creates no order and
  shows the payment reference for support. Support must reconcile the captured
  payment and arrange fulfillment or a refund; automatic provider refunds and
  webhook reconciliation are outside this change.
- `HOMEPAGE_FEATURED_PRODUCT_IDS` optionally selects a comma-separated set of
  products. Otherwise the homepage displays up to four latest active products.

The product importer suite uses in-memory image storage and mocked downloads.
It checks preview mode, repeated import/stock preservation, and rollback after
image or storage failures; CI never fetches catalog photos or seeds live data.
