#!/bin/bash
#
# Pi Kiosk SD Card Prep for Mac
# Double-click this file to prepare a mounted Raspberry Pi OS SD card for
# first-boot kiosk installation from GitHub.
#

set -euo pipefail

clear
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         Pi Kiosk Display Manager - SD Card Prep             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_color() {
    echo -e "${1}${2}${NC}"
}

pause_exit() {
    echo ""
    read -p "Press Enter to exit..."
    exit "${1:-0}"
}

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        print_color "$RED" "Error: '$1' is required but not installed."
        pause_exit 1
    fi
}

require_cmd python3
require_cmd tar

trim() {
    printf '%s' "$1" | awk '{$1=$1};1'
}

yaml_single_quote() {
    python3 - "$1" <<'PY'
import sys
value = sys.argv[1]
print("'" + value.replace("'", "''") + "'")
PY
}

parse_repo_slug() {
    python3 - "$1" <<'PY'
import sys
value = sys.argv[1].strip()
for prefix in ("https://github.com/", "http://github.com/"):
    if value.startswith(prefix):
        value = value[len(prefix):]
value = value.strip("/").removesuffix(".git")
parts = [p for p in value.split("/") if p]
if len(parts) != 2:
    raise SystemExit(1)
print("/".join(parts))
PY
}

indent_text() {
    local prefix="$1"
    while IFS= read -r line; do
        printf '%s%s\n' "$prefix" "$line"
    done
}

discover_boot_volumes() {
    find /Volumes -maxdepth 2 -name config.txt -print 2>/dev/null | while IFS= read -r path; do
        dirname "$path"
    done | sort -u
}

print_color "$BLUE" "Scanning /Volumes for Raspberry Pi boot partitions..."
mapfile -t BOOT_VOLUMES < <(discover_boot_volumes)

if [ "${#BOOT_VOLUMES[@]}" -eq 0 ]; then
    print_color "$RED" "No Raspberry Pi boot partition was found."
    echo "Expected a mounted volume under /Volumes containing config.txt."
    pause_exit 1
fi

echo ""
print_color "$YELLOW" "Select the mounted boot partition to prepare:"
for i in "${!BOOT_VOLUMES[@]}"; do
    volume="${BOOT_VOLUMES[$i]}"
    extra=""
    [ -f "$volume/user-data" ] && extra=" (cloud-init image)"
    echo "  $((i + 1))) $volume$extra"
done
echo ""
read -p "Enter choice [1]: " BOOT_CHOICE
BOOT_CHOICE=${BOOT_CHOICE:-1}

if ! [[ "$BOOT_CHOICE" =~ ^[0-9]+$ ]] || [ "$BOOT_CHOICE" -lt 1 ] || [ "$BOOT_CHOICE" -gt "${#BOOT_VOLUMES[@]}" ]; then
    print_color "$RED" "Invalid selection."
    pause_exit 1
fi

BOOT_DIR="${BOOT_VOLUMES[$((BOOT_CHOICE - 1))]}"
USER_DATA="$BOOT_DIR/user-data"
META_DATA="$BOOT_DIR/meta-data"
NETWORK_CONFIG="$BOOT_DIR/network-config"
SSH_SENTINEL="$BOOT_DIR/ssh"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"

if [ ! -f "$USER_DATA" ] || [ ! -f "$META_DATA" ]; then
    print_color "$RED" "This SD card does not expose cloud-init files on the boot partition."
    echo "This prep tool currently supports Raspberry Pi OS images that include:"
    echo "  - user-data"
    echo "  - meta-data"
    echo ""
    echo "Reflash with a current Raspberry Pi OS image in Raspberry Pi Imager, then try again."
    pause_exit 1
fi

echo ""
print_color "$BLUE" "Choose kiosk mode:"
echo "  1) Master - Server + Display"
echo "  2) Client - Display only"
echo ""
read -p "Enter choice [1]: " MODE_CHOICE
MODE_CHOICE=${MODE_CHOICE:-1}

if [ "$MODE_CHOICE" = "1" ]; then
    INSTALL_MODE="master"
    MASTER_HOST=""
elif [ "$MODE_CHOICE" = "2" ]; then
    INSTALL_MODE="client"
    read -p "Master Pi host/IP: " MASTER_HOST
    MASTER_HOST="$(trim "$MASTER_HOST")"
    if [ -z "$MASTER_HOST" ]; then
        print_color "$RED" "Master host is required for client mode."
        pause_exit 1
    fi
else
    print_color "$RED" "Invalid mode selection."
    pause_exit 1
fi

echo ""
read -p "Hostname [pi-kiosk-${INSTALL_MODE}]: " HOSTNAME_OVERRIDE
HOSTNAME_OVERRIDE="$(trim "${HOSTNAME_OVERRIDE:-pi-kiosk-${INSTALL_MODE}}")"

echo ""
read -p "WiFi SSID (leave blank to skip WiFi config): " WIFI_SSID
WIFI_SSID="$(trim "$WIFI_SSID")"
WIFI_PASSWORD=""
WIFI_COUNTRY="GB"
WIFI_HIDDEN="false"
if [ -n "$WIFI_SSID" ]; then
    read -s -p "WiFi password (leave blank for open network): " WIFI_PASSWORD
    echo ""
    read -p "WiFi country code [GB]: " WIFI_COUNTRY_INPUT
    WIFI_COUNTRY="$(trim "${WIFI_COUNTRY_INPUT:-GB}")"
    read -p "Hidden network? (y/n) [n]: " WIFI_HIDDEN_CHOICE
    WIFI_HIDDEN_CHOICE=${WIFI_HIDDEN_CHOICE:-n}
    if [[ "$WIFI_HIDDEN_CHOICE" =~ ^[Yy]$ ]]; then
        WIFI_HIDDEN="true"
    fi
fi

echo ""
read -p "GitHub repository (owner/repo or URL): " GITHUB_REPO_INPUT
GITHUB_REPO_INPUT="$(trim "$GITHUB_REPO_INPUT")"
if [ -z "$GITHUB_REPO_INPUT" ]; then
    print_color "$RED" "GitHub repository is required."
    pause_exit 1
fi

if ! GITHUB_REPO_SLUG="$(parse_repo_slug "$GITHUB_REPO_INPUT" 2>/dev/null)"; then
    print_color "$RED" "Repository must look like owner/repo or https://github.com/owner/repo"
    pause_exit 1
fi

read -p "GitHub branch [main]: " GITHUB_BRANCH_INPUT
GITHUB_BRANCH="$(trim "${GITHUB_BRANCH_INPUT:-main}")"

read -s -p "Optional GitHub token for private repos (leave blank for public): " GITHUB_TOKEN
echo ""
GITHUB_TOKEN="$(trim "$GITHUB_TOKEN")"

echo ""
read -p "Skip package updates during install? (y/n) [n]: " SKIP_UPDATES_CHOICE
SKIP_UPDATES_CHOICE=${SKIP_UPDATES_CHOICE:-n}
SKIP_UPDATES="0"
if [[ "$SKIP_UPDATES_CHOICE" =~ ^[Yy]$ ]]; then
    SKIP_UPDATES="1"
fi

echo ""
print_color "$BLUE" "Summary:"
echo "  Boot volume: $BOOT_DIR"
echo "  Mode: $INSTALL_MODE"
[ -n "$MASTER_HOST" ] && echo "  Master host: $MASTER_HOST"
echo "  Hostname: $HOSTNAME_OVERRIDE"
if [ -n "$WIFI_SSID" ]; then
    echo "  WiFi: $WIFI_SSID ($WIFI_COUNTRY)"
else
    echo "  WiFi: unchanged / skipped"
fi
echo "  GitHub repo: $GITHUB_REPO_SLUG"
echo "  GitHub branch: $GITHUB_BRANCH"
[ -n "$GITHUB_TOKEN" ] && echo "  GitHub token: provided"
[ "$SKIP_UPDATES" = "1" ] && echo "  Skip updates: yes"
echo ""
read -p "Write first-boot config to this SD card? (y/n) [y]: " CONFIRM
CONFIRM=${CONFIRM:-y}
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    pause_exit 0
fi

cp "$USER_DATA" "$USER_DATA.pi-kiosk-backup-$TIMESTAMP"
[ -f "$NETWORK_CONFIG" ] && cp "$NETWORK_CONFIG" "$NETWORK_CONFIG.pi-kiosk-backup-$TIMESTAMP"

touch "$SSH_SENTINEL"

BOOTSTRAP_SCRIPT=$(cat <<EOF
#!/bin/bash
set -euxo pipefail
exec > >(tee -a /var/log/pi-kiosk-bootstrap.log) 2>&1

MODE=$(yaml_single_quote "$INSTALL_MODE")
MASTER_HOST=$(yaml_single_quote "$MASTER_HOST")
HOSTNAME_OVERRIDE=$(yaml_single_quote "$HOSTNAME_OVERRIDE")
GITHUB_REPO=$(yaml_single_quote "$GITHUB_REPO_SLUG")
GITHUB_BRANCH=$(yaml_single_quote "$GITHUB_BRANCH")
GITHUB_TOKEN=$(yaml_single_quote "$GITHUB_TOKEN")
SKIP_UPDATES=$(yaml_single_quote "$SKIP_UPDATES")

WORKDIR="\$(mktemp -d /tmp/pi-kiosk-bootstrap.XXXXXX)"
ARCHIVE="\$WORKDIR/repo.tgz"
cleanup() {
    rm -rf "\$WORKDIR"
}
trap cleanup EXIT

if [ -n "\$GITHUB_TOKEN" ]; then
    curl -fsSL \\
      -H "Authorization: Bearer \$GITHUB_TOKEN" \\
      -H "Accept: application/vnd.github+json" \\
      "https://api.github.com/repos/\$GITHUB_REPO/tarball/\$GITHUB_BRANCH" \\
      -o "\$ARCHIVE"
else
    curl -fsSL \\
      "https://github.com/\$GITHUB_REPO/archive/refs/heads/\$GITHUB_BRANCH.tar.gz" \\
      -o "\$ARCHIVE"
fi

tar -xzf "\$ARCHIVE" -C "\$WORKDIR"
SRC_DIR="\$(find "\$WORKDIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [ ! -f "\$SRC_DIR/install.sh" ]; then
    echo "install.sh was not found in the downloaded repository."
    exit 1
fi

chmod +x "\$SRC_DIR/install.sh"

ARGS=( "\$MODE" )
if [ "\$MODE" = "client" ]; then
    ARGS+=( "\$MASTER_HOST" )
fi
if [ -n "\$HOSTNAME_OVERRIDE" ]; then
    ARGS+=( --hostname "\$HOSTNAME_OVERRIDE" )
fi
if [ "\$SKIP_UPDATES" = "1" ]; then
    ARGS+=( --skip-updates )
fi

bash "\$SRC_DIR/install.sh" "\${ARGS[@]}"

touch /boot/firmware/pi-kiosk-firstboot-complete 2>/dev/null || true
touch /boot/pi-kiosk-firstboot-complete 2>/dev/null || true
EOF
)

BOOTSTRAP_INDENTED="$(printf '%s\n' "$BOOTSTRAP_SCRIPT" | indent_text '      ')"
EXISTING_USER_DATA_CONTENT="$(cat "$USER_DATA")"
USER_DATA_HEADER=""
if ! grep -q '^#cloud-config' "$USER_DATA"; then
    USER_DATA_HEADER="#cloud-config"$'\n'
fi

cat > "$USER_DATA" <<EOF
${USER_DATA_HEADER}${EXISTING_USER_DATA_CONTENT}

# Added by Pi SD Card Prep.command on $TIMESTAMP
hostname: $(yaml_single_quote "$HOSTNAME_OVERRIDE")
manage_etc_hosts: true
ssh_pwauth: true
write_files:
  - path: /usr/local/bin/pi-kiosk-bootstrap.sh
    permissions: '0755'
    owner: root:root
    content: |
$BOOTSTRAP_INDENTED
runcmd:
  - [ bash, -lc, "/usr/local/bin/pi-kiosk-bootstrap.sh" ]
EOF

if [ -n "$WIFI_SSID" ]; then
    WIFI_PASSWORD_LINE=""
    [ -n "$WIFI_PASSWORD" ] && WIFI_PASSWORD_LINE="            password: $(yaml_single_quote "$WIFI_PASSWORD")"
    cat > "$NETWORK_CONFIG" <<EOF
network:
  version: 2
  wifis:
    renderer: NetworkManager
    wlan0:
      dhcp4: true
      dhcp6: false
      optional: true
      regulatory-domain: $(yaml_single_quote "$WIFI_COUNTRY")
      access-points:
        $(yaml_single_quote "$WIFI_SSID"):
$WIFI_PASSWORD_LINE
          hidden: $WIFI_HIDDEN
EOF
else
    print_color "$YELLOW" "WiFi config left unchanged."
fi

cat > "$BOOT_DIR/pi-kiosk-firstboot.txt" <<EOF
Pi Kiosk first-boot install prepared on $TIMESTAMP
Mode: $INSTALL_MODE
Hostname: $HOSTNAME_OVERRIDE
GitHub repo: $GITHUB_REPO_SLUG
GitHub branch: $GITHUB_BRANCH
WiFi configured: $( [ -n "$WIFI_SSID" ] && echo "yes" || echo "no" )
EOF

echo ""
print_color "$GREEN" "SD card prepared successfully."
echo "Files written:"
echo "  $USER_DATA"
[ -n "$WIFI_SSID" ] && echo "  $NETWORK_CONFIG"
echo "  $SSH_SENTINEL"
echo "  $BOOT_DIR/pi-kiosk-firstboot.txt"
echo ""
print_color "$YELLOW" "Next steps:"
echo "  1. Eject the SD card."
echo "  2. Insert it into the Pi and boot."
echo "  3. Give it a few minutes to download from GitHub and run install.sh."
echo "  4. Check /var/log/pi-kiosk-bootstrap.log on the Pi if first boot fails."
echo ""
print_color "$YELLOW" "Note:"
echo "  This prep flow targets Raspberry Pi OS images that expose cloud-init files"
echo "  on the boot partition (user-data/meta-data). It does not modify the Linux"
echo "  root partition directly from macOS."

pause_exit 0
