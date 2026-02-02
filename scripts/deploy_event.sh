#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-event.local}"
USER_NAME="${USER_NAME:-event}"
PASS="${PASS:-}"

if [[ -z "${PASS}" ]]; then
  echo "PASS env var is required (Pi password)."
  echo "Example: PASS=event HOST=event.local ./scripts/deploy_event.sh"
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

scp_cmd() {
  sshpass -p "${PASS}" scp "$@"
}

ssh_cmd() {
  sshpass -p "${PASS}" ssh -o StrictHostKeyChecking=accept-new "${USER_NAME}@${HOST}" "$@"
}

echo "Deploying to ${USER_NAME}@${HOST}..."
scp_cmd "${ROOT_DIR}/server.py" "${USER_NAME}@${HOST}:/opt/pi-kiosk/server.py"
scp_cmd "${ROOT_DIR}/static/app.js" "${USER_NAME}@${HOST}:/opt/pi-kiosk/static/app.js"
scp_cmd "${ROOT_DIR}/static/style.css" "${USER_NAME}@${HOST}:/opt/pi-kiosk/static/style.css"
scp_cmd "${ROOT_DIR}/templates/index.html" "${USER_NAME}@${HOST}:/opt/pi-kiosk/templates/index.html"
scp_cmd "${ROOT_DIR}/templates/display.html" "${USER_NAME}@${HOST}:/opt/pi-kiosk/templates/display.html"

ssh_cmd "echo '${PASS}' | sudo -S systemctl restart pi-kiosk-server"
echo "Done."
