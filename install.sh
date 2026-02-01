#!/bin/bash
#
# Pi Kiosk Display Manager - Installation Script
#
# Usage:
#   Master (server + optional display):
#     sudo ./install.sh master [--hostname <name>]
#     curl -sSL http://<ip>:5000/install.sh | sudo bash -s -- master
#
#   Client (display only):
#     sudo ./install.sh client <master-ip> [--hostname <name>]
#     curl -sSL http://<master-ip>:5000/install.sh | sudo bash -s -- client <master-ip>
#

set -e

INSTALL_DIR="/opt/pi-kiosk"
SERVICE_USER="${SUDO_USER:-pi}"
MODE="${1:-master}"
MASTER_IP="${2:-localhost}"
HOSTNAME_OVERRIDE="${HOSTNAME_OVERRIDE:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKIP_UPDATES="${SKIP_UPDATES:-}"

# Allow passing hostname and skip-updates via flags to avoid sudo env issues
ARGS=("$@")
i=0
while [ $i -lt $# ]; do
    case "${ARGS[$i]}" in
        --hostname)
            HOSTNAME_OVERRIDE="${ARGS[$((i+1))]}"
            i=$((i+2))
            ;;
        --hostname=*)
            HOSTNAME_OVERRIDE="${ARGS[$i]#*=}"
            i=$((i+1))
            ;;
        --skip-updates)
            SKIP_UPDATES="1"
            i=$((i+1))
            ;;
        *)
            i=$((i+1))
            ;;
    esac
done

echo "========================================"
echo "Pi Kiosk Display Manager - Installer"
echo "========================================"
echo ""
echo "Mode: $MODE"
if [ "$MODE" = "client" ]; then
    echo "Master Server: $MASTER_IP"
fi
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (use sudo)"
    exit 1
fi

# Validate mode
if [ "$MODE" != "master" ] && [ "$MODE" != "client" ]; then
    echo "Invalid mode. Use 'master' or 'client'"
    echo ""
    echo "Usage:"
    echo "  Master: sudo ./install.sh master"
    echo "  Client: sudo ./install.sh client <master-ip>"
    exit 1
fi

# For client mode, require master IP
if [ "$MODE" = "client" ] && [ "$MASTER_IP" = "localhost" ]; then
    echo "Client mode requires master server IP"
    echo ""
    echo "Usage: sudo ./install.sh client <master-ip>"
    exit 1
fi

# Check if running on Raspberry Pi
if ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null && ! grep -q "BCM" /proc/cpuinfo 2>/dev/null; then
    echo "Warning: This doesn't appear to be a Raspberry Pi"
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo "[1/8] Updating system packages..."
apt-get update
if [ -z "$SKIP_UPDATES" ]; then
    apt-get -y full-upgrade
fi

echo "[2/8] Installing system dependencies..."
apt-get install -y chromium unclutter xdotool scrot imagemagick x11-apps python3 python3-pip python3-venv xserver-xorg x11-xserver-utils openbox

# Install wtype for Wayland support (optional, may not be in all repos)
apt-get install -y wtype 2>/dev/null || echo "Note: wtype not available (Wayland support). X11 will be used."

echo ""
echo "[3/8] Creating installation directory..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/uploads"
mkdir -p "$INSTALL_DIR/templates"
mkdir -p "$INSTALL_DIR/static"

# Check if we have local files or need to create them
if [ -f "$SCRIPT_DIR/server.py" ]; then
    cp -a "$SCRIPT_DIR/." "$INSTALL_DIR/" 2>/dev/null || true
else
    # Create kiosk.py inline for remote install (tab-based xiosk-style version)
    # Updated with scheduling support and fallback image features
    cat > "$INSTALL_DIR/kiosk.py" << 'KIOSKEOF'
#!/usr/bin/env python3
"""Pi Kiosk - Tab-Based Edition (xiosk-style)
Opens all URLs as browser tabs and uses Ctrl+Tab to rotate.
Includes scheduling support and fallback to default image."""
import argparse, os, shutil, signal, socket, subprocess, sys, threading, time
import urllib.request
import socketio

DEFAULT_SERVER_URL = 'http://localhost:5000'
DISPLAY = os.environ.get('DISPLAY', ':0')
PROFILE_DIR = os.path.expanduser('~/.config/pi-kiosk/chromium-profile')

def find_chromium():
    for cmd in ('chromium', 'chromium-browser'):
        if shutil.which(cmd): return cmd
    return 'chromium'

CHROMIUM_CMD = find_chromium()
browser_process = None
pages = []
current_index = 0
browser_ready = False
paused = False
running = True
server_url = DEFAULT_SERVER_URL
sio = socketio.Client(reconnection=True, reconnection_attempts=0, reconnection_delay=1)
page_switch_counts = {}  # page_id -> count since last refresh

def get_hostname(): return socket.gethostname()
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
        return ip
    except: return "127.0.0.1"

def log(msg): print(f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] {msg}')
def get_current_page(): return pages[current_index] if pages and current_index < len(pages) else None
def build_url(path): return path if path.startswith('http') else f"{server_url}{path}"

def send_status():
    page = get_current_page()
    try: sio.emit('kiosk_status', {'hostname': get_hostname(), 'ip': get_local_ip(), 'current_page_id': page['id'] if page else None, 'current_url': page['url'] if page else None, 'paused': paused, 'current_index': current_index, 'total_pages': len(pages)})
    except Exception as e: log(f'Error sending status: {e}')

def clear_profile_locks():
    for name in ('SingletonLock', 'SingletonSocket', 'SingletonCookie'):
        try: os.path.exists(os.path.join(PROFILE_DIR, name)) and os.remove(os.path.join(PROFILE_DIR, name))
        except: pass

def kill_browser():
    global browser_process
    if browser_process:
        try: browser_process.terminate(); browser_process.wait(timeout=5)
        except subprocess.TimeoutExpired: browser_process.kill()
        except: pass
        browser_process = None
    try: subprocess.run(['pkill', '-f', 'chromium.*--kiosk'], capture_output=True, timeout=5)
    except: pass

def send_keystroke(keys):
    """Send keystroke via xdotool (X11) or wtype (Wayland)."""
    env = {**os.environ, 'DISPLAY': DISPLAY}
    if shutil.which('xdotool'):
        try:
            subprocess.run(['xdotool', 'search', '--name', 'Chromium', 'windowactivate', '--sync'], env=env, capture_output=True, timeout=2)
            r = subprocess.run(['xdotool', 'key', '--clearmodifiers', keys], env=env, capture_output=True, timeout=2)
            if r.returncode == 0: log(f'Sent: {keys}'); return True
        except: pass
    if shutil.which('wtype'):
        try:
            args = []
            parts = keys.lower().split('+')
            for p in parts[:-1]:
                if p == 'ctrl': args.extend(['-M', 'ctrl'])
                elif p == 'shift': args.extend(['-M', 'shift'])
            key = parts[-1]
            if key == 'tab': args.extend(['-P', 'Tab'])
            elif key == 'r': args.extend(['-P', 'r'])
            elif key.isdigit(): args.extend(['-P', key])
            for p in parts[:-1]:
                if p == 'ctrl': args.extend(['-m', 'ctrl'])
                elif p == 'shift': args.extend(['-m', 'shift'])
            r = subprocess.run(['wtype'] + args, capture_output=True, timeout=2)
            if r.returncode == 0: log(f'Sent via wtype: {keys}'); return True
        except: pass
    return False

def launch_browser_with_tabs(urls):
    """Launch Chromium with all URLs as tabs."""
    global browser_process, current_index, browser_ready
    if not urls: urls = [f'{server_url}/static/default.png']
    browser_ready = False
    log(f'Launching browser with {len(urls)} tabs')
    kill_browser()
    os.makedirs(PROFILE_DIR, exist_ok=True)
    clear_profile_locks()
    cmd = [CHROMIUM_CMD, '--kiosk', '--noerrdialogs', '--disable-infobars', '--disable-session-crashed-bubble',
           '--no-first-run', '--start-fullscreen', '--autoplay-policy=no-user-gesture-required',
           '--check-for-update-interval=31536000', '--disable-features=TranslateUI', '--disable-component-update',
           f'--user-data-dir={PROFILE_DIR}', '--password-store=basic', '--disable-background-networking',
           '--disable-gpu-compositing', '--disable-software-rasterizer', '--disable-dev-shm-usage',
           '--disable-breakpad', '--disable-hang-monitor', '--memory-pressure-off',
           '--max_old_space_size=256', '--js-flags=--max-old-space-size=256'] + urls
    try:
        browser_process = subprocess.Popen(cmd, env={**os.environ, 'DISPLAY': DISPLAY},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        log(f'Browser PID: {browser_process.pid}')
        time.sleep(3)
        send_keystroke('ctrl+1')
        current_index = 0
        browser_ready = True
        return True
    except Exception as e:
        log(f'Error: {e}')
        return False

def get_enabled_urls():
    """Get list of URLs from enabled pages."""
    urls = []
    for page in pages:
        if page.get('enabled', True):
            urls.append(build_url(page['url']))
    # Fallback to default image if no pages available
    if not urls:
        urls = [f'{server_url}/static/default.png']
    return urls

def switcher_thread():
    """Background thread that rotates tabs."""
    global current_index, page_switch_counts, browser_ready
    log('Switcher started')
    while running:
        if paused or not pages or not browser_ready:
            time.sleep(0.5); continue
        page = get_current_page()
        if not page: time.sleep(1); continue
        duration = page.get('duration', 30)
        start = time.monotonic()
        while running and not paused:
            elapsed = time.monotonic() - start
            if elapsed >= duration:
                break
            time.sleep(min(0.1, duration - elapsed))
        if running and not paused and pages:
            send_keystroke('ctrl+Tab')
            current_index = (current_index + 1) % len(pages)
            new_page = get_current_page()
            if new_page and new_page.get('refresh'):
                page_id = new_page.get('id')
                refresh_interval = new_page.get('refresh_interval', 1)
                if page_id not in page_switch_counts: page_switch_counts[page_id] = 0
                page_switch_counts[page_id] += 1
                if page_switch_counts[page_id] >= refresh_interval:
                    time.sleep(0.3)
                    send_keystroke('ctrl+r')
                    log(f'Auto-refreshed: {new_page.get("name", "unnamed")} (after {refresh_interval} switches)')
                    page_switch_counts[page_id] = 0
            send_status()

@sio.event
def connect():
    log('Connected')
    sio.emit('kiosk_connect', {'hostname': get_hostname(), 'ip': get_local_ip()})
    sio.emit('request_pages', {'hostname': get_hostname()})

@sio.event
def disconnect(): log('Disconnected')

@sio.on('pages_list')
def on_pages_list(data):
    global pages, current_index
    log(f'Received {len(data)} pages')
    pages = data
    current_index = 0
    # Get URLs and launch browser (get_enabled_urls handles fallback)
    urls = get_enabled_urls()
    launch_browser_with_tabs(urls)
    if pages:
        send_status()
    else:
        log('No pages configured, showing default image')

@sio.on('pages_updated')
def on_pages_updated(data): sio.emit('request_pages', {'hostname': get_hostname()})

@sio.on('control')
def on_control(data):
    global paused, current_index
    action = data.get('action')
    log(f'Control: {action}')
    if action == 'pause': paused = True; send_status()
    elif action == 'resume': paused = False; send_status()
    elif action == 'next': send_keystroke('ctrl+Tab'); current_index = (current_index + 1) % len(pages) if pages else 0; send_status()
    elif action == 'prev': send_keystroke('ctrl+shift+Tab'); current_index = (current_index - 1) % len(pages) if pages else 0; send_status()
    elif action == 'refresh': send_keystroke('ctrl+r')
    elif action == 'goto':
        page_id = data.get('page_id')
        if page_id:
            for i, p in enumerate(pages):
                if p['id'] == page_id:
                    if 1 <= i+1 <= 9: send_keystroke(f'ctrl+{i+1}'); current_index = i
                    send_status(); break
    elif action == 'login_mode':
        paused = True
        try: subprocess.run(['pkill', 'unclutter'], capture_output=True, timeout=2)
        except: pass
        send_status()
    elif action == 'exit_login_mode':
        try: subprocess.Popen(['unclutter', '-idle', '0.1', '-root'], env={**os.environ, 'DISPLAY': DISPLAY}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except: pass
        send_status()

def signal_handler(sig, frame):
    global running
    log('Shutting down...')
    running = False
    kill_browser()
    try: sio.disconnect()
    except: pass
    sys.exit(0)

def wait_for_display(timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        try:
            if subprocess.run(['xset', 'q'], env={**os.environ, 'DISPLAY': DISPLAY}, capture_output=True, timeout=2).returncode == 0:
                return True
        except: pass
        time.sleep(1)
    return False

def wait_for_server(timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(f'{server_url}/api/status', timeout=5) as resp:
                if resp.status == 200: return True
        except: pass
        time.sleep(2)
    return False

def main():
    global server_url, DISPLAY, running
    parser = argparse.ArgumentParser()
    parser.add_argument('--server', '-s', default=DEFAULT_SERVER_URL)
    parser.add_argument('--display', '-d', default=DISPLAY)
    args = parser.parse_args()
    server_url = args.server
    DISPLAY = args.display
    log('Pi Kiosk starting (tab-based mode)...')
    log(f'Hostname: {get_hostname()}')
    log(f'Server: {server_url}')
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    log('Waiting for display...')
    if not wait_for_display(): log('Display timeout')
    try: subprocess.Popen(['unclutter', '-idle', '0.1', '-root'], env={**os.environ, 'DISPLAY': DISPLAY}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except: pass
    for cmd in [['xset', 's', 'off'], ['xset', '-dpms'], ['xset', 's', 'noblank']]:
        try: subprocess.run(cmd, env={**os.environ, 'DISPLAY': DISPLAY}, capture_output=True)
        except: pass
    log('Waiting for server...')
    while running and not wait_for_server(timeout=10): log('Server not ready...')
    if not running: return
    log('Connecting...')
    while running:
        try: sio.connect(server_url, wait_timeout=10); break
        except Exception as e: log(f'Connection failed: {e}'); time.sleep(5)
    threading.Thread(target=switcher_thread, daemon=True).start()
    last_crash = 0
    while running:
        try:
            if browser_process and browser_process.poll() is not None:
                now = time.time()
                if now - last_crash < 10: time.sleep(10)
                last_crash = now
                log('Browser crashed, relaunching...')
                urls = get_enabled_urls()
                launch_browser_with_tabs(urls)
            sio.sleep(2)
        except KeyboardInterrupt: break
        except Exception as e: log(f'Error: {e}'); time.sleep(5)
    signal_handler(None, None)

if __name__ == '__main__': main()
KIOSKEOF

    cat > "$INSTALL_DIR/requirements.txt" << 'EOF'
python-socketio==5.10.0
EOF
fi

if [ "$MODE" = "master" ] && [ ! -f "$INSTALL_DIR/server.py" ]; then
    echo "Error: server.py missing in $INSTALL_DIR (file copy failed)"
    exit 1
fi

# Ensure ownership for runtime writes (pages.db, uploads, screenshots)
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

echo ""
echo "[4/8] Setting up Python virtual environment..."
cd "$INSTALL_DIR"
sudo -u "$SERVICE_USER" python3 -m venv venv
sudo -u "$SERVICE_USER" ./venv/bin/pip install --upgrade pip

if [ "$MODE" = "master" ]; then
    sudo -u "$SERVICE_USER" ./venv/bin/pip install flask flask-socketio python-socketio gevent gevent-websocket requests websocket-client
else
    sudo -u "$SERVICE_USER" ./venv/bin/pip install python-socketio websocket-client requests
fi

echo ""
echo "[5/8] Installing systemd services..."

if [ "$MODE" = "master" ]; then
    cat > /etc/systemd/system/pi-kiosk-server.service << EOF
[Unit]
Description=Pi Kiosk Web Server
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python server.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
fi

SERVER_ARG=""
[ "$MODE" = "client" ] && SERVER_ARG="--server http://$MASTER_IP:5000"

cat > /etc/systemd/system/pi-kiosk.service << EOF
[Unit]
Description=Pi Kiosk Display
After=graphical.target
$([ "$MODE" = "master" ] && echo "After=pi-kiosk-server.service
Wants=pi-kiosk-server.service")

[Service]
Type=simple
User=$SERVICE_USER
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/$SERVICE_USER/.Xauthority
WorkingDirectory=$INSTALL_DIR
ExecStartPre=/bin/sleep 5
ExecStart=$INSTALL_DIR/venv/bin/python kiosk.py $SERVER_ARG
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=graphical.target
EOF

systemctl daemon-reload

echo ""
echo "[4.5/8] Configuring sudo permissions..."
SUDOERS_FILE="/etc/sudoers.d/pi-kiosk"
cat > "$SUDOERS_FILE" << EOF
$SERVICE_USER ALL=NOPASSWD: /usr/bin/hostnamectl, /usr/bin/sed, /usr/bin/tee, /usr/bin/nmcli, /opt/pi-kiosk/update.sh, /bin/systemctl restart pi-kiosk, /bin/systemctl restart pi-kiosk-server, /sbin/reboot, /usr/sbin/reboot, /bin/systemctl reboot, /usr/bin/systemctl reboot
EOF
chmod 440 "$SUDOERS_FILE"

echo ""
echo "[6/8] Configuring auto-login..."
mkdir -p /etc/systemd/system/getty@tty1.service.d/
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf << EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $SERVICE_USER --noclear %I \$TERM
EOF

BASHRC="/home/$SERVICE_USER/.bashrc"
grep -q "pi-kiosk auto-start" "$BASHRC" 2>/dev/null || cat >> "$BASHRC" << 'EOF'

# pi-kiosk auto-start
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
    startx -- -nocursor &>/dev/null
fi
EOF

cat > "/home/$SERVICE_USER/.xinitrc" << 'EOF'
#!/bin/bash
xset s off; xset -dpms; xset s noblank
exec openbox-session &
sleep 2; wait
EOF
chown "$SERVICE_USER:$SERVICE_USER" "/home/$SERVICE_USER/.xinitrc"
chmod +x "/home/$SERVICE_USER/.xinitrc"

echo ""
echo "[7/8] Configuring hardware watchdog..."
# Enable hardware watchdog to auto-reboot if system hangs
if [ -e /dev/watchdog ]; then
    apt-get install -y watchdog

    # Configure watchdog
    cat > /etc/watchdog.conf << 'WATCHDOGEOF'
# Hardware watchdog configuration
watchdog-device = /dev/watchdog
watchdog-timeout = 15
max-load-1 = 24
min-memory = 1
WATCHDOGEOF

    # Enable watchdog service
    systemctl enable watchdog
    systemctl start watchdog || true
    echo "Hardware watchdog enabled - system will auto-reboot if unresponsive"
else
    echo "Hardware watchdog not available on this device"
fi

echo ""
echo "[8/9] Disabling screen blanking..."
mkdir -p /etc/X11/xorg.conf.d/
cat > /etc/X11/xorg.conf.d/10-blanking.conf << 'EOF'
Section "ServerFlags"
    Option "BlankTime" "0"
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime" "0"
EndSection
EOF

echo ""
echo "[8.5/9] Applying kiosk power settings..."
# Prevent console blanking
if [ -f /boot/firmware/cmdline.txt ]; then
    sed -i 's/ consoleblank=[0-9]\+//g' /boot/firmware/cmdline.txt
    sed -i 's/$/ consoleblank=0/' /boot/firmware/cmdline.txt
elif [ -f /boot/cmdline.txt ]; then
    sed -i 's/ consoleblank=[0-9]\+//g' /boot/cmdline.txt
    sed -i 's/$/ consoleblank=0/' /boot/cmdline.txt
fi

# Disable sleep targets
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target 2>/dev/null || true

echo ""
echo "[9/9] Enabling services..."
[ "$MODE" = "master" ] && systemctl enable pi-kiosk-server.service
systemctl enable pi-kiosk.service

echo ""
echo "[10/10] Setting hostname..."
if [ -n "$HOSTNAME_OVERRIDE" ]; then
    NEW_HOSTNAME="$HOSTNAME_OVERRIDE"
elif [ "$MODE" = "master" ]; then
    NEW_HOSTNAME="pi-kiosk-master"
else
    NEW_HOSTNAME="pi-kiosk-$(cat /dev/urandom | tr -dc 'a-z0-9' | fold -w 4 | head -n 1)"
fi
if [ -n "$NEW_HOSTNAME" ]; then
    if hostnamectl set-hostname "$NEW_HOSTNAME"; then
        echo "$NEW_HOSTNAME" > /etc/hostname
        hostname "$NEW_HOSTNAME" || true
        if grep -q "^127.0.1.1" /etc/hosts; then
            sed -i "s/^127.0.1.1.*/127.0.1.1\t$NEW_HOSTNAME/" /etc/hosts
        else
            echo -e "127.0.1.1\t$NEW_HOSTNAME" >> /etc/hosts
        fi
        if [ -f /etc/cloud/cloud.cfg ]; then
            sed -i 's/^preserve_hostname:.*/preserve_hostname: true/' /etc/cloud/cloud.cfg
            grep -q '^preserve_hostname:' /etc/cloud/cloud.cfg || echo 'preserve_hostname: true' >> /etc/cloud/cloud.cfg
        fi
        # Clear Chromium profile locks after hostname change to prevent lockout
        PROFILE_DIR="/home/$SERVICE_USER/.config/pi-kiosk/chromium-profile"
        if [ -d "$PROFILE_DIR" ]; then
            echo "Clearing Chromium profile locks after hostname change..."
            rm -f "$PROFILE_DIR/SingletonLock" "$PROFILE_DIR/SingletonSocket" "$PROFILE_DIR/SingletonCookie"
        fi
    else
        echo "Error: failed to set hostname to '$NEW_HOSTNAME'"
        exit 1
    fi
fi

echo "Hostname now: $(hostname)"

echo ""
echo "========================================"
echo "Installation Complete!"
echo "========================================"
echo ""
echo "Mode: $MODE"
echo "Hostname: $NEW_HOSTNAME"
echo ""

if [ "$MODE" = "master" ]; then
    IP=$(hostname -I | awk '{print $1}')
    echo "Web interface: http://$IP:5000"
    echo ""
    echo "To add client displays:"
    echo "  curl -sSL http://$IP:5000/install.sh | sudo bash -s -- client $IP"
fi
echo ""
echo "Reboot to start: sudo reboot"
echo ""
