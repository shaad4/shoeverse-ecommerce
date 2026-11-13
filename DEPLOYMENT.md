# Production behind Tailscale Funnel

This deployment uses PostgreSQL, Gunicorn, and Nginx. Tailscale Funnel terminates
public HTTPS and forwards to Nginx on the server's loopback port 8080. The
application and database have no published ports. This requires a running Funnel
on the host; the development `compose.yaml` is not used.

## Initial installation on box3

Run in the repository on the server:

```sh
cd ~/project/shoeverse-ecommerce
git pull --ff-only origin fix/payment-refund-stock-integrity
python3 docker/configure-production.py cluster-box3.tail6ee210.ts.net
docker compose -f compose.production.yaml up --build --wait --wait-timeout 180
```

The configuration helper generates a signing key and database password if they
are blank, retains existing credentials, sets debug off and the public hostname,
and writes `.env` with mode 600. It does not print credentials. Keep this file
and the PostgreSQL data backed up. Do not change an initialized database's
password only in `.env`; PostgreSQL credentials require a corresponding SQL
change.

Migrations and static collection run before Gunicorn starts. Once all services
are healthy, switch Funnel from the temporary port-80 test server to Nginx:

```sh
sudo tailscale funnel --bg http://127.0.0.1:8080
tailscale funnel status
docker compose -f compose.production.yaml exec web python manage.py createsuperuser
```

Visit https://cluster-box3.tail6ee210.ts.net/ and `/adminpanel/login/` to populate
the catalog. Stop the temporary Python test server with Ctrl+C in its original
terminal. The containers restart automatically when Docker restarts; the host's
Tailscale daemon must also remain running.

## External service configuration

Set SMTP, Cloudinary, Razorpay, and optional Google OAuth values in `.env` on the
server (never in Git or chat), then recreate the web service:

```sh
nano .env
docker compose -f compose.production.yaml up -d --force-recreate web nginx
```

Without these credentials the homepage/catalog can run, but OTP signup, email
recovery, media uploads, and online payments need their corresponding services.
Use a separate customer account for shopping; admin accounts have a different
role. For Google login, update Django Site ID 1 and register
`https://cluster-box3.tail6ee210.ts.net/accounts/google/login/callback/` with the
OAuth provider.

## Verify and operate

```sh
docker compose -f compose.production.yaml ps
docker compose -f compose.production.yaml logs --tail 100 web nginx
curl -I -H 'Host: cluster-box3.tail6ee210.ts.net' http://127.0.0.1:8080/
curl -I https://cluster-box3.tail6ee210.ts.net/
docker compose -f compose.production.yaml exec -e TEST_POSTGRES=true web python manage.py test shop wallet users --settings=shoeverse.test_settings
```

Nginx always tells Django that public traffic used HTTPS. Only use this config
behind the trusted HTTPS Funnel with its loopback binding; do not expose this
Nginx HTTP port publicly. `localhost` remains allowed for internal health checks.

To stop containers while retaining the database:

```sh
docker compose -f compose.production.yaml down
```

Do not add `--volumes` unless you intend to delete the database. Back up the
PostgreSQL database before updates and migrations. See [TESTING.md](TESTING.md)
for the payment migration and manual reconciliation requirements.

## A custom domain later

Funnel provides the `ts.net` hostname and certificate. A CNAME alone cannot add
a custom HTTPS domain to Funnel. Select an ingress supporting your domain
(such as a domain-aware HTTPS tunnel or a public reverse proxy), then update
Django's allowed hosts, CSRF origins, Google callbacks, and the ingress TLS
configuration together. Namecheap DNS remains unchanged during this initial
Funnel deployment.

## Automatic startup on box3

For `shoeverse.shaad.site` through Cloudflare Tunnel, keep the existing
`cloudflared.service` installed on the host. Once the production images are built
and `.env` is configured, run on box3:

```sh
cd ~/project/shoeverse-ecommerce
sudo sh docker/install-autostart.sh
```

This installer targets `/data/home/project/shoeverse-ecommerce`. It enables Docker
and the existing Cloudflare service at boot and installs:

- `shoeverse-start.service`: brings up the production stack without rebuilding,
  pulling images, resetting inventory, or deleting volumes.
- `shoeverse-start.timer`: retries the same startup every two minutes while the
  VM is running, including after a suspend/resume where boot units do not rerun.

The existing container restart policies still apply. The timer reconciles the
current Compose configuration, so saved `.env` or Compose changes can be applied
automatically on its next run. Deploy and build new application code explicitly.

Verify with:

```sh
systemctl is-enabled docker cloudflared shoeverse-start.service shoeverse-start.timer
systemctl is-active docker cloudflared shoeverse-start.timer
systemctl list-timers shoeverse-start.timer --no-pager
sudo journalctl -u shoeverse-start.service -n 40 --no-pager
docker compose -f compose.production.yaml ps
```

The startup service is a one-shot task. Seeing it become `inactive (dead)` after
a successful run is normal; the timer and containers should remain running.

For planned maintenance, disable autostart before stopping the containers:

```sh
sudo systemctl disable --now shoeverse-start.timer shoeverse-start.service
docker compose -f compose.production.yaml down
```

To restore it, rerun the installer. Database volumes are retained by these steps.

### Instabox cooldown versus application startup

Startup services run only when the VM runs. They cannot wake a paused VM or
shorten an enforced cooldown. Instabox documents automatic wake for its own SSH,
API and published-port requests; a request through an independent Cloudflare
Tunnel cannot reach its connector while the VM is paused. After the cooldown,
use Instabox's supported wake/resume mechanism; these services then handle app
startup/retries. Consistent public availability requires hosting without an
enforced runtime pause. See
[Instabox auto-pause and maximum runtime](https://instabox.sh/docs/machines/auto-pause).
