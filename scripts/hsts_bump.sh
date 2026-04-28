#!/usr/bin/env bash
# Bump SECURE_HSTS_SECONDS in /home/administrator/.env to a target value
# and restart gunicorn. Logs to /home/administrator/logs/hsts-ramp.log.
#
# Usage: hsts_bump.sh <new_seconds_value>
# Idempotent — re-running with the same value is a no-op (just restarts).

set -euo pipefail

NEW_VALUE="${1:?usage: hsts_bump.sh <seconds>}"
ENV_FILE="/home/administrator/.env"
LOG_FILE="/home/administrator/logs/hsts-ramp.log"
SERVICE="huduglue-gunicorn.service"

mkdir -p "$(dirname "$LOG_FILE")"

ts() { date -Iseconds; }

{
  echo "[$(ts)] HSTS bump → ${NEW_VALUE}"
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "[$(ts)] ERROR: $ENV_FILE missing"
    exit 1
  fi

  # Snapshot the old value
  OLD_VALUE=$(grep -E '^SECURE_HSTS_SECONDS=' "$ENV_FILE" | head -1 | cut -d'=' -f2- || true)
  echo "[$(ts)] Previous SECURE_HSTS_SECONDS=${OLD_VALUE:-<unset>}"

  # Backup before edit
  cp "$ENV_FILE" "${ENV_FILE}.bak.$(date +%s)"

  # Replace or append the line
  if grep -qE '^SECURE_HSTS_SECONDS=' "$ENV_FILE"; then
    # Use python for safe in-place rewrite (handles any escape weirdness)
    /home/administrator/venv/bin/python - "$ENV_FILE" "$NEW_VALUE" <<'PY'
import re, sys
path, val = sys.argv[1], sys.argv[2]
with open(path) as f:
    content = f.read()
content = re.sub(r'^SECURE_HSTS_SECONDS=.*$', f'SECURE_HSTS_SECONDS={val}', content, count=1, flags=re.M)
with open(path, 'w') as f:
    f.write(content)
PY
  else
    echo "SECURE_HSTS_SECONDS=${NEW_VALUE}" >> "$ENV_FILE"
  fi

  echo "[$(ts)] .env updated; restarting ${SERVICE}"
  sudo -n systemctl restart "$SERVICE"
  sleep 4
  if systemctl is-active --quiet "$SERVICE"; then
    echo "[$(ts)] ${SERVICE} active"
  else
    echo "[$(ts)] ERROR: ${SERVICE} failed to activate"
    exit 2
  fi

  # Verify the header is being served
  HEADER=$(curl -ski https://huduglue.agit8or.net/account/login/ 2>/dev/null | grep -i 'strict-transport' || echo '<header missing>')
  echo "[$(ts)] Live header: ${HEADER}"
  echo "[$(ts)] Done."
} >> "$LOG_FILE" 2>&1
