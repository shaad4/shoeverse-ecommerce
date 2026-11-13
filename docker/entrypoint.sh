#!/bin/sh
set -eu

python manage.py migrate --noinput
if [ "${COLLECT_STATIC:-0}" = "1" ]; then
    python manage.py collectstatic --noinput
fi
exec "$@"
