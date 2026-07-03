#!/bin/sh
# Docker HEALTHCHECK helper — curl /health/ with Host from ALLOWED_HOSTS.
# Default Host: localhost fails Django when ALLOWED_HOSTS is production-only.
set -e
H=$(printf '%s' "${ALLOWED_HOSTS:-localhost}" | cut -d, -f1 | tr -d ' ')
PORT="${HEALTHCHECK_PORT:-8000}"
curl -fsS -H "Host: ${H}" "http://127.0.0.1:${PORT}/health/" >/dev/null
