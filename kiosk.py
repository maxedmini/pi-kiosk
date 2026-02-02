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
import urllib.parse
import ipaddress
import requests
import json
import base64
import websocket
from datetime import datetime

import socketio

# Import network helper for Tailscale/hybrid connection support
try:
    import network_helper
    NETWORK_HELPER_AVAILABLE = True
except ImportError:
    NETWORK_HELPER_AVAILABLE = False
    # Fallback implementations if network_helper not available
    class network_helper:
        @staticmethod
        def get_connection_candidates(url, server_name=None):
            return [{'url': url, 'type': 'unknown', 'priority': 1}]

        @staticmethod
        def test_connection_health(url, timeout=3):
            return (False, float('inf'))

        @staticmethod
        def get_tailscale_ip():
            return None

        @staticmethod
        def is_tailscale_active():
            return False

        @staticmethod
        def get_peer_tailscale_ip(name):
            return None

        @staticmethod
        def discover_server_addresses(local_ip=None, tailscale_name=None, port='5000'):
            candidates = []
            if local_ip:
                candidates.append({'url': f'http://{local_ip}:{port}', 'type': 'local', 'priority': 1})
            return candidates

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
browser_ready = False
sync_target_page_id = None
sync_at = None
reset_timer = False
sync_enabled = True
sync_server_time = None
sync_received_at = None
safe_mode_until = 0
crash_times = []
paused = False
running = True
server_url = DEFAULT_SERVER_URL
server_name = None  # Tailscale hostname of the server for auto-discovery
current_server_url = None  # Track which URL we're actually connected to
connection_type = 'unknown'  # 'local', 'tailscale', or 'unknown'
server_url_candidates = []  # List of server URLs to try
last_connection_health_check = 0  # Time of last health check
last_disconnect_time = 0  # Track when we disconnected for auto-reconnect logic
reconnect_rescan_threshold = 30  # Seconds of disconnection before rescanning for servers
last_paused_before_disconnect = False  # Track paused state before disconnect
pause_reason = None  # 'manual', 'admin', 'login', 'sync', or None
admin_mode_active = False
last_switch_time = 0  # Track when we last switched connection
local_stable_since = 0  # Track when local connection became stable
switch_check_interval = 15  # Seconds between switch checks
switch_stable_window = 30  # Seconds local must be stable before switching
switch_cooldown = 120  # Minimum seconds between switches
last_local_failure_time = 0  # Track recent local failures
local_failure_cooldown = 300  # Seconds to avoid local after failure
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


def is_ip_in_local_net(ip):
    """Check if an IP is within the current local network."""
    try:
        if not ip or ip.startswith('127.'):
            return False
        local_net = get_local_network()
        if not local_net:
            return False
        return ipaddress.ip_address(ip) in local_net
    except Exception:
        return False


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
            url = build_url('/api/display/screenshot')
            with open(SCREENSHOT_PATH, 'rb') as f:
                files = {'file': ('screenshot.jpg', f, 'image/jpeg')}
                data = {'hostname': get_hostname()}
                requests.post(url, files=files, data=data, timeout=5)
            last_screenshot_time = time.time()
        except Exception as e:
            log(f'Screenshot upload failed: {e}')
            try:
                global last_local_failure_time
                if connection_type == 'local':
                    last_local_failure_time = time.time()
            except Exception:
                pass


def schedule_screenshot(delay_sec=1.0):
    """Schedule a screenshot upload after a delay."""
    if not SCREENSHOT_ENABLED:
        return
    t = threading.Timer(delay_sec, upload_screenshot)
    t.daemon = True
    t.start()


def exit_admin_mode_actions():
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


def get_current_page():
    """Get current page dict."""
    if not pages or current_index >= len(pages):
        return None
    return pages[current_index]


def build_url(path):
    """Build full URL from path."""
    if path.startswith('http://') or path.startswith('https://'):
        return path
    # Use current_server_url if available (actual connected server), otherwise fall back to configured server_url
    active_url = current_server_url if current_server_url else server_url
    return f"{active_url}{path}"


def send_status():
    """Send status to server."""
    page = get_current_page()
    try:
        status_data = {
            'hostname': get_hostname(),
            'ip': get_local_ip(),
            'current_page_id': page['id'] if page else None,
            'current_url': page['url'] if page else None,
            'paused': paused,
            'current_index': current_index,
            'total_pages': len(pages),
            'safe_mode': time.time() < safe_mode_until
        }

        # Add Tailscale/connection info if available
        if NETWORK_HELPER_AVAILABLE:
            tailscale_ip = network_helper.get_tailscale_ip()
            if tailscale_ip:
                status_data['tailscale_ip'] = tailscale_ip
            status_data['connection_type'] = connection_type

        sio.emit('kiosk_status', status_data)
    except Exception as e:
        log(f'Error sending status: {e}')


def get_cpu_temp_c():
    """Return CPU temperature in Celsius (float) or None."""
    try:
        out = subprocess.check_output(['vcgencmd', 'measure_temp'], text=True).strip()
        # temp=38.4'C
        if out.startswith('temp='):
            return float(out.split('=')[1].split("'")[0])
    except Exception:
        pass
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            return float(f.read().strip()) / 1000.0
    except Exception:
        return None


def get_mem_info_mb():
    """Return (mem_total_mb, mem_free_mb) or (None, None)."""
    try:
        mem_total = None
        mem_available = None
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    mem_total = int(line.split()[1]) / 1024.0
                elif line.startswith('MemAvailable:'):
                    mem_available = int(line.split()[1]) / 1024.0
        return mem_total, mem_available
    except Exception:
        return None, None


def get_uptime_sec():
    """Return uptime in seconds or None."""
    try:
        with open('/proc/uptime', 'r') as f:
            return float(f.read().split()[0])
    except Exception:
        return None


def get_wifi_rssi_dbm():
    """Return Wi-Fi RSSI in dBm (int) or None."""
    try:
        out = subprocess.check_output(['iwconfig', 'wlan0'], text=True, stderr=subprocess.DEVNULL)
        # Look for "Signal level=-55 dBm"
        for part in out.split():
            if part.startswith('level=') or part.startswith('level:-'):
                val = part.split('=')[-1].replace('dBm', '')
                return int(val)
        if 'Signal level=' in out:
            seg = out.split('Signal level=')[1].split()[0]
            return int(seg.replace('dBm', ''))
    except Exception:
        return None
    return None


def send_health():
    """Send health metrics to server."""
    temp_c = get_cpu_temp_c()
    mem_total, mem_free = get_mem_info_mb()
    uptime = get_uptime_sec()
    rssi = get_wifi_rssi_dbm()
    try:
        sio.emit('kiosk_health', {
            'hostname': get_hostname(),
            'ip': get_local_ip(),
            'temp_c': temp_c,
            'mem_total_mb': mem_total,
            'mem_free_mb': mem_free,
            'uptime_sec': uptime,
            'wifi_rssi_dbm': rssi,
            'last_seen': datetime.now().isoformat()
        })
    except Exception as e:
        log(f'Error sending health: {e}')


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
            # Ensure Chromium has focus before sending keys
            subprocess.run(
                ['xdotool', 'search', '--name', 'Chromium', 'windowactivate', '--sync'],
                env=env,
                capture_output=True,
                timeout=2
            )
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
    global browser_process, current_index, browser_ready

    if not urls:
        log('No URLs to display')
        return False

    log(f'Launching browser with {len(urls)} tabs')
    for i, url in enumerate(urls):
        log(f'  Tab {i+1}: {url}')

    browser_ready = False
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
        # Chromium focuses the last opened tab; force first tab to align timing
        send_keystroke('ctrl+1')
        current_index = 0
        browser_ready = True
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
        urls = [build_url('/static/default.png')]
    return urls


def switcher_thread():
    """Background thread that rotates through tabs."""
    global current_index, running, paused, page_switch_counts, browser_ready, sync_target_page_id, sync_at, reset_timer, sync_enabled, sync_server_time, sync_received_at, safe_mode_until

    log('Switcher thread started')

    while running:
        if time.time() < safe_mode_until:
            time.sleep(1)
            continue
        # If a sync is scheduled, wait for the target time and align
        if paused and browser_ready and sync_at is not None:
            if time.time() >= sync_at:
                if sync_target_page_id is not None:
                    goto_page_id(sync_target_page_id)
                paused = False
                pause_reason = None
                sync_at = None
                sync_target_page_id = None
                send_status()
        # Wait if paused, no pages, or browser not ready
        if paused or not pages or not browser_ready:
            time.sleep(0.5)
            continue

        # Time-locked sync mode (server time)
        if sync_enabled and sync_server_time is not None and sync_received_at is not None:
            now = sync_server_time + (time.time() - sync_received_at)
            idx, remaining = compute_sync_target(now)
            if idx is not None and idx != current_index:
                goto_page_index(idx)
            sleep_for = max(0.05, min(0.5, remaining))
            time.sleep(sleep_for)
            continue

        # Get current page duration
        page = get_current_page()
        if not page:
            time.sleep(1)
            continue

        duration = page.get('duration', 30)
        log(f"Starting page: id={page.get('id')} name={page.get('name', '')} duration={duration}s index={current_index}")

        # Wait for duration using a monotonic clock (avoids drift/early exits)
        start = time.monotonic()
        break_for_reset = False
        while running and not paused:
            if reset_timer:
                reset_timer = False
                break_for_reset = True
                break
            elapsed = time.monotonic() - start
            if elapsed >= duration:
                break
            time.sleep(min(0.1, duration - elapsed))
        if break_for_reset:
            continue
        log(f"Completed page: id={page.get('id')} elapsed={elapsed:.2f}s target={duration}s")

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


def goto_page_id(page_id):
    """Switch to a specific page by id."""
    global current_index, reset_timer
    if not pages:
        return
    try:
        page_id = int(page_id)
    except Exception:
        return
    idx = next((i for i, p in enumerate(pages) if p.get('id') == page_id), None)
    if idx is None:
        return
    if idx == current_index:
        send_status()
        schedule_screenshot()
        return
    tab_num = idx + 1
    if 1 <= tab_num <= 9:
        send_keystroke(f'ctrl+{tab_num}')
        current_index = idx
        reset_timer = True
        send_status()
        schedule_screenshot()
        return
    # For tabs > 9, cycle through
    while current_index != idx:
        send_keystroke('ctrl+Tab')
        current_index = (current_index + 1) % len(pages)
        time.sleep(0.2)
    reset_timer = True
    send_status()
    schedule_screenshot()


def goto_page_index(idx):
    """Switch to a specific page by index."""
    global current_index, reset_timer
    if not pages or idx is None:
        return
    if idx == current_index:
        send_status()
        schedule_screenshot()
        return
    tab_num = idx + 1
    if 1 <= tab_num <= 9:
        send_keystroke(f'ctrl+{tab_num}')
        current_index = idx
        reset_timer = True
        send_status()
        schedule_screenshot()
        return
    while current_index != idx:
        send_keystroke('ctrl+Tab')
        current_index = (current_index + 1) % len(pages)
        time.sleep(0.2)
    reset_timer = True
    send_status()
    schedule_screenshot()


def compute_sync_target(now_ts):
    """Return (index, seconds_remaining) for the given timestamp."""
    durations = []
    for p in pages:
        try:
            durations.append(int(p.get('duration', 30)))
        except Exception:
            durations.append(30)
    total = sum(durations)
    if not durations or total <= 0:
        return None, 1.0
    offset = now_ts % total
    acc = 0
    for i, d in enumerate(durations):
        if offset < acc + d:
            remaining = (acc + d) - offset
            return i, remaining
        acc += d
    return 0, durations[0]


# Socket.IO Event Handlers
@sio.event
def connect():
    """Handle connection to server."""
    global last_disconnect_time, last_paused_before_disconnect, paused, pause_reason, admin_mode_active
    log('Connected to server')
    last_disconnect_time = 0  # Reset disconnect timer on successful connection
    if admin_mode_active:
        log('Auto-exiting admin mode after reconnect')
        admin_mode_active = False
        exit_admin_mode_actions()
        paused = False
        pause_reason = None
    if pause_reason not in ('manual', 'admin', 'login') and not last_paused_before_disconnect:
        paused = False
    send_status()
    connect_payload = {'hostname': get_hostname(), 'ip': get_local_ip(), 'connection_type': connection_type}
    if NETWORK_HELPER_AVAILABLE:
        ts_ip = network_helper.get_tailscale_ip()
        if ts_ip:
            connect_payload['tailscale_ip'] = ts_ip
    sio.emit('kiosk_connect', connect_payload)
    refresh_pages()
    send_health()


@sio.event
def disconnect():
    """Handle disconnection from server."""
    global last_disconnect_time, last_paused_before_disconnect, pause_reason, last_local_failure_time
    log('Disconnected from server')
    last_disconnect_time = time.time()
    last_paused_before_disconnect = paused
    # Preserve explicit pause modes across reconnects
    if not paused:
        pause_reason = None
    if connection_type == 'local':
        last_local_failure_time = time.time()


@sio.on('pages_list')
def on_pages_list(data):
    """Handle pages list from server."""
    global pages, current_index

    # Prefer pages_sync payload for time-locked mode
    if isinstance(data, list):
        log(f'Received {len(data)} pages (legacy)')
        pages = data
        if not sync_enabled:
            current_index = 0
            urls = get_enabled_urls()
            launch_browser_with_tabs(urls)
            if pages:
                send_status()
            else:
                log('No pages configured, showing default image')


@sio.on('pages_sync')
def on_pages_sync(data):
    """Handle synced pages list from server."""
    global pages, current_index, sync_enabled, sync_server_time, sync_received_at, reset_timer
    if not data:
        return
    pages = data.get('pages', [])
    sync_enabled = bool(data.get('sync_enabled', True))
    sync_server_time = data.get('server_time')
    sync_received_at = time.time()
    log(f'Received {len(pages)} pages (sync mode={sync_enabled})')
    current_index = 0
    reset_timer = True
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


@sio.on('sync')
def on_sync(data):
    """Align all displays to the same page at the same time."""
    global paused, sync_target_page_id, sync_at, sync_enabled, pause_reason
    if not data:
        return
    if data.get('sync_enabled') is False:
        sync_enabled = False
        return
    sync_at_value = data.get('sync_at')
    page_id = data.get('page_id')
    try:
        sync_at_value = float(sync_at_value)
    except Exception:
        sync_at_value = time.time() + 2.0
    sync_at = sync_at_value
    sync_target_page_id = page_id
    paused = True
    pause_reason = 'sync'
    log(f'Sync scheduled at {sync_at} for page {sync_target_page_id}')


@sio.on('control')
def on_control(data):
    """Handle control commands from server."""
    global paused, current_index, reset_timer, pause_reason, admin_mode_active

    action = data.get('action')
    log(f'Control: {action}')

    if action == 'pause':
        paused = True
        pause_reason = 'manual'
        send_status()

    elif action == 'resume':
        paused = False
        pause_reason = None
        send_status()

    elif action == 'next':
        send_keystroke('ctrl+Tab')
        current_index = (current_index + 1) % len(pages) if pages else 0
        reset_timer = True
        send_status()
        schedule_screenshot()

    elif action == 'prev':
        send_keystroke('ctrl+shift+Tab')
        current_index = (current_index - 1) % len(pages) if pages else 0
        reset_timer = True
        send_status()
        schedule_screenshot()

    elif action == 'refresh':
        send_keystroke('ctrl+r')
        reset_timer = True
        schedule_screenshot()

    elif action == 'goto':
        page_id = data.get('page_id')
        if page_id:
            goto_page_id(page_id)

    elif action == 'login_mode':
        log('Entering login mode')
        paused = True
        pause_reason = 'login'
        # Show cursor
        try:
            subprocess.run(['pkill', 'unclutter'], capture_output=True, timeout=2)
        except Exception:
            pass
        send_status()

    elif action == 'exit_login_mode':
        log('Exiting login mode')
        paused = False
        pause_reason = None
        hide_cursor()
        send_status()

    elif action == 'admin_mode':
        log('Entering admin mode')
        try:
            subprocess.run(['pkill', 'unclutter'], capture_output=True, timeout=2)
            subprocess.run(
                ['xdotool', 'search', '--name', 'Chromium', 'windowminimize'],
                env={**os.environ, 'DISPLAY': DISPLAY},
                capture_output=True, timeout=5
            )
        except Exception as e:
            log(f'Error in admin mode: {e}')
        admin_mode_active = True
        send_status()

    elif action == 'exit_admin_mode':
        log('Exiting admin mode')
        paused = False
        pause_reason = None
        admin_mode_active = False
        exit_admin_mode_actions()
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


def get_server_candidates(configured_url):
    """
    Get list of server URLs to try, in priority order.
    Returns list with local IPs first, then Tailscale IPs.

    Automatically discovers Tailscale fallback addresses:
    - If server_name is configured, uses that specific peer
    - Otherwise, auto-scans all online Tailscale peers
    """
    global server_url_candidates

    if NETWORK_HELPER_AVAILABLE:
        # Pass server_name to enable Tailscale peer discovery
        # auto_scan_tailscale=True means it will scan all peers if no specific server_name
        candidates = network_helper.get_connection_candidates(
            configured_url,
            server_name=server_name,
            auto_scan_tailscale=True
        )

        server_url_candidates = candidates  # Store for later optimization checks

        # Log candidates grouped by type
        local_candidates = [c for c in candidates if c['type'] == 'local']
        tailscale_candidates = [c for c in candidates if c['type'] == 'tailscale']

        log(f'Connection candidates: {len(candidates)} total')
        if local_candidates:
            log(f'  Local network: {len(local_candidates)}')
            for c in local_candidates:
                log(f'    - {c["url"]}')
        if tailscale_candidates:
            log(f'  Tailscale fallback: {len(tailscale_candidates)}')
            for c in tailscale_candidates:
                hostname = c.get('hostname', '')
                if hostname:
                    log(f'    - {c["url"]} ({hostname})')
                else:
                    log(f'    - {c["url"]}')

        return candidates
    else:
        # Fallback if network_helper not available
        candidates = [{'url': configured_url, 'type': 'unknown', 'priority': 1}]
        server_url_candidates = candidates
        return candidates


def test_server_connection(url, timeout=3):
    """Test if a server URL is reachable."""
    try:
        with urllib.request.urlopen(f'{url}/api/status', timeout=timeout) as resp:
            if resp.status == 200:
                return True
    except Exception:
        pass
    return False


def get_url_host(url):
    try:
        return urllib.parse.urlparse(url).hostname
    except Exception:
        return None


def get_default_interface():
    try:
        result = subprocess.run(['ip', '-4', 'route', 'show', 'default'], capture_output=True, text=True, timeout=2)
        if result.returncode != 0:
            return None
        # Example: "default via 192.168.0.1 dev wlan0 proto dhcp src 192.168.0.87 metric 303"
        parts = result.stdout.strip().split()
        if 'dev' in parts:
            return parts[parts.index('dev') + 1]
    except Exception:
        pass
    return None


def get_local_network():
    """Return ipaddress.ip_network for the default interface, or None."""
    try:
        dev = get_default_interface()
        if not dev:
            return None
        result = subprocess.run(['ip', '-4', 'addr', 'show', 'dev', dev],
                                capture_output=True, text=True, timeout=2)
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith('inet '):
                # inet 192.168.0.87/21 brd 192.168.7.255 scope global dynamic noprefixroute wlan0
                cidr = line.split()[1]
                return ipaddress.ip_network(cidr, strict=False)
    except Exception:
        pass
    return None


def filter_candidates_for_local_ip(candidates, local_ip, require_same_subnet=True, avoid_local=False):
    if not local_ip or not candidates:
        return candidates
    local_net = get_local_network()
    filtered = []
    for c in candidates:
        if c['type'] == 'local' and avoid_local:
            log('Skipping local candidates due to recent local failure')
            continue
        if c['type'] == 'local' and require_same_subnet:
            host = get_url_host(c['url'])
            if host and host.count('.') == 3 and local_net:
                try:
                    if ipaddress.ip_address(host) not in local_net:
                        log(f'Skipping local candidate {c["url"]} (outside {local_net})')
                        continue
                except Exception:
                    pass
        filtered.append(c)
    return filtered


def find_best_server_url(candidates, timeout=60):
    """
    Try connection candidates in priority order until one succeeds.
    Returns (url, connection_type, hostname) or (None, None, None) if all fail.
    """
    start = time.time()

    while time.time() - start < timeout:
        for candidate in candidates:
            url = candidate['url']
            conn_type = candidate['type']
            hostname = candidate.get('hostname', '')

            if hostname:
                log(f'Trying: {url} ({conn_type}, {hostname})...')
            else:
                log(f'Trying: {url} ({conn_type})...')

            if test_server_connection(url, timeout=3):
                if conn_type == 'tailscale' and hostname:
                    log(f'Connected via Tailscale to {hostname} ({url})')
                elif conn_type == 'local':
                    log(f'Connected via local network ({url})')
                else:
                    log(f'Connected to {url}')
                return (url, conn_type, hostname)

        # If we've tried all candidates and none worked, wait before retry
        if time.time() - start < timeout:
            log('All candidates failed, retrying in 5s...')
            time.sleep(5)

    return (None, None, None)


def get_connection_priority(conn_type):
    """Return numeric priority for a connection type (lower is better)."""
    return {
        'localhost': 0,
        'local': 1,
        'tailscale': 2,
        'unknown': 3
    }.get(conn_type, 3)


def find_candidate_by_type(candidates, preferred_types):
    """Return first candidate matching preferred types in order."""
    for preferred in preferred_types:
        for c in candidates:
            if c['type'] == preferred:
                return c
    return None


def wait_for_server(timeout=60):
    """
    Wait for server to be available, trying multiple connection candidates.
    Updates global current_server_url and connection_type when successful.
    """
    global current_server_url, connection_type

    # Get connection candidates
    candidates = get_server_candidates(server_url)

    # Filter local candidates that are on a different subnet for reconnect/search
    avoid_local = (time.time() - last_local_failure_time) < local_failure_cooldown
    candidates = filter_candidates_for_local_ip(candidates, get_local_ip(), require_same_subnet=True, avoid_local=avoid_local)

    # Try to find a working server
    result = find_best_server_url(candidates, timeout)
    best_url, best_type, best_hostname = result if len(result) == 3 else (*result, None)

    if best_url:
        current_server_url = best_url
        connection_type = best_type
        return True

    log('No server connection available')
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
    global running, server_url, server_name, DISPLAY, safe_mode_until, crash_times
    global current_server_url, connection_type, last_disconnect_time
    global last_switch_time, local_stable_since

    parser = argparse.ArgumentParser(description='Pi Kiosk Display (Tab-Based)')
    parser.add_argument('--server', '-s', default=DEFAULT_SERVER_URL,
                        help='Server URL (local IP preferred, e.g., http://192.168.1.100:5000). '
                             'If Tailscale is installed, will automatically fall back to Tailscale '
                             'when local network is unavailable.')
    parser.add_argument('--server-name', '-n', default=None,
                        help='Optional: Tailscale hostname of server (e.g., pi-server). '
                             'If not set, will auto-scan all Tailscale peers to find server.')
    parser.add_argument('--display', '-d', default=DISPLAY, help='X display')
    args = parser.parse_args()

    server_url = args.server
    server_name = args.server_name
    DISPLAY = args.display

    log('Pi Kiosk starting (tab-based mode)...')
    log(f'Hostname: {get_hostname()}')
    log(f'Configured server: {server_url}')

    # Show Tailscale status
    if NETWORK_HELPER_AVAILABLE:
        if network_helper.is_tailscale_active():
            ts_ip = network_helper.get_tailscale_ip()
            log(f'Tailscale active (this device: {ts_ip})')
            log('Auto-failover enabled: will use Tailscale if local network unavailable')

            # Show available peers
            status = network_helper.get_tailscale_status()
            online_peers = [p for p in status.get('peers', []) if p.get('online')]
            if online_peers:
                log(f'Tailscale peers online: {len(online_peers)}')
                for peer in online_peers[:5]:  # Show first 5
                    log(f'  - {peer.get("hostname")}')
                if len(online_peers) > 5:
                    log(f'  ... and {len(online_peers) - 5} more')
        else:
            log('Tailscale not active (local network only)')

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

    # Connect to server using optimal URL
    while running:
        try:
            # Use the current_server_url determined by wait_for_server
            connect_url = current_server_url if current_server_url else server_url
            log(f'Connecting to Socket.IO at {connect_url}...')
            sio.connect(connect_url, wait_timeout=10)
            log(f'Socket.IO connected successfully (connection type: {connection_type})')
            break
        except Exception as e:
            log(f'Connection failed: {e}')

            # Try to find a new best server URL
            log('Scanning for available servers...')
            candidates = get_server_candidates(server_url)
            result = find_best_server_url(candidates, timeout=30)
            new_url, new_type, _ = result if len(result) == 3 else (*result, None)

            if new_url:
                current_server_url = new_url
                connection_type = new_type
            else:
                log('No server available, waiting before retry...')
                time.sleep(5)

    # Start switcher thread
    switcher = threading.Thread(target=switcher_thread, daemon=True)
    switcher.start()

    # Main loop - monitor browser
    last_crash = 0
    last_health = 0
    last_status = 0
    last_switch_check = 0
    while running:
        try:
            # Check if browser crashed
            if browser_process and browser_process.poll() is not None:
                now = time.time()
                crash_times.append(now)
                # keep last 5 minutes
                while crash_times and now - crash_times[0] > 300:
                    crash_times.pop(0)

                # Get diagnostic info about why browser exited
                exit_info = get_browser_exit_info()
                log('--- Browser Exit Diagnostics ---')
                for info in exit_info:
                    log(f'  {info}')
                log('--------------------------------')

                if len(crash_times) >= 3:
                    log('Entering safe mode due to repeated crashes...')
                    safe_mode_until = now + 300
                    crash_times.clear()
                    urls = [build_url('/static/default.png')]
                    launch_browser_with_tabs(urls)
                else:
                    if now - last_crash < 10:
                        log('Browser crashed too fast, waiting 10s before relaunch...')
                        time.sleep(10)
                    last_crash = now
                    if now < safe_mode_until:
                        urls = [build_url('/static/default.png')]
                    else:
                        log('Relaunching browser...')
                        urls = get_enabled_urls()
                    launch_browser_with_tabs(urls)

            now = time.time()
            if now - last_health >= 10:
                send_health()
                last_health = now
            if now - last_status >= 10:
                send_status()
                last_status = now

            # Check if we've been disconnected too long and need to rescan for servers
            if last_disconnect_time > 0 and not sio.connected:
                disconnect_duration = now - last_disconnect_time
                if disconnect_duration >= reconnect_rescan_threshold:
                    log(f'Disconnected for {disconnect_duration:.0f}s, rescanning for available servers...')

                    # Get fresh connection candidates (will scan Tailscale peers)
                    candidates = get_server_candidates(server_url)
                    avoid_local = (time.time() - last_local_failure_time) < local_failure_cooldown
                    candidates = filter_candidates_for_local_ip(candidates, get_local_ip(), require_same_subnet=True, avoid_local=avoid_local)
                    result = find_best_server_url(candidates, timeout=30)
                    new_url, new_type, new_hostname = result if len(result) == 3 else (*result, None)

                    if new_url:
                        log(f'Found server at {new_url} ({new_type})')
                        try:
                            # Disconnect cleanly if still connected to old session
                            try:
                                sio.disconnect()
                            except:
                                pass

                            # Update connection info and reconnect
                            current_server_url = new_url
                            connection_type = new_type
                            sio.connect(new_url, wait_timeout=10)
                            log(f'Reconnected to {new_url} ({new_type})')
                            last_disconnect_time = 0  # Reset disconnect timer
                        except Exception as e:
                            log(f'Reconnection failed: {e}')
                            # Reset timer to try again later
                            last_disconnect_time = now
                    else:
                        log('No server found, will retry later')
                        # Reset timer to try again in another threshold period
                        last_disconnect_time = now

            # Periodic connection optimization with stability + cooldown
            if NETWORK_HELPER_AVAILABLE and now - last_switch_check >= switch_check_interval:
                last_switch_check = now

                # Only optimize if currently using non-local connection
                if connection_type != 'local' and sio.connected:
                    candidates = get_server_candidates(server_url)
                    avoid_local = (time.time() - last_local_failure_time) < local_failure_cooldown
                    candidates = filter_candidates_for_local_ip(candidates, get_local_ip(), require_same_subnet=True, avoid_local=avoid_local)
                    local_candidate = find_candidate_by_type(candidates, ['local', 'localhost'])

                    # Only consider switching if the local candidate is in our current subnet
                    if local_candidate:
                        host = get_url_host(local_candidate['url'])
                        if host and not is_ip_in_local_net(host):
                            local_candidate = None

                    if local_candidate and test_server_connection(local_candidate['url'], timeout=2):
                        if local_stable_since == 0:
                            local_stable_since = now
                        stable_for = now - local_stable_since

                        if stable_for >= switch_stable_window:
                            # Apply cooldown only if we recently switched successfully
                            if (now - last_switch_time) >= switch_cooldown:
                                log(f'Local connection stable for {stable_for:.0f}s at {local_candidate["url"]}')
                                log(f'Switching from {connection_type} to local for better performance')
                                try:
                                    sio.disconnect()
                                    time.sleep(1)
                                    current_server_url = local_candidate['url']
                                    connection_type = local_candidate['type']
                                    sio.connect(current_server_url, wait_timeout=10)
                                    last_switch_time = time.time()
                                    local_stable_since = 0
                                    log(f'Successfully switched to {current_server_url} ({connection_type})')
                                except Exception as e:
                                    log(f'Failed to switch connections: {e}')
                                    # If switch failed, try to reconnect to previous server
                                    try:
                                        sio.connect(current_server_url, wait_timeout=10)
                                    except:
                                        pass
                    else:
                        local_stable_since = 0

            sio.sleep(2)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log(f'Main loop error: {e}')
            time.sleep(5)

    signal_handler(None, None)


if __name__ == '__main__':
    main()
