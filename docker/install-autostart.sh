#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    exec sudo sh "$0" "$@"
fi

cd "$(dirname "$0")/.."
if [ "$(pwd -P)" != "/data/home/project/shoeverse-ecommerce" ]; then
    echo 'This installer targets box3 at /data/home/project/shoeverse-ecommerce.' >&2
    exit 1
fi
if [ ! -f .env ]; then
    echo 'Configure .env and build the production stack before enabling autostart.' >&2
    exit 1
fi
command -v docker >/dev/null
docker compose -f compose.production.yaml config --quiet
if ! systemctl cat cloudflared.service >/dev/null 2>&1; then
    echo 'Install the Cloudflare tunnel service using its dashboard command first.' >&2
    exit 1
fi

install -m 0644 docker/systemd/shoeverse-start.service /etc/systemd/system/shoeverse-start.service
install -m 0644 docker/systemd/shoeverse-start.timer /etc/systemd/system/shoeverse-start.timer
systemctl daemon-reload
systemctl enable --now docker.service cloudflared.service
systemctl enable shoeverse-start.service
systemctl enable --now shoeverse-start.timer
systemctl start shoeverse-start.service

echo 'Autostart installed. Docker and Cloudflare start at boot; Shoeverse startup is retried every two minutes while the VM runs.'
echo 'Instabox cooldown and waking a paused VM remain controlled by Instabox.'
