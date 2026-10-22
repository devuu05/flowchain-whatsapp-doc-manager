#!/bin/sh
# Usage: entrypoint.sh api | worker | migrate | cli <args>
set -eu
case "${1:-api}" in
  api)
    if [ "${RUN_MIGRATIONS_ON_START:-false}" = "true" ]; then alembic upgrade head; fi
    if [ -n "${BOOTSTRAP_ADMIN_EMAIL:-}" ]; then python -m app.cli bootstrap || echo "bootstrap failed (see logs)"; fi
    exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers \
      --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" --no-server-header --no-access-log
    ;;
  worker) exec python -m app.workers.runner ;;
  migrate) exec alembic upgrade head ;;
  cli) shift; exec python -m app.cli "$@" ;;
  *) exec "$@" ;;
esac
