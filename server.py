#!/usr/bin/env python3
"""
Pi Kiosk Display Manager - Web Server
Flask server with REST API and WebSocket for managing kiosk pages.
Supports multiple Pi displays, image uploads, and hostname management.
"""

import os
import socket
import subprocess
import sqlite3
import uuid
import threading
import time
from datetime import datetime
import shutil
import io
import tarfile
import json
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit

# Import network helper for Tailscale support
try:
    import network_helper
    NETWORK_HELPER_AVAILABLE = True
except ImportError:
    NETWORK_HELPER_AVAILABLE = False

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, 'pages.db')
SETTINGS_FILE = os.path.join(BASE_DIR, 'settings.json')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max upload
HOST = '0.0.0.0'
PORT = 5000

# Ensure upload folder exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')
ASSET_CACHE_BUST = str(int(time.time()))

# Connected displays (in-memory tracking)
# Key: socket session ID, Value: display info dict
connected_displays = {}


def load_settings():
    """Load server settings from disk."""
    if not os.path.exists(SETTINGS_FILE):
        return {'sync_enabled': True, 'tailscale_authkey': None, 'tailscale_authkey_set_at': None}
    try:
        with open(SETTINGS_FILE, 'r') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {**{'sync_enabled': True, 'tailscale_authkey': None, 'tailscale_authkey_set_at': None}, **data}
    except Exception:
        pass
    return {'sync_enabled': True, 'tailscale_authkey': None, 'tailscale_authkey_set_at': None}


def save_settings(settings):
    """Persist server settings to disk."""
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f)
    except Exception:
        pass


SETTINGS = load_settings()


def serialize_displays():
    """Return connected displays with stable ids for UI controls."""
    return [{'id': sid, **info} for sid, info in connected_displays.items()]


def get_db():
    """Get database connection."""
    conn = sqlite3.connect(DATABASE, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # Enable WAL and busy timeout to reduce lock errors under concurrent access
    try:
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA synchronous=NORMAL;')
        conn.execute('PRAGMA busy_timeout=5000;')
    except Exception:
        pass
    return conn


def init_db():
    """Initialize the database with updated schema."""
    conn = get_db()

    # Create pages table with new columns
    conn.execute('''
        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT,
            name TEXT,
            duration INTEGER DEFAULT 30,
            position INTEGER,
            enabled INTEGER DEFAULT 1,
            type TEXT DEFAULT 'url',
            filename TEXT,
            display_id TEXT,
            refresh INTEGER DEFAULT 0,
            refresh_interval INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Check if we need to add new columns to existing table
    cursor = conn.execute('PRAGMA table_info(pages)')
    columns = [row[1] for row in cursor.fetchall()]

    if 'type' not in columns:
        conn.execute("ALTER TABLE pages ADD COLUMN type TEXT DEFAULT 'url'")
    if 'filename' not in columns:
        conn.execute("ALTER TABLE pages ADD COLUMN filename TEXT")
    if 'display_id' not in columns:
        conn.execute("ALTER TABLE pages ADD COLUMN display_id TEXT")
    if 'refresh' not in columns:
        conn.execute("ALTER TABLE pages ADD COLUMN refresh INTEGER DEFAULT 0")
    if 'refresh_interval' not in columns:
        conn.execute("ALTER TABLE pages ADD COLUMN refresh_interval INTEGER DEFAULT 1")
    if 'schedule_enabled' not in columns:
        conn.execute("ALTER TABLE pages ADD COLUMN schedule_enabled INTEGER DEFAULT 0")
    if 'schedule_start' not in columns:
        conn.execute("ALTER TABLE pages ADD COLUMN schedule_start TEXT")
    if 'schedule_end' not in columns:
        conn.execute("ALTER TABLE pages ADD COLUMN schedule_end TEXT")
    if 'schedule_ranges' not in columns:
        conn.execute("ALTER TABLE pages ADD COLUMN schedule_ranges TEXT")

    conn.commit()
    conn.close()


def dict_from_row(row):
    """Convert sqlite3.Row to dictionary."""
    if row is None:
        return None
    return dict(row)


def _parse_schedule_ranges(raw_ranges):
    """Parse schedule ranges JSON into a list of {start,end} dicts."""
    if not raw_ranges:
        return []
    try:
        ranges = json.loads(raw_ranges)
    except Exception:
        return []
    if not isinstance(ranges, list):
        return []
    normalized = []
    for r in ranges:
        if not isinstance(r, dict):
            continue
        start = r.get('start')
        end = r.get('end')
        if isinstance(start, str) and isinstance(end, str) and start and end:
            normalized.append({'start': start, 'end': end})
    return normalized


def _normalize_schedule_ranges(page):
    """Return normalized schedule ranges for a page as a list."""
    ranges = []
    raw_ranges = page.get('schedule_ranges')
    if isinstance(raw_ranges, list):
        for r in raw_ranges:
            if not isinstance(r, dict):
                continue
            start = r.get('start')
            end = r.get('end')
            if isinstance(start, str) and isinstance(end, str) and start and end:
                ranges.append({'start': start, 'end': end})
    elif isinstance(raw_ranges, str) and raw_ranges.strip():
        ranges = _parse_schedule_ranges(raw_ranges)

    if not ranges:
        start_str = page.get('schedule_start')
        end_str = page.get('schedule_end')
        if start_str and end_str:
            ranges = [{'start': start_str, 'end': end_str}]

    return ranges


def is_page_active_now(page):
    """Check if a page should be displayed at the current time.

    Args:
        page: dict with schedule_enabled, schedule_start, schedule_end, schedule_ranges

    Returns:
        True if page should be shown, False otherwise
    """
    # No schedule = always show
    if not page.get('schedule_enabled'):
        return True

    ranges = _normalize_schedule_ranges(page)
    if not ranges:
        return True

    try:
        now = datetime.now()
        current_minutes = now.hour * 60 + now.minute
        for r in ranges:
            start_parts = r['start'].split(':')
            start_minutes = int(start_parts[0]) * 60 + int(start_parts[1])

            end_parts = r['end'].split(':')
            end_minutes = int(end_parts[0]) * 60 + int(end_parts[1])

            # Normal case: start before end (e.g., 09:00 - 17:00)
            if start_minutes <= end_minutes:
                if start_minutes <= current_minutes < end_minutes:
                    return True
            # Overnight case: end before start (e.g., 23:00 - 06:00)
            else:
                if current_minutes >= start_minutes or current_minutes < end_minutes:
                    return True
        return False
    except (ValueError, AttributeError, KeyError):
        # On parse error, show the page
        return True


def allowed_file(filename):
    """Check if file extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_hostname():
    """Get the system hostname."""
    return socket.gethostname()


def get_local_ip():
    """Get the local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_tailscale_ip():
    """Get the Tailscale IP address if available."""
    if NETWORK_HELPER_AVAILABLE:
        return network_helper.get_tailscale_ip()
    return None


def is_tailscale_active():
    """Check if Tailscale is active."""
    if NETWORK_HELPER_AVAILABLE:
        return network_helper.is_tailscale_active()
    return False


def get_tailscale_status():
    """Get Tailscale status information."""
    if NETWORK_HELPER_AVAILABLE:
        return network_helper.get_tailscale_status()
    return {'active': False}


def reboot_system():
    """Reboot the system using sudo."""
    reboot_cmds = [
        ['/sbin/reboot'],
        ['/usr/sbin/reboot'],
        ['/bin/systemctl', 'reboot'],
        ['/usr/bin/systemctl', 'reboot'],
    ]
    for cmd in reboot_cmds:
        try:
            if shutil.which(cmd[0]) or os.path.exists(cmd[0]):
                subprocess.run(['sudo'] + cmd, check=True, capture_output=True)
                return
        except subprocess.CalledProcessError as e:
            raise RuntimeError(e.stderr.decode() or str(e))
    raise RuntimeError('No reboot command found')


# Web Interface
@app.route('/')
def index():
    """Serve the management interface."""
    return render_template('index.html', cache_bust=ASSET_CACHE_BUST)


@app.route('/display')
def display():
    """Serve the full-screen display viewer."""
    return render_template('display.html', cache_bust=ASSET_CACHE_BUST)


# Serve uploaded images
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    """Serve uploaded files."""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# Image viewer page (for displaying images in kiosk)
@app.route('/view/image/<filename>')
def view_image(filename):
    """Serve fullscreen image viewer."""
    return render_template('image.html', filename=filename)


# External URL viewer - wraps external URLs to bypass iframe restrictions
@app.route('/view/external')
def view_external():
    """Redirect to external URL for full-page viewing.

    This endpoint allows external URLs to be loaded directly in the browser
    rather than in an iframe, bypassing X-Frame-Options restrictions.
    """
    url = request.args.get('url')
    if not url:
        return "No URL provided", 400

    # For security, only allow http/https URLs
    if not url.startswith(('http://', 'https://')):
        return "Invalid URL scheme", 400

    # Redirect to the external URL
    from flask import redirect
    return redirect(url)


@app.route('/api/display/screenshot', methods=['POST'])
def upload_display_screenshot():
    """Upload a screenshot from a kiosk display."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    hostname = (request.form.get('hostname') or 'display').strip()
    safe_host = secure_filename(hostname) or 'display'
    filename = f"screenshots/{safe_host}.jpg"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    file.save(filepath)

    screenshot_url = f"/uploads/{filename}"

    # Update connected display info if present
    for sid, info in connected_displays.items():
        if info.get('hostname') == hostname:
            info['screenshot_url'] = screenshot_url
            info['last_screenshot'] = datetime.now().isoformat()
            break

    socketio.emit('displays_updated', serialize_displays())

    return jsonify({'success': True, 'url': screenshot_url})


# Serve install script for remote installation
@app.route('/install.sh')
def serve_install_script():
    """Serve the installation script for curl-based install."""
    script_path = os.path.join(BASE_DIR, 'install.sh')
    if os.path.exists(script_path):
        with open(script_path, 'r') as f:
            content = f.read()
        return content, 200, {'Content-Type': 'text/plain'}
    return "Install script not found", 404


@app.route('/update.tgz')
def serve_update_bundle():
    """Serve a tar.gz of the current app (excluding uploads/db/venv)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for root, dirs, files in os.walk(BASE_DIR):
            rel_root = os.path.relpath(root, BASE_DIR)
            if rel_root.startswith(('uploads', 'venv')):
                continue
            for name in files:
                if name in ('pages.db',):
                    continue
                path = os.path.join(root, name)
                rel = os.path.relpath(path, BASE_DIR)
                if rel.startswith(('uploads', 'venv')):
                    continue
                tar.add(path, arcname=rel)
    buf.seek(0)
    return buf.read(), 200, {'Content-Type': 'application/gzip'}


# REST API Endpoints

# === Pages API ===

@app.route('/api/pages', methods=['GET'])
def get_pages():
    """Get all pages ordered by position."""
    display_id = request.args.get('display_id')

    conn = get_db()
    if display_id:
        # Get pages for specific display OR pages with no display assigned (global)
        pages = conn.execute(
            'SELECT * FROM pages WHERE display_id = ? OR display_id IS NULL OR display_id = "" ORDER BY position ASC, id ASC',
            (display_id,)
        ).fetchall()
    else:
        pages = conn.execute(
            'SELECT * FROM pages ORDER BY position ASC, id ASC'
        ).fetchall()
    conn.close()

    result = []
    for p in pages:
        page_dict = dict_from_row(p)
        page_dict['schedule_ranges'] = _normalize_schedule_ranges(page_dict)
        # For images, generate the viewer URL
        if page_dict['type'] == 'image' and page_dict['filename']:
            page_dict['url'] = f"/view/image/{page_dict['filename']}"
            page_dict['thumbnail'] = f"/uploads/{page_dict['filename']}"
        # Add is_active field for UI display
        page_dict['is_active'] = is_page_active_now(page_dict)
        result.append(page_dict)

    return jsonify(result)


@app.route('/api/pages', methods=['POST'])
def add_page():
    """Add a new page (URL type)."""
    data = request.get_json()

    if not data or not data.get('url'):
        return jsonify({'error': 'URL is required'}), 400

    conn = get_db()

    # Get next position
    max_pos = conn.execute('SELECT MAX(position) FROM pages').fetchone()[0]
    next_pos = (max_pos or 0) + 1

    schedule_ranges = data.get('schedule_ranges')
    if isinstance(schedule_ranges, list):
        schedule_ranges = json.dumps(schedule_ranges)

    cursor = conn.execute(
        'INSERT INTO pages (url, name, duration, position, enabled, type, display_id, refresh, refresh_interval, schedule_enabled, schedule_start, schedule_end, schedule_ranges) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (
            data['url'],
            data.get('name', ''),
            data.get('duration', 30),
            next_pos,
            1 if data.get('enabled', True) else 0,
            'url',
            data.get('display_id', None),
            1 if data.get('refresh', False) else 0,
            data.get('refresh_interval', 1),
            1 if data.get('schedule_enabled', False) else 0,
            data.get('schedule_start'),
            data.get('schedule_end'),
            schedule_ranges
        )
    )
    conn.commit()

    page = conn.execute('SELECT * FROM pages WHERE id = ?', (cursor.lastrowid,)).fetchone()
    conn.close()

    # Notify all kiosks of update
    socketio.emit('pages_updated', {'action': 'add'})

    return jsonify(dict_from_row(page)), 201


@app.route('/api/pages/<int:page_id>', methods=['GET'])
def get_page(page_id):
    """Get a single page."""
    conn = get_db()
    page = conn.execute('SELECT * FROM pages WHERE id = ?', (page_id,)).fetchone()
    conn.close()

    if not page:
        return jsonify({'error': 'Page not found'}), 404

    page_dict = dict_from_row(page)
    page_dict['schedule_ranges'] = _normalize_schedule_ranges(page_dict)
    return jsonify(page_dict)


@app.route('/api/pages/<int:page_id>', methods=['PUT'])
def update_page(page_id):
    """Update a page."""
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    conn = get_db()
    page = conn.execute('SELECT * FROM pages WHERE id = ?', (page_id,)).fetchone()

    if not page:
        conn.close()
        return jsonify({'error': 'Page not found'}), 404

    # Build update query dynamically
    updates = []
    values = []

    if 'url' in data:
        updates.append('url = ?')
        values.append(data['url'])
    if 'name' in data:
        updates.append('name = ?')
        values.append(data['name'])
    if 'duration' in data:
        updates.append('duration = ?')
        values.append(data['duration'])
    if 'enabled' in data:
        updates.append('enabled = ?')
        values.append(1 if data['enabled'] else 0)
    if 'position' in data:
        updates.append('position = ?')
        values.append(data['position'])
    if 'display_id' in data:
        updates.append('display_id = ?')
        values.append(data['display_id'] if data['display_id'] else None)
    if 'refresh' in data:
        updates.append('refresh = ?')
        values.append(1 if data['refresh'] else 0)
    if 'refresh_interval' in data:
        updates.append('refresh_interval = ?')
        values.append(max(1, int(data['refresh_interval'])))
    if 'schedule_enabled' in data:
        updates.append('schedule_enabled = ?')
        values.append(1 if data['schedule_enabled'] else 0)
    if 'schedule_start' in data:
        updates.append('schedule_start = ?')
        values.append(data['schedule_start'])
    if 'schedule_end' in data:
        updates.append('schedule_end = ?')
        values.append(data['schedule_end'])
    if 'schedule_ranges' in data:
        schedule_ranges = data['schedule_ranges']
        if isinstance(schedule_ranges, list):
            schedule_ranges = json.dumps(schedule_ranges)
        updates.append('schedule_ranges = ?')
        values.append(schedule_ranges)

    if updates:
        values.append(page_id)
        conn.execute(
            f'UPDATE pages SET {", ".join(updates)} WHERE id = ?',
            values
        )
        conn.commit()

    page = conn.execute('SELECT * FROM pages WHERE id = ?', (page_id,)).fetchone()
    conn.close()

    # Notify all kiosks of update
    socketio.emit('pages_updated', {'action': 'update', 'page_id': page_id})

    return jsonify(dict_from_row(page))


@app.route('/api/pages/<int:page_id>', methods=['DELETE'])
def delete_page(page_id):
    """Delete a page."""
    conn = get_db()
    page = conn.execute('SELECT * FROM pages WHERE id = ?', (page_id,)).fetchone()

    if not page:
        conn.close()
        return jsonify({'error': 'Page not found'}), 404

    page_dict = dict_from_row(page)

    # If it's an image, delete the file too
    if page_dict['type'] == 'image' and page_dict['filename']:
        filepath = os.path.join(UPLOAD_FOLDER, page_dict['filename'])
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except Exception:
                pass

    conn.execute('DELETE FROM pages WHERE id = ?', (page_id,))
    conn.commit()
    conn.close()

    # Notify all kiosks of update
    socketio.emit('pages_updated', {'action': 'delete', 'page_id': page_id})

    return jsonify({'success': True})


@app.route('/api/pages/bulk', methods=['PUT'])
def bulk_update_pages():
    """Bulk update pages."""
    data = request.get_json()
    if not data or 'ids' not in data or 'updates' not in data:
        return jsonify({'error': 'ids and updates are required'}), 400

    ids = [int(i) for i in data.get('ids', []) if str(i).isdigit()]
    updates_data = data.get('updates', {})
    if not ids or not updates_data:
        return jsonify({'error': 'No valid ids or updates provided'}), 400

    updates = []
    values = []

    if 'url' in updates_data:
        updates.append('url = ?')
        values.append(updates_data['url'])
    if 'name' in updates_data:
        updates.append('name = ?')
        values.append(updates_data['name'])
    if 'duration' in updates_data:
        updates.append('duration = ?')
        values.append(updates_data['duration'])
    if 'enabled' in updates_data:
        updates.append('enabled = ?')
        values.append(1 if updates_data['enabled'] else 0)
    if 'position' in updates_data:
        updates.append('position = ?')
        values.append(updates_data['position'])
    if 'display_id' in updates_data:
        updates.append('display_id = ?')
        values.append(updates_data['display_id'] if updates_data['display_id'] else None)
    if 'refresh' in updates_data:
        updates.append('refresh = ?')
        values.append(1 if updates_data['refresh'] else 0)
    if 'refresh_interval' in updates_data:
        updates.append('refresh_interval = ?')
        values.append(max(1, int(updates_data['refresh_interval'])))
    if 'schedule_enabled' in updates_data:
        updates.append('schedule_enabled = ?')
        values.append(1 if updates_data['schedule_enabled'] else 0)
    if 'schedule_start' in updates_data:
        updates.append('schedule_start = ?')
        values.append(updates_data['schedule_start'])
    if 'schedule_end' in updates_data:
        updates.append('schedule_end = ?')
        values.append(updates_data['schedule_end'])
    if 'schedule_ranges' in updates_data:
        schedule_ranges = updates_data['schedule_ranges']
        if isinstance(schedule_ranges, list):
            schedule_ranges = json.dumps(schedule_ranges)
        updates.append('schedule_ranges = ?')
        values.append(schedule_ranges)

    if not updates:
        return jsonify({'error': 'No valid updates provided'}), 400

    conn = get_db()
    for page_id in ids:
        conn.execute(
            f'UPDATE pages SET {", ".join(updates)} WHERE id = ?',
            values + [page_id]
        )
    conn.commit()
    conn.close()

    socketio.emit('pages_updated', {'action': 'bulk_update'})
    return jsonify({'success': True, 'updated': len(ids)})


@app.route('/api/pages/bulk', methods=['DELETE'])
def bulk_delete_pages():
    """Bulk delete pages."""
    data = request.get_json()
    if not data or 'ids' not in data:
        return jsonify({'error': 'ids are required'}), 400

    ids = [int(i) for i in data.get('ids', []) if str(i).isdigit()]
    if not ids:
        return jsonify({'error': 'No valid ids provided'}), 400

    placeholders = ','.join(['?'] * len(ids))
    conn = get_db()
    rows = conn.execute(f'SELECT * FROM pages WHERE id IN ({placeholders})', ids).fetchall()
    pages_found = [dict_from_row(r) for r in rows]

    for page in pages_found:
        if page['type'] == 'image' and page['filename']:
            filepath = os.path.join(UPLOAD_FOLDER, page['filename'])
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass

    conn.execute(f'DELETE FROM pages WHERE id IN ({placeholders})', ids)
    conn.commit()
    conn.close()

    socketio.emit('pages_updated', {'action': 'bulk_delete'})
    return jsonify({'success': True, 'deleted': len(ids)})


@app.route('/api/pages/reorder', methods=['POST'])
def reorder_pages():
    """Reorder pages by providing list of IDs in desired order."""
    data = request.get_json()

    if not data or 'order' not in data:
        return jsonify({'error': 'Order list is required'}), 400

    conn = get_db()

    for position, page_id in enumerate(data['order']):
        conn.execute(
            'UPDATE pages SET position = ? WHERE id = ?',
            (position, page_id)
        )

    conn.commit()
    conn.close()

    # Notify all kiosks of update
    socketio.emit('pages_updated', {'action': 'reorder'})

    return jsonify({'success': True})


@app.route('/api/pages/urls', methods=['GET'])
def get_page_urls():
    """Get list of URLs for browser tabs (used by tab-based kiosk).

    Returns URLs in the order they should be opened as browser tabs.
    For images, returns the viewer URL. For regular pages, returns the URL directly.
    """
    display_id = request.args.get('display_id')

    conn = get_db()
    if display_id:
        pages = conn.execute(
            '''SELECT * FROM pages
               WHERE enabled = 1 AND (display_id = ? OR display_id IS NULL OR display_id = "")
               ORDER BY position ASC, id ASC''',
            (display_id,)
        ).fetchall()
    else:
        pages = conn.execute(
            'SELECT * FROM pages WHERE enabled = 1 ORDER BY position ASC, id ASC'
        ).fetchall()
    conn.close()

    urls = []
    durations = []
    for p in pages:
        page = dict_from_row(p)
        # Skip pages not active at current time
        if not is_page_active_now(page):
            continue
        if page['type'] == 'image' and page['filename']:
            # For images, use the viewer URL
            urls.append(f"/view/image/{page['filename']}")
        else:
            urls.append(page['url'])
        durations.append(page.get('duration', 30))

    # Fallback to default image if no pages available
    if not urls:
        urls = ['/static/backup.html']
        durations = [30]

    return jsonify({
        'urls': urls,
        'durations': durations,
        'count': len(urls)
    })


# === Image Upload API ===

@app.route('/api/images', methods=['POST'])
def upload_image():
    """Upload an image file."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not allowed. Use: ' + ', '.join(ALLOWED_EXTENSIONS)}), 400

    # Generate unique filename
    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    file.save(filepath)

    # Get form data
    name = request.form.get('name', file.filename)
    duration = int(request.form.get('duration', 30))
    display_id = request.form.get('display_id', None)
    if display_id == '':
        display_id = None
    refresh = request.form.get('refresh', '').lower() in ('true', '1', 'yes', 'on')
    refresh_interval = int(request.form.get('refresh_interval', 1) or 1)
    schedule_enabled = request.form.get('schedule_enabled', '').lower() in ('true', '1', 'yes', 'on')
    schedule_start = request.form.get('schedule_start') or None
    schedule_end = request.form.get('schedule_end') or None
    schedule_ranges = request.form.get('schedule_ranges') or None

    # Add to database
    conn = get_db()
    try:
        max_pos = conn.execute('SELECT MAX(position) FROM pages').fetchone()[0]
        next_pos = (max_pos or 0) + 1

        cursor = conn.execute(
            'INSERT INTO pages (url, name, duration, position, enabled, type, filename, display_id, refresh, refresh_interval, schedule_enabled, schedule_start, schedule_end, schedule_ranges) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                f"/view/image/{filename}",
                name,
                duration,
                next_pos,
                1,
                'image',
                filename,
                display_id,
                1 if refresh else 0,
                max(1, refresh_interval),
                1 if schedule_enabled else 0,
                schedule_start,
                schedule_end,
                schedule_ranges
            )
        )

        page = conn.execute('SELECT * FROM pages WHERE id = ?', (cursor.lastrowid,)).fetchone()
    except sqlite3.OperationalError as e:
        conn.close()
        return jsonify({'error': f'Database busy, please try again: {str(e)}'}), 503
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # Notify all kiosks
    socketio.emit('pages_updated', {'action': 'add'})

    page_dict = dict_from_row(page)
    page_dict['thumbnail'] = f"/uploads/{filename}"

    return jsonify(page_dict), 201


# === System API ===

@app.route('/api/system/hostname', methods=['GET'])
def get_system_hostname():
    """Get current system hostname and network information."""
    response_data = {
        'hostname': get_hostname(),
        'ip': get_local_ip()
    }

    # Add Tailscale information if available
    tailscale_ip = get_tailscale_ip()
    if tailscale_ip:
        response_data['tailscale_ip'] = tailscale_ip
        response_data['tailscale_active'] = is_tailscale_active()

    return jsonify(response_data)


@app.route('/api/system/hostname', methods=['PUT'])
def set_system_hostname():
    """Change system hostname (requires sudo privileges)."""
    data = request.get_json()

    if not data or not data.get('hostname'):
        return jsonify({'error': 'Hostname is required'}), 400

    new_hostname = data['hostname'].strip()

    # Validate hostname
    if not new_hostname or len(new_hostname) > 63:
        return jsonify({'error': 'Invalid hostname'}), 400

    # Only allow alphanumeric and hyphens
    if not all(c.isalnum() or c == '-' for c in new_hostname):
        return jsonify({'error': 'Hostname can only contain letters, numbers, and hyphens'}), 400

    try:
        # Update /etc/hostname
        subprocess.run(
            ['sudo', 'hostnamectl', 'set-hostname', new_hostname],
            check=True,
            capture_output=True
        )
        try:
            with open('/etc/hosts', 'r', encoding='utf-8') as hosts_file:
                hosts_content = hosts_file.read()
        except Exception:
            hosts_content = ''

        if '127.0.1.1' in hosts_content:
            subprocess.run(
                ['sudo', '/usr/bin/sed', '-i', f's/^127.0.1.1.*/127.0.1.1\\t{new_hostname}/', '/etc/hosts'],
                check=True,
                capture_output=True
            )
        else:
            subprocess.run(
                ['sudo', '/usr/bin/tee', '-a', '/etc/hosts'],
                input=f"127.0.1.1\t{new_hostname}\n",
                text=True,
                check=True,
                capture_output=True
            )

        return jsonify({
            'success': True,
            'hostname': new_hostname,
            'message': 'Hostname changed. A reboot is recommended for full effect.'
        })
    except subprocess.CalledProcessError as e:
        return jsonify({'error': f'Failed to change hostname: {e.stderr.decode()}'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/system/reboot', methods=['POST'])
def reboot():
    """Reboot the system (requires sudo privileges)."""
    try:
        reboot_system()
        return jsonify({'success': True, 'message': 'Reboot initiated'}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to reboot: {str(e)}'}), 500


@app.route('/api/system/restart-displays', methods=['POST'])
def restart_displays():
    """Restart kiosk service on all connected displays."""
    socketio.emit('restart_kiosk')
    return jsonify({
        'success': True,
        'message': f'Restart command sent to {len(connected_displays)} display(s)'
    })


@app.route('/api/system/reboot-all', methods=['POST'])
def reboot_all():
    """Reboot all connected displays and the master."""
    # Send reboot command to all clients first
    socketio.emit('reboot_system')

    # Schedule master reboot after a short delay to allow response
    def delayed_reboot():
        import time
        time.sleep(2)
        try:
            reboot_system()
        except Exception:
            pass

    import threading
    threading.Thread(target=delayed_reboot, daemon=True).start()

    return jsonify({
        'success': True,
        'message': f'Reboot command sent to {len(connected_displays)} display(s). Master rebooting in 2 seconds.'
    })


@app.route('/api/system/wifi', methods=['POST'])
def push_wifi():
    """Push WiFi credentials to connected displays."""
    data = request.get_json()

    if not data or not data.get('ssid'):
        return jsonify({'error': 'SSID is required'}), 400

    ssid = data['ssid'].strip()
    password = data.get('password', '').strip()
    hidden = data.get('hidden', False)

    # Validate SSID
    if not ssid or len(ssid) > 32:
        return jsonify({'error': 'Invalid SSID (max 32 characters)'}), 400

    # Build the WiFi config payload
    wifi_config = {
        'ssid': ssid,
        'password': password,
        'hidden': hidden
    }

    # Emit to all connected kiosks
    socketio.emit('wifi_config', wifi_config)

    # Also configure on the master Pi itself
    local_result = configure_wifi_locally(ssid, password, hidden)

    return jsonify({
        'success': True,
        'message': f'WiFi credentials pushed to {len(connected_displays)} display(s)',
        'local_configured': local_result
    })


@app.route('/api/system/update', methods=['POST'])
def push_update():
    """Tell connected kiosks to pull the latest bundle."""
    update_url = f"http://{get_local_ip()}:{PORT}/update.tgz"
    socketio.emit('update_client', {'url': update_url})

    return jsonify({
        'success': True,
        'message': f'Update command sent to {len(connected_displays)} display(s)',
        'url': update_url
    })


@socketio.on('wifi_result')
def handle_wifi_result(data):
    """Receive WiFi configuration result from kiosk."""
    socketio.emit('wifi_result', data)


@socketio.on('update_result')
def handle_update_result(data):
    """Receive update result from kiosk."""
    socketio.emit('update_result', data)


def configure_wifi_locally(ssid, password, hidden=False):
    """Configure WiFi on the local system using nmcli."""
    try:
        # Check if NetworkManager is available
        if not shutil.which('nmcli'):
            return False

        # Build nmcli command
        cmd = ['sudo', 'nmcli', 'device', 'wifi', 'connect', ssid]

        if password:
            cmd.extend(['password', password])

        if hidden:
            cmd.extend(['hidden', 'yes'])

        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        return True
    except subprocess.CalledProcessError:
        # Try alternative: add connection profile
        try:
            if password:
                subprocess.run([
                    'sudo', 'nmcli', 'connection', 'add',
                    'type', 'wifi',
                    'con-name', ssid,
                    'ssid', ssid,
                    'wifi-sec.key-mgmt', 'wpa-psk',
                    'wifi-sec.psk', password
                ], check=True, capture_output=True, timeout=30)
            else:
                subprocess.run([
                    'sudo', 'nmcli', 'connection', 'add',
                    'type', 'wifi',
                    'con-name', ssid,
                    'ssid', ssid
                ], check=True, capture_output=True, timeout=30)
            return True
        except Exception:
            return False
    except Exception:
        return False


# === Displays API ===

@app.route('/api/displays', methods=['GET'])
def get_displays():
    """Get all connected displays."""
    return jsonify(serialize_displays())


@app.route('/api/status', methods=['GET'])
def get_status():
    """Get overall system status."""
    status_data = {
        'displays': len(connected_displays),
        'hostname': get_hostname(),
        'ip': get_local_ip()
    }

    # Add Tailscale information if available
    tailscale_ip = get_tailscale_ip()
    if tailscale_ip:
        status_data['tailscale_ip'] = tailscale_ip
        status_data['tailscale_active'] = is_tailscale_active()

    return jsonify(status_data)


@app.route('/api/control', methods=['POST'])
def control_kiosk():
    """Send control command to kiosk(s)."""
    data = request.get_json()

    if not data or 'action' not in data:
        return jsonify({'error': 'Action is required'}), 400

    action = data['action']
    valid_actions = ['pause', 'resume', 'next', 'prev', 'refresh', 'goto', 'login_mode', 'exit_login_mode', 'admin_mode', 'exit_admin_mode', 'tailscale_auth']

    if action not in valid_actions:
        return jsonify({'error': f'Invalid action. Must be one of: {valid_actions}'}), 400

    payload = {'action': action}
    if action == 'goto' and 'page_id' in data:
        payload['page_id'] = data['page_id']
    if action == 'tailscale_auth' and 'authkey' in data:
        payload['authkey'] = data['authkey']

    # Target specific display or broadcast to all
    display_id = data.get('display_id')

    if display_id:
        # Send to specific display
        socketio.emit('control', payload, room=display_id)
    else:
        # Broadcast to all kiosks
        socketio.emit('control', payload)

    return jsonify({'success': True, 'action': action})


@app.route('/api/displays/sync', methods=['POST'])
def sync_displays():
    """Align all displays to the same page at the same time."""
    data = request.get_json(silent=True) or {}
    page_id = data.get('page_id')
    delay_ms = data.get('delay_ms', 3000)
    try:
        delay_ms = int(delay_ms)
    except Exception:
        delay_ms = 3000
    sync_at = time.time() + max(0, delay_ms) / 1000.0
    if data.get('reload'):
        socketio.emit('pages_updated', {'action': 'sync'})
    socketio.emit('sync', {
        'sync_at': sync_at,
        'page_id': page_id,
        'sync_enabled': SETTINGS.get('sync_enabled', True)
    })
    return jsonify({'success': True, 'sync_at': sync_at})


@app.route('/api/settings/sync', methods=['GET', 'POST'])
def sync_settings():
    """Get or set sync mode."""
    if request.method == 'GET':
        return jsonify({'sync_enabled': SETTINGS.get('sync_enabled', True)})

    data = request.get_json(silent=True) or {}
    sync_enabled = bool(data.get('sync_enabled', True))
    SETTINGS['sync_enabled'] = sync_enabled
    save_settings(SETTINGS)
    return jsonify({'sync_enabled': SETTINGS.get('sync_enabled', True)})


@app.route('/api/settings/tailscale', methods=['GET', 'POST'])
def tailscale_settings():
    """Get or set Tailscale auth key (stored server-side)."""
    if request.method == 'GET':
        has_key = bool(SETTINGS.get('tailscale_authkey'))
        return jsonify({'has_key': has_key})

    data = request.get_json(silent=True) or {}
    authkey = (data.get('authkey') or '').strip()
    if not authkey:
        return jsonify({'error': 'Auth key is required'}), 400

    SETTINGS['tailscale_authkey'] = authkey
    SETTINGS['tailscale_authkey_set_at'] = datetime.now().isoformat()
    save_settings(SETTINGS)

    if data.get('push'):
        socketio.emit('control', {
            'action': 'tailscale_auth',
            'authkey': authkey
        })

    return jsonify({'success': True, 'pushed': bool(data.get('push'))})


# WebSocket Events

@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    print(f'Client connected: {request.sid}')
    # Send current display list to web clients
    emit('displays_updated', serialize_displays())


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    sid = request.sid
    print(f'Client disconnected: {sid}')

    # Remove from connected displays if it was a kiosk
    if sid in connected_displays:
        hostname = connected_displays[sid].get('hostname', 'Unknown')
        del connected_displays[sid]
        print(f'Kiosk disconnected: {hostname}')
        # Notify web clients
        socketio.emit('displays_updated', serialize_displays())


@socketio.on('kiosk_connect')
def handle_kiosk_connect(data=None):
    """Handle kiosk client connection."""
    sid = request.sid
    info = data or {}
    hostname = info.get('hostname', 'Unknown')

    # Check if this hostname is already connected (deduplication)
    # If so, remove the old entry to avoid duplicates
    existing_sids = [s for s, d in connected_displays.items() if d.get('hostname') == hostname and s != sid]
    for old_sid in existing_sids:
        del connected_displays[old_sid]
        print(f'Removed duplicate connection for hostname: {hostname}')

    connected_displays[sid] = {
        'hostname': hostname,
        'ip': info.get('ip', request.remote_addr),
        'tailscale_ip': info.get('tailscale_ip'),
        'connection_type': info.get('connection_type', 'unknown'),
        'current_page_id': None,
        'current_url': None,
        'paused': False,
        'admin_mode_active': False,
        'last_seen': datetime.now().isoformat()
    }

    print(f'Kiosk connected: {hostname} ({request.remote_addr})')

    # Notify web clients
    socketio.emit('displays_updated', serialize_displays())


@socketio.on('kiosk_status')
def handle_kiosk_status(data):
    """Handle status update from kiosk."""
    sid = request.sid
    hostname = data.get('hostname')

    # If this sid isn't tracked but we have hostname, register it
    if sid not in connected_displays and hostname:
        # Remove any existing entries with this hostname (deduplication)
        existing_sids = [s for s, d in connected_displays.items() if d.get('hostname') == hostname]
        for old_sid in existing_sids:
            del connected_displays[old_sid]

        connected_displays[sid] = {
            'hostname': hostname,
            'ip': data.get('ip', request.remote_addr),
            'tailscale_ip': data.get('tailscale_ip'),
            'connection_type': data.get('connection_type', 'unknown'),
            'current_page_id': None,
            'current_url': None,
            'paused': False,
            'admin_mode_active': False,
            'last_seen': datetime.now().isoformat()
        }

    if sid in connected_displays:
        connected_displays[sid].update({
            'current_page_id': data.get('current_page_id'),
            'current_url': data.get('current_url'),
            'paused': data.get('paused', False),
            'admin_mode_active': data.get('admin_mode_active', connected_displays[sid].get('admin_mode_active', False)),
            'current_index': data.get('current_index'),
            'total_pages': data.get('total_pages'),
            'safe_mode': data.get('safe_mode', False),
            'tailscale_ip': data.get('tailscale_ip'),
            'connection_type': data.get('connection_type', connected_displays[sid].get('connection_type', 'unknown')),
            'last_seen': datetime.now().isoformat()
        })

        # Notify web clients
        socketio.emit('displays_updated', serialize_displays())


@socketio.on('kiosk_health')
def handle_kiosk_health(data):
    """Handle health metrics from kiosk."""
    sid = request.sid
    hostname = data.get('hostname')

    if sid not in connected_displays and hostname:
        connected_displays[sid] = {
            'hostname': hostname,
            'ip': data.get('ip', request.remote_addr),
            'current_page_id': None,
            'current_url': None,
            'paused': False,
            'last_seen': datetime.now().isoformat()
        }

    if sid in connected_displays:
        connected_displays[sid].update({
            'temp_c': data.get('temp_c'),
            'mem_total_mb': data.get('mem_total_mb'),
            'mem_free_mb': data.get('mem_free_mb'),
            'uptime_sec': data.get('uptime_sec'),
            'wifi_rssi_dbm': data.get('wifi_rssi_dbm'),
            'health_seen': datetime.now().isoformat()
        })

        socketio.emit('displays_updated', serialize_displays())


@socketio.on('request_pages')
def handle_request_pages(data=None):
    """Send pages list to kiosk."""
    sid = request.sid
    display_hostname = None

    # Get hostname from connected display info or from request data
    if sid in connected_displays:
        display_hostname = connected_displays[sid].get('hostname')
    if data and data.get('hostname'):
        display_hostname = data.get('hostname')

    conn = get_db()
    if display_hostname:
        # Get pages for this specific display OR global pages (no display assigned)
        pages = conn.execute(
            '''SELECT * FROM pages
               WHERE enabled = 1 AND (display_id = ? OR display_id IS NULL OR display_id = "")
               ORDER BY position ASC, id ASC''',
            (display_hostname,)
        ).fetchall()
    else:
        pages = conn.execute(
            'SELECT * FROM pages WHERE enabled = 1 ORDER BY position ASC, id ASC'
        ).fetchall()
    conn.close()

    result = []
    for p in pages:
        page_dict = dict_from_row(p)
        # Skip pages not active at current time
        if not is_page_active_now(page_dict):
            continue
        # For images, ensure we use the viewer URL
        if page_dict['type'] == 'image' and page_dict['filename']:
            page_dict['url'] = f"/view/image/{page_dict['filename']}"
        result.append(page_dict)

    # Fallback to default image if no pages available
    if not result:
        result = [{
            'id': 0,
            'url': '/static/backup.html',
            'name': 'Default',
            'duration': 30,
            'type': 'image',
            'enabled': 1
        }]

    emit('pages_list', result)
    emit('pages_sync', {
        'pages': result,
        'server_time': time.time(),
        'sync_enabled': SETTINGS.get('sync_enabled', True)
    })


def schedule_checker_thread():
    """Background thread that checks for schedule transitions every minute."""
    last_active_pages = set()

    while True:
        try:
            conn = get_db()
            pages = conn.execute(
                'SELECT id, schedule_enabled, schedule_start, schedule_end, schedule_ranges FROM pages WHERE enabled = 1'
            ).fetchall()
            conn.close()

            current_active = set()
            for p in pages:
                page = dict_from_row(p)
                if is_page_active_now(page):
                    current_active.add(page['id'])

            # If active pages changed, notify all kiosks
            if current_active != last_active_pages:
                socketio.emit('pages_updated', {'action': 'schedule_change'})
                last_active_pages = current_active

        except Exception as e:
            print(f'Schedule checker error: {e}')

        time.sleep(60)  # Check every minute


if __name__ == '__main__':
    init_db()

    # Start background schedule checker
    schedule_thread = threading.Thread(target=schedule_checker_thread, daemon=True)
    schedule_thread.start()

    print(f'Starting Pi Kiosk Server on http://{HOST}:{PORT}')
    print(f'Hostname: {get_hostname()}')
    print(f'Local IP: {get_local_ip()}')
    socketio.run(app, host=HOST, port=PORT, debug=False)
