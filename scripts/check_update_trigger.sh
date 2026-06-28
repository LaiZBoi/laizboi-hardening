#!/bin/bash
# Check for update trigger file and run update if it exists.
# Runs via cron every minute when installed by update_instructions.sh.
#
# Refuses to trigger updates unless AUTO_UPDATE_ENABLED=True in .env.

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
# shellcheck source=clientst0r_env.sh
source "$SCRIPT_DIR/clientst0r_env.sh"

TRIGGER_FILE="/tmp/clientst0r-update-trigger"
UPDATE_SCRIPT="$PROJECT_DIR/scripts/auto_update.sh"
LOG_FILE="/var/log/clientst0r/triggered-update.log"

clientst0r_load_deployment_env "$PROJECT_DIR"

if [ ! -f "$TRIGGER_FILE" ]; then
    exit 0
fi

if ! clientst0r_auto_update_enabled; then
    echo "[$(date)] Trigger file found but auto-update execution is disabled. Set AUTO_UPDATE_ENABLED=True to opt in." >> "$LOG_FILE"
    rm -f "$TRIGGER_FILE"
    exit 0
fi

echo "[$(date)] Trigger file found, starting update..." >> "$LOG_FILE"
rm -f "$TRIGGER_FILE"

cd "$PROJECT_DIR" || exit 1
bash "$UPDATE_SCRIPT" >> "$LOG_FILE" 2>&1

echo "[$(date)] Update completed" >> "$LOG_FILE"
