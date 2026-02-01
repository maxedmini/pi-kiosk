#!/usr/bin/env python3
"""
Pi Kiosk Display Manager - Tab-Based Edition (xiosk-style)

Reliable kiosk mode using browser tabs:
- Opens all URLs as separate browser tabs at startup
- Uses Ctrl+Tab keystroke to rotate between tabs
- No CDP/WebSocket complexity - just keyboard input
- Proven reliable approach (same as xiosk)
"""

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import requests
import json
import base64
import websocket

import socketio

# Configuration
DEFAULT_SERVER_URL = 'http://localhost:5000'
DISPLAY = os.environ.get('DISPLAY', ':0')
PROFILE_DIR = os.path.expanduser('~/.config/pi-kiosk/chromium-profile')
SCREENSHOT_ENABLED = os.environ.get('PI_KIOSK_SCREENSHOT', '1').lower() not in ('0', 'false', 'no')
SCREENSHOT_SIZE = os.environ.get('PI_KIOSK_SCREENSHOT_SIZE', '640x360')
SCREENSHOT_PATH = '/tmp/pi-kiosk-shot.jpg'
SCREENSHOT_INTERVAL_SEC = 3
SCREENSHOT_FORCE_SOFTWARE = os.environ.get('PI_KIOSK_SCREENSHOT_SOFTWARE', '1').lower() in ('1', 'true', 'yes')


def find_chromium_cmd():
    """Find the chromium binary."""
    for cmd in ('chromium', 'chromium-browser'):
        if shutil.which(cmd):
            return cmd
    return 'chromium'


CHROMIUM_CMD = find_chromium_cmd()

# Global state
browser_process = None
pages = []
current_index = 0
paused = False
running = True
server_url = DEFAULT_SERVER_URL
sio = socketio.Client(reconnection=True, reconnection_attempts=0, reconnection_delay=1)
# Track switch counts per page for interval-based refresh
page_switch_counts = {}  # page_id -> count since last refresh
last_screenshot_time = 0
shot_lock = threading.Lock()


def get_hostname():
    """Get system hostname."""
    return socket.gethostname()


def get_local_ip():
    """Get local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def log(message):
    """Log with timestamp."""
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{timestamp}] {message}')


def capture_screenshot():
    """Capture a scaled screenshot using scrot."""
    # Try Chromium DevTools screenshot first (more reliable than X capture)
    try:
        r = requests.get('http://127.0.0.1:9222/json/list', timeout=2)
        if r.ok:
            pages = r.json()
            page = next((p for p in pages if p.get('type') == 'page'), None)
            if page and page.get('webSocketDebuggerUrl'):
                ws = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=3)
                ws.send(json.dumps({
                    'id': 1,
                    'method': 'Page.captureScreenshot',
                    'params': {
                        'format': 'jpeg',
                        'quality': 60
                    }
                }))
                resp = json.loads(ws.recv())
                ws.close()
                data = resp.get('result', {}).get('data')
                if data:
                    with open(SCREENSHOT_PATH, 'wb') as f:
                        f.write(base64.b64decode(data))
                    if os.path.exists(SCREENSHOT_PATH):
                        log(f'Screenshot saved (CDP): {SCREENSHOT_PATH}')
                        return True
    except Exception as e:
        log(f'CDP screenshot error: {e}')

    # Fallback: scrot
    if shutil.which('scrot'):
        try:
            subprocess.run(
                ['scrot', '-o', '-q', '60', '-d', '0.2', '-t', SCREENSHOT_SIZE, SCREENSHOT_PATH],
                env={**os.environ, 'DISPLAY': DISPLAY},
                capture_output=True,
                timeout=5
            )
            if os.path.exists(SCREENSHOT_PATH):
                log(f'Screenshot saved (scrot): {SCREENSHOT_PATH}')
                return True
        except Exception as e:
            log(f'Scrot error: {e}')

    # Fallback: xwd + convert
    if shutil.which('xwd') and shutil.which('convert'):
        try:
            tmp_xwd = '/tmp/pi-kiosk-shot.xwd'
            subprocess.run(
                ['xwd', '-root', '-silent', '-display', DISPLAY, '-out', tmp_xwd],
                capture_output=True,
                timeout=5
            )
            if os.path.exists(tmp_xwd):
                subprocess.run(
                    ['convert', tmp_xwd, '-resize', SCREENSHOT_SIZE, SCREENSHOT_PATH],
                    capture_output=True,
                    timeout=5
                )
                os.remove(tmp_xwd)
            if os.path.exists(SCREENSHOT_PATH):
                log(f'Screenshot saved (xwd): {SCREENSHOT_PATH}')
                return True
        except Exception as e:
            log(f'XWD/convert error: {e}')
            return False

    log('No screenshot tools available')
    return False


def upload_screenshot():
    """Upload the latest screenshot to the server."""
    global last_screenshot_time
    if not SCREENSHOT_ENABLED:
        return
    now = time.time()
    if now - last_screenshot_time < SCREENSHOT_INTERVAL_SEC:
        return
    with shot_lock:
        now = time.time()
        if now - last_screenshot_time < SCREENSHOT_INTERVAL_SEC:
            return
        if not capture_screenshot():
            return
        try:
            url = f"{server_url}/api/display/screenshot"
            with open(SCREENSHOT_PATH, 'rb') as f:
                files = {'file': ('screenshot.jpg', f, 'image/jpeg')}
                data = {'hostname': get_hostname()}
                requests.post(url, files=files, data=data, timeout=5)
            last_screenshot_time = time.time()
        except Exception as e:
            log(f'Screenshot upload failed: {e}')


def schedule_screenshot(delay_sec=1.0):
    """Schedule a screenshot upload after a delay."""
    if not SCREENSHOT_ENABLED:
        return
    t = threading.Timer(delay_sec, upload_screenshot)
    t.daemon = True
    t.start()


def get_current_page():
    """Get current page dict."""
    if not pages or current_index >= len(pages):
        return None
    return pages[current_index]


def build_url(path):
    """Build full URL from path."""
    if path.startswith('http://') or path.startswith('https://'):
        return path
    return f"{server_url}{path}"


def send_status():
    """Send status to server."""
    page = get_current_page()
    try:
        sio.emit('kiosk_status', {
            'hostname': get_hostname(),
            'ip': get_local_ip(),
            'current_page_id': page['id'] if page else None,
            'current_url': page['url'] if page else None,
            'paused': paused,
            'current_index': current_index,
            'total_pages': len(pages)
        })
    except Exception as e:
        log(f'Error sending status: {e}')


def clear_profile_locks():
    """Clear stale Chromium profile locks."""
    for name in ('SingletonLock', 'SingletonSocket', 'SingletonCookie'):
        path = os.path.join(PROFILE_DIR, name)
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


def kill_browser():
    """Kill browser process."""
    global browser_process
    if browser_process:
        try:
            browser_process.terminate()
            browser_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            browser_process.kill()
        except Exception:
            pass
        browser_process = None
    # Kill any orphaned chromium processes
    try:
        subprocess.run(['pkill', '-f', 'chromium.*--kiosk'], capture_output=True, timeout=5)
    except Exception:
        pass


def send_keystroke(keys):
    """Send keystroke to browser via xdotool (X11) or wtype (Wayland).

    Args:
        keys: Key combination like 'ctrl+Tab', 'ctrl+shift+Tab', 'ctrl+r', 'ctrl+1'
    """
    env = {**os.environ, 'DISPLAY': DISPLAY}

    # Try xdotool first (X11 - most common on Pi)
    if shutil.which('xdotool'):
        try:
            result = subprocess.run(
                ['xdotool', 'key', '--clearmodifiers', keys],
                env=env,
                capture_output=True,
                timeout=2
            )
            if result.returncode == 0:
                log(f'Sent keystroke: {keys}')
                return True
        except Exception as e:
            log(f'xdotool failed: {e}')

    # Fall back to wtype (Wayland)
    if shutil.which('wtype'):
        try:
            # Convert key format: 'ctrl+Tab' -> wtype args
            wtype_args = []
            parts = keys.lower().split('+')
            for part in parts[:-1]:  # Modifiers
                if part == 'ctrl':
                    wtype_args.extend(['-M', 'ctrl'])
                elif part == 'shift':
                    wtype_args.extend(['-M', 'shift'])
                elif part == 'alt':
                    wtype_args.extend(['-M', 'alt'])

            # The actual key
            key = parts[-1]
            if key == 'tab':
                wtype_args.extend(['-P', 'Tab'])
            elif key == 'r':
                wtype_args.extend(['-P', 'r'])
            elif key.isdigit():
                wtype_args.extend(['-P', key])

            # Release modifiers
            for part in parts[:-1]:
                if part == 'ctrl':
                    wtype_args.extend(['-m', 'ctrl'])
                elif part == 'shift':
                    wtype_args.extend(['-m', 'shift'])
                elif part == 'alt':
                    wtype_args.extend(['-m', 'alt'])

            result = subprocess.run(['wtype'] + wtype_args, capture_output=True, timeout=2)
            if result.returncode == 0:
                log(f'Sent keystroke via wtype: {keys}')
                return True
        except Exception as e:
            log(f'wtype failed: {e}')

    log(f'Failed to send keystroke: {keys}')
    return False


def launch_browser_with_tabs(urls):
    """Launch Chromium with all URLs as separate tabs.

    Args:
        urls: List of URLs to open as tabs
    """
    global browser_process

    if not urls:
        log('No URLs to display')
        return False

    log(f'Launching browser with {len(urls)} tabs')
    for i, url in enumerate(urls):
        log(f'  Tab {i+1}: {url}')

    kill_browser()
    os.makedirs(PROFILE_DIR, exist_ok=True)
    clear_profile_locks()

    # Log file for browser output
    log_dir = os.path.expanduser('~/.config/pi-kiosk')
    os.makedirs(log_dir, exist_ok=True)
    browser_log_path = os.path.join(log_dir, 'chromium.log')

    cmd = [
        CHROMIUM_CMD,
        '--kiosk',
        '--noerrdialogs',
        '--disable-infobars',
        '--disable-session-crashed-bubble',
        '--no-first-run',
        '--start-fullscreen',
        '--autoplay-policy=no-user-gesture-required',
        '--check-for-update-interval=31536000',
        '--disable-features=TranslateUI',
        '--disable-component-update',
        f'--user-data-dir={PROFILE_DIR}',
        '--password-store=basic',
        '--disable-background-networking',
        '--remote-debugging-port=9222',
        '--remote-debugging-address=127.0.0.1',
        '--remote-allow-origins=http://127.0.0.1:9222',
        # Stability flags
        '--disable-gpu-compositing',
        '--disable-dev-shm-usage',
        '--disable-breakpad',
        '--disable-hang-monitor',
        '--memory-pressure-off',
        '--max_old_space_size=256',
        '--js-flags=--max-old-space-size=256',
        # Enable logging
        '--enable-logging=stderr',
        '--v=1',
    ] + urls  # URLs become tabs

    if SCREENSHOT_ENABLED and SCREENSHOT_FORCE_SOFTWARE:
        cmd.insert(1, '--disable-gpu')
        cmd.insert(2, '--use-gl=swiftshader')

    log(f'Browser command: {" ".join(cmd[:10])}... + {len(urls)} URLs')

    try:
        # Open log file for browser stderr
        browser_log = open(browser_log_path, 'w')
        browser_process = subprocess.Popen(
            cmd,
            env={**os.environ, 'DISPLAY': DISPLAY},
            stdout=subprocess.DEVNULL,
            stderr=browser_log,
            start_new_session=True
        )
        log(f'Browser launched with PID: {browser_process.pid}')
        log(f'Browser log: {browser_log_path}')
        time.sleep(3)  # Wait for tabs to load
        return True
    except Exception as e:
        log(f'Error launching browser: {e}')
        return False


def get_browser_exit_info():
    """Get information about why browser exited."""
    global browser_process
    info = []

    if browser_process:
        exit_code = browser_process.returncode
        info.append(f'Exit code: {exit_code}')

        # Common exit codes
        exit_meanings = {
            0: 'Normal exit',
            1: 'General error',
            -6: 'SIGABRT (abort)',
            -9: 'SIGKILL (killed)',
            -11: 'SIGSEGV (segfault)',
            -15: 'SIGTERM (terminated)',
            134: 'SIGABRT (abort, unsigned)',
            137: 'SIGKILL (killed, unsigned)',
            139: 'SIGSEGV (segfault, unsigned)',
        }
        if exit_code in exit_meanings:
            info.append(f'Meaning: {exit_meanings[exit_code]}')

    # Check for crash dump
    crash_dir = os.path.expanduser('~/.config/pi-kiosk/chromium-profile/Crash Reports')
    if os.path.exists(crash_dir):
        crashes = os.listdir(crash_dir)
        if crashes:
            info.append(f'Crash reports: {len(crashes)} files in {crash_dir}')

    # Check recent lines from browser log
    log_path = os.path.expanduser('~/.config/pi-kiosk/chromium.log')
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r') as f:
                lines = f.readlines()
                # Get last 20 lines
                recent = lines[-20:] if len(lines) > 20 else lines
                # Look for error indicators
                errors = [l.strip() for l in recent if 'ERROR' in l or 'FATAL' in l or 'crash' in l.lower()]
                if errors:
                    info.append(f'Recent errors: {errors[:5]}')
        except Exception:
            pass

    # Check system memory
    try:
        result = subprocess.run(['free', '-m'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            if len(lines) >= 2:
                # Parse the Mem line
                parts = lines[1].split()
                if len(parts) >= 4:
                    total, used, free = parts[1], parts[2], parts[3]
                    info.append(f'Memory: {used}MB used / {total}MB total ({free}MB free)')
    except Exception:
        pass

    return info


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
    """Background thread that rotates through tabs."""
    global current_index, running, paused, page_switch_counts

    log('Switcher thread started')

    while running:
        # Wait if paused or no pages
        if paused or not pages:
            time.sleep(0.5)
            continue

        # Get current page duration
        page = get_current_page()
        if not page:
            time.sleep(1)
            continue

        duration = page.get('duration', 30)

        # Wait for duration using a monotonic clock (avoids drift/early exits)
        start = time.monotonic()
        while running and not paused:
            elapsed = time.monotonic() - start
            if elapsed >= duration:
                break
            time.sleep(min(0.1, duration - elapsed))

        # If still running and not paused, switch to next tab
        if running and not paused and pages:
            send_keystroke('ctrl+Tab')
            current_index = (current_index + 1) % len(pages)

            # Check if the new page has auto-refresh enabled with interval
            new_page = get_current_page()
            if new_page and new_page.get('refresh'):
                page_id = new_page.get('id')
                refresh_interval = new_page.get('refresh_interval', 1)

                # Increment switch count for this page
                if page_id not in page_switch_counts:
                    page_switch_counts[page_id] = 0
                page_switch_counts[page_id] += 1

                # Check if we've reached the refresh interval
                if page_switch_counts[page_id] >= refresh_interval:
                    time.sleep(0.3)  # Small delay for tab switch to complete
                    send_keystroke('ctrl+r')
                    log(f'Auto-refreshed page: {new_page.get("name", "unnamed")} (after {refresh_interval} switches)')
                    page_switch_counts[page_id] = 0  # Reset counter

            send_status()
            schedule_screenshot()

    log('Switcher thread stopped')


def refresh_pages():
    """Request pages from server."""
    try:
        sio.emit('request_pages', {'hostname': get_hostname()})
    except Exception as e:
        log(f'Error requesting pages: {e}')


# Socket.IO Event Handlers
@sio.event
def connect():
    """Handle connection to server."""
    log('Connected to server')
    sio.emit('kiosk_connect', {'hostname': get_hostname(), 'ip': get_local_ip()})
    refresh_pages()


@sio.event
def disconnect():
    """Handle disconnection from server."""
    log('Disconnected from server')


@sio.on('pages_list')
def on_pages_list(data):
    """Handle pages list from server."""
    global pages, current_index

    log(f'Received {len(data)} pages')
    pages = data

    # Reset to first tab
    current_index = 0

    # Get URLs and launch browser (get_enabled_urls handles fallback)
    urls = get_enabled_urls()
    launch_browser_with_tabs(urls)

    if pages:
        send_status()
    else:
        log('No pages configured, showing default image')


@sio.on('pages_updated')
def on_pages_updated(data):
    """Handle pages update notification."""
    log('Pages updated, refreshing...')
    refresh_pages()


@sio.on('control')
def on_control(data):
    """Handle control commands from server."""
    global paused, current_index

    action = data.get('action')
    log(f'Control: {action}')

    if action == 'pause':
        paused = True
        send_status()

    elif action == 'resume':
        paused = False
        send_status()

    elif action == 'next':
        send_keystroke('ctrl+Tab')
        current_index = (current_index + 1) % len(pages) if pages else 0
        send_status()
        schedule_screenshot()

    elif action == 'prev':
        send_keystroke('ctrl+shift+Tab')
        current_index = (current_index - 1) % len(pages) if pages else 0
        send_status()
        schedule_screenshot()

    elif action == 'refresh':
        send_keystroke('ctrl+r')
        schedule_screenshot()

    elif action == 'goto':
        page_id = data.get('page_id')
        if page_id:
            # Find index of page
            for i, p in enumerate(pages):
                if p['id'] == page_id:
                    # Use Ctrl+1/2/3/... for direct tab access (1-9 only)
                    tab_num = i + 1
                    if 1 <= tab_num <= 9:
                        send_keystroke(f'ctrl+{tab_num}')
                        current_index = i
                        send_status()
                        schedule_screenshot()
                    else:
                        # For tabs > 9, cycle through
                        while current_index != i:
                            send_keystroke('ctrl+Tab')
                            current_index = (current_index + 1) % len(pages)
                            time.sleep(0.2)
                        send_status()
                        schedule_screenshot()
                    break

    elif action == 'login_mode':
        log('Entering login mode')
        paused = True
        # Show cursor
        try:
            subprocess.run(['pkill', 'unclutter'], capture_output=True, timeout=2)
        except Exception:
            pass
        send_status()

    elif action == 'exit_login_mode':
        log('Exiting login mode')
        hide_cursor()
        send_status()

    elif action == 'admin_mode':
        log('Entering admin mode')
        paused = True
        try:
            subprocess.run(['pkill', 'unclutter'], capture_output=True, timeout=2)
            subprocess.run(
                ['xdotool', 'search', '--name', 'Chromium', 'windowminimize'],
                env={**os.environ, 'DISPLAY': DISPLAY},
                capture_output=True, timeout=5
            )
        except Exception as e:
            log(f'Error in admin mode: {e}')
        send_status()

    elif action == 'exit_admin_mode':
        log('Exiting admin mode')
        try:
            subprocess.run(
                ['xdotool', 'search', '--name', 'Chromium', 'windowactivate', '--sync'],
                env={**os.environ, 'DISPLAY': DISPLAY},
                capture_output=True, timeout=5
            )
            subprocess.run(
                ['xdotool', 'key', 'F11'],
                env={**os.environ, 'DISPLAY': DISPLAY},
                capture_output=True, timeout=2
            )
        except Exception as e:
            log(f'Error exiting admin mode: {e}')
        hide_cursor()
        send_status()


@sio.on('wifi_config')
def on_wifi_config(data):
    """Handle WiFi configuration."""
    ssid = data.get('ssid', '')
    password = data.get('password', '')
    hidden = data.get('hidden', False)

    if not ssid:
        return

    log(f'Configuring WiFi: {ssid}')
    success = False
    error_msg = ''
    result_msg = ''
    if shutil.which('nmcli'):
        try:
            cmd = ['nmcli', 'device', 'wifi', 'connect', ssid]
            if password:
                cmd.extend(['password', password])
            if hidden:
                cmd.extend(['hidden', 'yes'])
            r = subprocess.run(cmd, capture_output=True, timeout=30)
            if r.returncode != 0:
                r = subprocess.run(['sudo'] + cmd, capture_output=True, timeout=30)
            if r.returncode == 0:
                success = True
                result_msg = 'Connected'
            else:
                error_msg = (r.stderr.decode() if r.stderr else r.stdout.decode()).strip()

                # If SSID not found, pre-save profile for later
                if 'No network with SSID' in error_msg:
                    try:
                        add_cmd = ['sudo', 'nmcli', 'connection', 'add', 'type', 'wifi',
                                   'ifname', 'wlan0', 'con-name', ssid, 'ssid', ssid]
                        subprocess.run(add_cmd, capture_output=True, timeout=30)
                        if password:
                            subprocess.run([
                                'sudo', 'nmcli', 'connection', 'modify', ssid,
                                'wifi-sec.key-mgmt', 'wpa-psk',
                                'wifi-sec.psk', password
                            ], capture_output=True, timeout=30)
                        if hidden:
                            subprocess.run([
                                'sudo', 'nmcli', 'connection', 'modify', ssid,
                                '802-11-wireless.hidden', 'yes'
                            ], capture_output=True, timeout=30)
                        subprocess.run([
                            'sudo', 'nmcli', 'connection', 'modify', ssid,
                            'connection.autoconnect', 'yes'
                        ], capture_output=True, timeout=30)
                        success = True
                        result_msg = 'Saved (SSID not currently visible)'
                        error_msg = ''
                    except Exception as e:
                        error_msg = f'{error_msg}; save failed: {e}'
        except Exception as e:
            error_msg = str(e)
            log(f'WiFi config error: {e}')

    try:
        sio.emit('wifi_result', {
            'hostname': get_hostname(),
            'success': success,
            'error': error_msg,
            'message': result_msg
        })
    except Exception:
        pass


@sio.on('update_client')
def on_update_client(data):
    """Handle update command from server."""
    url = data.get('url')
    if not url:
        return
    log(f'Update requested: {url}')
    success = False
    error_msg = ''
    try:
        r = subprocess.run(['sudo', '/opt/pi-kiosk/update.sh', '--url', url], capture_output=True, timeout=120)
        if r.returncode == 0:
            success = True
        else:
            error_msg = (r.stderr.decode() if r.stderr else r.stdout.decode()).strip()
    except Exception as e:
        error_msg = str(e)

    try:
        sio.emit('update_result', {
            'hostname': get_hostname(),
            'success': success,
            'error': error_msg
        })
    except Exception:
        pass


@sio.on('restart_kiosk')
def on_restart_kiosk():
    """Handle restart command."""
    log('Restart command received')
    try:
        subprocess.run(['sudo', 'systemctl', 'restart', 'pi-kiosk'], capture_output=True, timeout=10)
    except Exception:
        pass


@sio.on('reboot_system')
def on_reboot_system():
    """Handle reboot command."""
    log('Reboot command received')
    try:
        subprocess.run(['sudo', 'reboot'], capture_output=True, timeout=5)
    except Exception:
        pass


def hide_cursor():
    """Hide the mouse cursor."""
    try:
        subprocess.Popen(
            ['unclutter', '-idle', '0.1', '-root'],
            env={**os.environ, 'DISPLAY': DISPLAY},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception:
        pass


def disable_screen_blanking():
    """Disable screen blanking."""
    env = {**os.environ, 'DISPLAY': DISPLAY}
    for cmd in [['xset', 's', 'off'], ['xset', '-dpms'], ['xset', 's', 'noblank']]:
        try:
            subprocess.run(cmd, env=env, capture_output=True)
        except Exception:
            pass


def wait_for_display(timeout=60):
    """Wait for X display to be available."""
    start = time.time()
    env = {**os.environ, 'DISPLAY': DISPLAY}
    while time.time() - start < timeout:
        try:
            result = subprocess.run(['xset', 'q'], env=env, capture_output=True, timeout=2)
            if result.returncode == 0:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def wait_for_server(timeout=60):
    """Wait for server to be available."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(f'{server_url}/api/status', timeout=5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def signal_handler(sig, frame):
    """Handle shutdown signals."""
    global running
    log('Shutting down...')
    running = False
    kill_browser()
    try:
        sio.disconnect()
    except Exception:
        pass
    sys.exit(0)


def main():
    global running, server_url, DISPLAY

    parser = argparse.ArgumentParser(description='Pi Kiosk Display (Tab-Based)')
    parser.add_argument('--server', '-s', default=DEFAULT_SERVER_URL, help='Server URL')
    parser.add_argument('--display', '-d', default=DISPLAY, help='X display')
    args = parser.parse_args()

    server_url = args.server
    DISPLAY = args.display

    log('Pi Kiosk starting (tab-based mode)...')
    log(f'Hostname: {get_hostname()}')
    log(f'Server: {server_url}')

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Wait for X display
    log('Waiting for display...')
    if not wait_for_display():
        log('Display timeout - continuing anyway')

    hide_cursor()
    disable_screen_blanking()

    # Wait for server
    log('Waiting for server...')
    while running and not wait_for_server(timeout=10):
        log('Server not ready, retrying...')

    if not running:
        return

    log('Server ready, connecting...')

    # Connect to server
    while running:
        try:
            sio.connect(server_url, wait_timeout=10)
            break
        except Exception as e:
            log(f'Connection failed: {e}')
            time.sleep(5)

    # Start switcher thread
    switcher = threading.Thread(target=switcher_thread, daemon=True)
    switcher.start()

    # Main loop - monitor browser
    last_crash = 0
    while running:
        try:
            # Check if browser crashed
            if browser_process and browser_process.poll() is not None:
                now = time.time()

                # Get diagnostic info about why browser exited
                exit_info = get_browser_exit_info()
                log('--- Browser Exit Diagnostics ---')
                for info in exit_info:
                    log(f'  {info}')
                log('--------------------------------')

                if now - last_crash < 10:
                    log('Browser crashed too fast, waiting 10s before relaunch...')
                    time.sleep(10)
                last_crash = now
                log('Relaunching browser...')
                urls = get_enabled_urls()
                launch_browser_with_tabs(urls)

            sio.sleep(2)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log(f'Main loop error: {e}')
            time.sleep(5)

    signal_handler(None, None)


if __name__ == '__main__':
    main()
