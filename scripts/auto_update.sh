#!/bin/bash
# Client St0r Auto-Update Script
# Thin wrapper: downloads and executes update_instructions.sh from GitHub.
#
# Execution is opt-in: set AUTO_UPDATE_ENABLED=True in .env (project or /etc/clientst0r/.env).

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
# shellcheck source=clientst0r_env.sh
source "$SCRIPT_DIR/clientst0r_env.sh"

LOG_FILE="/var/log/clientst0r/auto-update.log"

sudo mkdir -p /var/log/clientst0r
sudo chown -R "$(whoami):$(whoami)" /var/log/clientst0r

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

if [ "$EUID" -eq 0 ]; then
    log "ERROR: Do not run this script as root. Run as the user who owns the installation."
    exit 1
fi

clientst0r_load_deployment_env "$PROJECT_DIR"

if ! clientst0r_auto_update_enabled; then
    log "Auto-update execution is disabled. Set AUTO_UPDATE_ENABLED=True to opt in."
    exit 0
fi

log "=========================================="
log "Client St0r Auto-Update Script"
log "=========================================="
log "Project directory: $PROJECT_DIR"

REPO_URL=$(git -C "$PROJECT_DIR" remote get-url origin 2>/dev/null || echo "")
if echo "$REPO_URL" | grep -q "github.com"; then
    REPO_PATH=$(echo "$REPO_URL" \
        | sed 's|.*github\.com[:/]\(.*\)|\1|' \
        | sed 's|\.git$||')
else
    REPO_PATH="agit8or1/clientst0r"
fi

INSTRUCTIONS_URL="https://raw.githubusercontent.com/$REPO_PATH/main/deploy/update_instructions.sh"
log "Downloading update instructions from: $INSTRUCTIONS_URL"

TEMP_SCRIPT=$(mktemp /tmp/clientst0r_update_XXXXXXXX.sh)

DOWNLOAD_OK=0
if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$INSTRUCTIONS_URL" -o "$TEMP_SCRIPT" 2>/dev/null && DOWNLOAD_OK=1
fi
if [ "$DOWNLOAD_OK" -eq 0 ] && command -v wget >/dev/null 2>&1; then
    wget -q "$INSTRUCTIONS_URL" -O "$TEMP_SCRIPT" 2>/dev/null && DOWNLOAD_OK=1
fi

if [ "$DOWNLOAD_OK" -eq 0 ]; then
    log "ERROR: Failed to download update instructions (tried curl and wget)"
    rm -f "$TEMP_SCRIPT"
    exit 1
fi

if ! head -1 "$TEMP_SCRIPT" | grep -q "^#!"; then
    log "ERROR: Downloaded content is not a valid shell script"
    rm -f "$TEMP_SCRIPT"
    exit 1
fi

chmod 700 "$TEMP_SCRIPT"
log "Update instructions downloaded and validated"

GUNICORN_SERVICE=""
    for svc in clientst0r.service; do
    if systemctl list-unit-files 2>/dev/null | grep -q "^${svc}"; then
        GUNICORN_SERVICE="$svc"
        break
    fi
done

export CLIENTST0R_BASE_DIR="$PROJECT_DIR"
export CLIENTST0R_SERVICE_NAME="$GUNICORN_SERVICE"

log "Executing update instructions..."
/bin/bash "$TEMP_SCRIPT"
EXIT_CODE=$?

rm -f "$TEMP_SCRIPT"

if [ "$EXIT_CODE" -eq 0 ]; then
    log "=========================================="
    log "Update completed successfully!"
    log "=========================================="
else
    log "=========================================="
    log "Update FAILED (exit code: $EXIT_CODE)"
    log "=========================================="
fi

exit "$EXIT_CODE"
