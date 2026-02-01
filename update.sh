#!/bin/bash
set -e

INSTALL_DIR="/opt/pi-kiosk"
UPDATE_URL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)
      UPDATE_URL="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

if [[ -z "$UPDATE_URL" ]]; then
  echo "Error: --url is required"
  exit 1
fi

TMP_DIR="$(mktemp -d)"
ARCHIVE="$TMP_DIR/update.tgz"

echo "Downloading update from $UPDATE_URL"
curl -fsSL "$UPDATE_URL" -o "$ARCHIVE"

mkdir -p "$TMP_DIR/extract"
tar -xzf "$ARCHIVE" -C "$TMP_DIR/extract"

# Copy only code assets; preserve uploads, venv, db
rsync -a --delete \
  --exclude 'uploads/' \
  --exclude 'venv/' \
  --exclude 'pages.db' \
  "$TMP_DIR/extract/" "$INSTALL_DIR/"

echo "Update applied. Restarting services..."
systemctl restart pi-kiosk || true
systemctl restart pi-kiosk-server || true
echo "Done."
