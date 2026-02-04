#!/bin/bash
#
# Pi Kiosk Installer for Mac
# Double-click this file to install Pi Kiosk on your Raspberry Pi via SSH
#

clear
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           Pi Kiosk Display Manager - SSH Installer           ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored text
print_color() {
    echo -e "${1}${2}${NC}"
}

# Check if SSH is available
if ! command -v ssh &> /dev/null; then
    print_color $RED "Error: SSH is not available on this system"
    exit 1
fi
if ! command -v scp &> /dev/null; then
    print_color $RED "Error: SCP is not available on this system"
    exit 1
fi

# Function to clear old SSH host key and accept new one
clear_ssh_host_key() {
    local host="$1"
    # Remove any existing host key for this host
    ssh-keygen -R "$host" 2>/dev/null || true
    # Also try IP if it's a hostname
    if [[ ! "$host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        # It's a hostname, try to resolve and remove IP too
        local ip=$(getent hosts "$host" 2>/dev/null | awk '{print $1}' | head -1)
        [ -n "$ip" ] && ssh-keygen -R "$ip" 2>/dev/null || true
    fi
}

# SSH options to handle host key changes gracefully
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=~/.ssh/known_hosts"
SCP_OPTS="-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=~/.ssh/known_hosts"

# Get Pi connection details
echo ""
print_color $BLUE "Enter your Raspberry Pi connection details:"
echo ""

read -p "Pi IP address or hostname: " PI_HOST
if [ -z "$PI_HOST" ]; then
    print_color $RED "Error: IP address is required"
    read -p "Press Enter to exit..."
    exit 1
fi

read -p "Pi username [pi]: " PI_USER
PI_USER=${PI_USER:-pi}

echo ""
read -p "Optional hostname (leave blank for default): " HOSTNAME_OVERRIDE

echo ""
read -p "Skip system updates? (y/n) [n]: " SKIP_UPDATES_CHOICE
SKIP_UPDATES_CHOICE=${SKIP_UPDATES_CHOICE:-n}

echo ""
print_color $YELLOW "Enable Tailscale for remote access?"
echo "  Allows you to manage displays from anywhere (home, travel, etc.)"
echo "  Recommended for remote admin access"
echo ""
read -p "Enable Tailscale? (y/n) [y]: " ENABLE_TAILSCALE
ENABLE_TAILSCALE=${ENABLE_TAILSCALE:-y}

TAILSCALE_AUTHKEY=""
if [[ "$ENABLE_TAILSCALE" =~ ^[Yy]$ ]]; then
    echo ""
    print_color $BLUE "Optional: Provide Tailscale auth key for automatic setup"
    echo "  (Leave blank to authenticate manually via browser after install)"
    echo "  Get auth keys from: https://login.tailscale.com/admin/settings/keys"
    echo ""
    read -p "Tailscale auth key (optional): " TAILSCALE_AUTHKEY
fi

echo ""
print_color $YELLOW "Select installation mode:"
echo "  1) Master - Server + Display (first Pi)"
echo "  2) Client - Display only (connects to master)"
echo ""
read -p "Enter choice [1]: " MODE_CHOICE
MODE_CHOICE=${MODE_CHOICE:-1}

if [ "$MODE_CHOICE" = "1" ]; then
    INSTALL_MODE="master"
    MASTER_IP=""
elif [ "$MODE_CHOICE" = "2" ]; then
    INSTALL_MODE="client"
    echo ""
    read -p "Enter Master Pi IP address: " MASTER_IP
    if [ -z "$MASTER_IP" ]; then
        print_color $RED "Error: Master IP is required for client mode"
        read -p "Press Enter to exit..."
        exit 1
    fi
else
    print_color $RED "Invalid choice"
    read -p "Press Enter to exit..."
    exit 1
fi

echo ""
print_color $BLUE "Installation Summary:"
echo "  Pi Address: $PI_USER@$PI_HOST"
echo "  Mode: $INSTALL_MODE"
[ -n "$MASTER_IP" ] && echo "  Master IP: $MASTER_IP"
[ -n "$HOSTNAME_OVERRIDE" ] && echo "  Hostname: $HOSTNAME_OVERRIDE"
if [[ "$ENABLE_TAILSCALE" =~ ^[Yy]$ ]]; then
    print_color $GREEN "  Tailscale: ENABLED (remote access from anywhere)"
else
    echo "  Tailscale: Disabled (local network only)"
fi
echo ""

read -p "Proceed with installation? (y/n) [y]: " CONFIRM
CONFIRM=${CONFIRM:-y}

if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "Installation cancelled"
    exit 0
fi

echo ""
print_color $YELLOW "Connecting to Pi and starting installation..."
print_color $YELLOW "You may be prompted for your Pi password."
echo ""

# Clear any stale SSH host keys (common after Pi re-imaging)
print_color $BLUE "Clearing old SSH host keys for $PI_HOST..."
clear_ssh_host_key "$PI_HOST"

# Build the install command
if [ "$INSTALL_MODE" = "master" ]; then
    # Try to use local install.sh if available
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    if [ -f "$SCRIPT_DIR/install.sh" ]; then
        print_color $GREEN "Copying local pi-kiosk files to Pi..."
        LOCAL_TARBALL="$(mktemp -t pi-kiosk-XXXXXX).tgz"
        REMOTE_TARBALL="/tmp/pi-kiosk.tgz"
        REMOTE_DIR="/tmp/pi-kiosk"
        COPYFILE_DISABLE=1 tar --no-xattrs -czf "$LOCAL_TARBALL" -C "$SCRIPT_DIR" .
        scp $SCP_OPTS "$LOCAL_TARBALL" "$PI_USER@$PI_HOST:$REMOTE_TARBALL"
        rm -f "$LOCAL_TARBALL"
        HOSTNAME_ENV=""
        [ -n "$HOSTNAME_OVERRIDE" ] && HOSTNAME_ENV="HOSTNAME_OVERRIDE=$HOSTNAME_OVERRIDE"
        SSH_INSTALL_CMD="rm -rf $REMOTE_DIR; mkdir -p $REMOTE_DIR; tar --warning=no-unknown-keyword -xzf $REMOTE_TARBALL -C $REMOTE_DIR; "
        INSTALL_FLAGS=""
        if [[ "$SKIP_UPDATES_CHOICE" =~ ^[Yy]$ ]]; then
            INSTALL_FLAGS="$INSTALL_FLAGS --skip-updates"
        fi
        if [ -n "$HOSTNAME_OVERRIDE" ]; then
            INSTALL_FLAGS="$INSTALL_FLAGS --hostname \"$HOSTNAME_OVERRIDE\""
        fi
        if [[ "$ENABLE_TAILSCALE" =~ ^[Yy]$ ]]; then
            INSTALL_FLAGS="$INSTALL_FLAGS --enable-tailscale"
        fi
        # Pass Tailscale auth key as environment variable if provided
        TAILSCALE_ENV=""
        if [ -n "$TAILSCALE_AUTHKEY" ]; then
            TAILSCALE_ENV="TAILSCALE_AUTHKEY='$TAILSCALE_AUTHKEY'"
        fi
        if [ -n "$INSTALL_FLAGS" ]; then
            SSH_INSTALL_CMD="${SSH_INSTALL_CMD}$TAILSCALE_ENV sudo -E bash $REMOTE_DIR/install.sh $INSTALL_MODE $INSTALL_FLAGS; "
        else
            SSH_INSTALL_CMD="${SSH_INSTALL_CMD}$TAILSCALE_ENV sudo -E bash $REMOTE_DIR/install.sh $INSTALL_MODE; "
        fi
        SSH_INSTALL_CMD="${SSH_INSTALL_CMD}STATUS=\$?; rm -rf $REMOTE_DIR $REMOTE_TARBALL; exit \$STATUS"
        ssh $SSH_OPTS -t "$PI_USER@$PI_HOST" "$SSH_INSTALL_CMD"
    else
        print_color $YELLOW "Local install.sh not found, using embedded installer..."
        ssh $SSH_OPTS -t "$PI_USER@$PI_HOST" 'bash -s' << 'REMOTE_INSTALL'
#!/bin/bash
set -e
INSTALL_DIR="/opt/pi-kiosk"
SERVICE_USER="${SUDO_USER:-pi}"
MODE="master"

echo "Installing Pi Kiosk (Master mode)..."

# Check root
if [ "$EUID" -ne 0 ]; then
    echo "Re-running with sudo..."
    exec sudo bash "$0" "$@"
fi

apt-get update
apt-get install -y chromium-browser unclutter xdotool python3 python3-pip python3-venv xserver-xorg x11-xserver-utils openbox

mkdir -p "$INSTALL_DIR"/{uploads,templates,static}

# Download files from GitHub or create minimal setup
echo "Setting up Pi Kiosk files..."

python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install flask flask-socketio python-socketio gevent gevent-websocket

echo "Installation requires the full pi-kiosk files."
echo "Please copy the pi-kiosk folder to your Pi and run: sudo ./install.sh master"
REMOTE_INSTALL
    fi
else
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    if [ -f "$SCRIPT_DIR/install.sh" ]; then
        print_color $GREEN "Copying local pi-kiosk files to Pi..."
        LOCAL_TARBALL="$(mktemp -t pi-kiosk-XXXXXX).tgz"
        REMOTE_TARBALL="/tmp/pi-kiosk.tgz"
        REMOTE_DIR="/tmp/pi-kiosk"
        COPYFILE_DISABLE=1 tar --no-xattrs -czf "$LOCAL_TARBALL" -C "$SCRIPT_DIR" .
        scp $SCP_OPTS "$LOCAL_TARBALL" "$PI_USER@$PI_HOST:$REMOTE_TARBALL"
        rm -f "$LOCAL_TARBALL"
        HOSTNAME_ENV=""
        [ -n "$HOSTNAME_OVERRIDE" ] && HOSTNAME_ENV="HOSTNAME_OVERRIDE=$HOSTNAME_OVERRIDE"
        SSH_INSTALL_CMD="rm -rf $REMOTE_DIR; mkdir -p $REMOTE_DIR; tar --warning=no-unknown-keyword -xzf $REMOTE_TARBALL -C $REMOTE_DIR; "
        INSTALL_FLAGS=""
        if [[ "$SKIP_UPDATES_CHOICE" =~ ^[Yy]$ ]]; then
            INSTALL_FLAGS="$INSTALL_FLAGS --skip-updates"
        fi
        if [ -n "$HOSTNAME_OVERRIDE" ]; then
            INSTALL_FLAGS="$INSTALL_FLAGS --hostname \"$HOSTNAME_OVERRIDE\""
        fi
        if [[ "$ENABLE_TAILSCALE" =~ ^[Yy]$ ]]; then
            INSTALL_FLAGS="$INSTALL_FLAGS --enable-tailscale"
        fi
        # Pass Tailscale auth key as environment variable if provided
        TAILSCALE_ENV=""
        if [ -n "$TAILSCALE_AUTHKEY" ]; then
            TAILSCALE_ENV="TAILSCALE_AUTHKEY='$TAILSCALE_AUTHKEY'"
        fi
        if [ -n "$INSTALL_FLAGS" ]; then
            SSH_INSTALL_CMD="${SSH_INSTALL_CMD}$TAILSCALE_ENV sudo -E bash $REMOTE_DIR/install.sh $INSTALL_MODE $MASTER_IP $INSTALL_FLAGS; "
        else
            SSH_INSTALL_CMD="${SSH_INSTALL_CMD}$TAILSCALE_ENV sudo -E bash $REMOTE_DIR/install.sh $INSTALL_MODE $MASTER_IP; "
        fi
        SSH_INSTALL_CMD="${SSH_INSTALL_CMD}STATUS=\$?; rm -rf $REMOTE_DIR $REMOTE_TARBALL; exit \$STATUS"
        ssh $SSH_OPTS -t "$PI_USER@$PI_HOST" "$SSH_INSTALL_CMD"
    else
        print_color $YELLOW "Attempting to download from master Pi..."
        ssh $SSH_OPTS -t "$PI_USER@$PI_HOST" "curl -sSL http://$MASTER_IP:5000/install.sh | sudo bash -s -- client $MASTER_IP"
    fi
fi

RESULT=$?

echo ""
if [ $RESULT -eq 0 ]; then
    FINAL_HOSTNAME="$HOSTNAME_OVERRIDE"
    if [ -z "$FINAL_HOSTNAME" ]; then
        if [ "$INSTALL_MODE" = "master" ]; then
            FINAL_HOSTNAME="pi-kiosk-master"
        else
            FINAL_HOSTNAME="(assigned on device)"
        fi
    fi
    print_color $GREEN "╔══════════════════════════════════════════════════════════════╗"
    print_color $GREEN "║              Installation Complete!                          ║"
    print_color $GREEN "╚══════════════════════════════════════════════════════════════╝"
    echo ""

    if [[ "$ENABLE_TAILSCALE" =~ ^[Yy]$ ]]; then
        print_color $YELLOW "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        print_color $YELLOW "  TAILSCALE AUTHENTICATION REQUIRED"
        print_color $YELLOW "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        echo "After reboot, you need to authenticate Tailscale:"
        echo ""
        echo "  1. SSH to Pi: ssh $PI_USER@$PI_HOST"
        echo "  2. Run: sudo tailscale up"
        echo "  3. Open the authentication URL in your browser"
        echo "  4. Authorize the device in your Tailscale account"
        echo ""
        print_color $GREEN "Once authenticated, you can access from anywhere:"
        echo "  • Local: http://$PI_HOST:5000 (same network)"
        echo "  • Remote: http://100.x.x.x:5000 (Tailscale IP - shown after auth)"
        echo ""
        print_color $YELLOW "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
    fi

    if [ "$INSTALL_MODE" = "master" ]; then
        echo "Next steps:"
        echo "  1. Reboot your Pi: ssh $PI_USER@$PI_HOST 'sudo reboot'"
        if [ "$FINAL_HOSTNAME" = "(assigned on device)" ]; then
            echo "  2. Access web interface at: http://$PI_HOST:5000"
        else
            echo "  2. Access web interface at: http://$FINAL_HOSTNAME.local:5000 (after reboot)"
        fi
        if [[ "$ENABLE_TAILSCALE" =~ ^[Yy]$ ]]; then
            echo "  3. Complete Tailscale authentication (see above)"
        fi
        echo ""
        echo "To add more display clients, run this installer again"
        if [ "$FINAL_HOSTNAME" = "(assigned on device)" ]; then
            echo "and select 'Client' mode with Master IP: $PI_HOST"
        else
            echo "and select 'Client' mode with Master IP: $FINAL_HOSTNAME.local"
        fi
    else
        echo "Next steps:"
        echo "  1. Reboot your Pi: ssh $PI_USER@$PI_HOST 'sudo reboot'"
        echo "  2. The display will connect to: http://$MASTER_IP:5000"
        if [[ "$ENABLE_TAILSCALE" =~ ^[Yy]$ ]]; then
            echo "  3. Complete Tailscale authentication (see above)"
        fi
    fi

    echo ""
    read -p "Reboot the Pi now? (y/n) [y]: " REBOOT_CONFIRM
    REBOOT_CONFIRM=${REBOOT_CONFIRM:-y}
    if [[ "$REBOOT_CONFIRM" =~ ^[Yy]$ ]]; then
        print_color $YELLOW "Rebooting $PI_USER@$PI_HOST..."
        ssh $SSH_OPTS -t "$PI_USER@$PI_HOST" "sudo reboot"
    fi
else
    print_color $RED "Installation failed. Check the output above for errors."
fi

echo ""
read -p "Press Enter to exit..."
