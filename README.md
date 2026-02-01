# Pi Kiosk Display Manager

A complete kiosk display system for Raspberry Pi 4 that rotates through web pages and images with a web-based management interface. Supports multiple Pi displays managed from a single dashboard.

## Features

- **Web Page Rotation** - Display multiple websites in rotation with customizable timing per page
- **Image Support** - Upload and display static images alongside web pages
- **Multi-Pi Support** - Manage multiple Raspberry Pi displays from one web interface
- **Login Persistence** - Browser remembers login sessions for authenticated websites
- **Remote Management** - Control all displays from any device on your network
- **Auto-Start** - Automatically starts on boot with systemd services
- **Hidden Cursor** - Clean kiosk display with no visible mouse cursor
- **Screen Always On** - Disables screen blanking and power saving

---

## Quick Start

### Option 1: Install from Mac (Easiest)

1. Double-click **`Pi Kiosk Installer.command`** on your Mac
2. Enter your Pi's IP address and username (default: `pi`)
3. Select **Master** for your first Pi
4. Enter your Pi's password when prompted
5. Reboot the Pi when installation completes

### Option 2: Install Directly on Pi

```bash
# Copy the pi-kiosk folder to your Pi, then:
cd pi-kiosk
sudo ./install.sh master
sudo reboot
```

### Option 3: Install Client from Master

After setting up a master Pi, install additional display clients:

```bash
# On the client Pi, run:
curl -sSL http://<master-ip>:5000/install.sh | sudo bash -s -- client <master-ip>
sudo reboot
```

---

## Accessing the Web Interface

After installation and reboot, access the management interface from any browser:

```
http://<pi-ip-address>:5000
```

---

## Using the Web Interface

### Adding Web Pages

1. In the **Add Content** section, select the **Add URL** tab
2. Enter the website URL (e.g., `https://example.com`)
3. Optionally add a name for easy identification
4. Set the duration (how long to display before rotating)
5. Click **Add Page**

### Uploading Images

1. Select the **Upload Image** tab
2. Click to select an image file (PNG, JPG, GIF, WebP, BMP)
3. Optionally add a name
4. Set the display duration
5. Click **Upload Image**

### Managing Pages

- **Drag and drop** pages to reorder them
- Click **Edit** to change URL, name, duration, or enable/disable
- Click **Delete** to remove a page
- Click **Go** to immediately jump to that page on all displays

### Controlling Displays

**Global Controls** (affect all connected displays):
- **Prev/Next** - Skip to previous or next page
- **Pause/Resume** - Stop or start rotation
- **Refresh** - Reload the current page

**Per-Display Controls** (in the Connected Displays section):
- Each display shows its own status and controls
- Control individual displays independently

---

## Handling Websites That Require Login

The kiosk uses a persistent browser profile, so login sessions are saved. To log in to websites:

### Using Login Mode

1. Click the **Login Mode** button in the web interface
2. This pauses rotation and **shows the mouse cursor** on the Pi display
3. Connect a USB mouse and keyboard to your Pi
4. Navigate to and log in to your websites
5. Click **Exit Login Mode** when finished
6. Your login sessions will persist, even after reboot

### Tips for Authenticated Pages

- Log in once, and the browser remembers your session
- For pages with session timeouts, you may need to re-login periodically
- Some sites (like Google) may require you to check "Remember me" or "Stay signed in"

---

## Multi-Pi Setup

### Architecture

```
┌─────────────────────────────────────────┐
│           MASTER PI                      │
│  - Runs web server (port 5000)          │
│  - Hosts management interface           │
│  - Can also display content (optional)  │
└─────────────────┬───────────────────────┘
                  │
      ┌───────────┼───────────┐
      │           │           │
      ▼           ▼           ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│ Client 1 │ │ Client 2 │ │ Client N │
│ Display  │ │ Display  │ │ Display  │
└──────────┘ └──────────┘ └──────────┘
```

### Setup Steps

1. **Install Master Pi** (first):
   ```bash
   sudo ./install.sh master
   ```

2. **Install Client Pis** (additional displays):
   ```bash
   curl -sSL http://<master-ip>:5000/install.sh | sudo bash -s -- client <master-ip>
   ```

3. All displays will appear in the **Connected Displays** section of the web interface

4. All displays show the same page rotation by default

---

## System Settings

### Changing Hostname

1. Click the **gear icon** in the top-right of the web interface
2. Enter a new hostname
3. Click **Save Hostname**
4. Reboot for the change to fully apply

### Viewing System Info

The header shows:
- Current hostname
- IP address
- Number of connected displays

---

## File Structure

```
pi-kiosk/
├── Pi Kiosk Installer.command  # Mac installer (double-click)
├── install.sh                   # Linux installer script
├── server.py                    # Flask web server
├── kiosk.py                     # Chromium kiosk controller
├── requirements.txt             # Python dependencies
├── uploads/                     # Uploaded images stored here
├── pages.db                     # SQLite database (auto-created)
├── templates/
│   ├── index.html              # Web management interface
│   └── image.html              # Fullscreen image viewer
├── static/
│   ├── style.css               # Styles
│   └── app.js                  # Frontend JavaScript
└── systemd/                    # Service file templates
```

---

## Services

The system uses two systemd services:

### pi-kiosk-server (Master only)
- Runs the Flask web server
- Manages the database and API
- Handles WebSocket connections

```bash
sudo systemctl status pi-kiosk-server
sudo systemctl restart pi-kiosk-server
journalctl -u pi-kiosk-server -f  # View logs
```

### pi-kiosk (All Pis)
- Runs the Chromium kiosk display
- Connects to the server (local or remote)
- Handles page rotation

```bash
sudo systemctl status pi-kiosk
sudo systemctl restart pi-kiosk
journalctl -u pi-kiosk -f  # View logs
```

---

## Troubleshooting

### Display not showing anything

```bash
# Check if the kiosk service is running
sudo systemctl status pi-kiosk

# View kiosk logs
journalctl -u pi-kiosk -f

# Restart the service
sudo systemctl restart pi-kiosk
```

### Can't access web interface

```bash
# Check if the server is running (master only)
sudo systemctl status pi-kiosk-server

# Check the IP address
hostname -I

# Verify port 5000 is listening
sudo netstat -tlnp | grep 5000
```

### Client not connecting to master

1. Verify the master IP is correct
2. Check that port 5000 is accessible (no firewall blocking)
3. Ensure the master server is running:
   ```bash
   # On master Pi:
   sudo systemctl status pi-kiosk-server
   ```

### Login sessions not persisting

- Ensure you're using Login Mode (not incognito)
- Check that the browser profile directory exists:
  ```bash
  ls -la ~/.config/pi-kiosk/chromium-profile
  ```
- Some websites may have short session timeouts

### Screen is blank/black

```bash
# Disable screen blanking manually
export DISPLAY=:0
xset s off
xset -dpms
xset s noblank
```

---

## Uninstalling

```bash
# Stop and disable services
sudo systemctl stop pi-kiosk pi-kiosk-server
sudo systemctl disable pi-kiosk pi-kiosk-server

# Remove service files
sudo rm /etc/systemd/system/pi-kiosk.service
sudo rm /etc/systemd/system/pi-kiosk-server.service

# Remove installation directory
sudo rm -rf /opt/pi-kiosk

# Remove browser profile (optional - removes saved logins)
rm -rf ~/.config/pi-kiosk
```

---

## API Reference

The server provides a REST API for programmatic control:

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/pages` | List all pages |
| POST | `/api/pages` | Add a new URL page |
| PUT | `/api/pages/<id>` | Update a page |
| DELETE | `/api/pages/<id>` | Delete a page |
| POST | `/api/pages/reorder` | Reorder pages |
| POST | `/api/images` | Upload an image |
| GET | `/api/displays` | List connected displays |
| POST | `/api/control` | Send control command |
| GET | `/api/system/hostname` | Get hostname |
| PUT | `/api/system/hostname` | Change hostname |

### Control Commands

```bash
# Pause all displays
curl -X POST http://<ip>:5000/api/control \
  -H "Content-Type: application/json" \
  -d '{"action": "pause"}'

# Resume rotation
curl -X POST http://<ip>:5000/api/control \
  -H "Content-Type: application/json" \
  -d '{"action": "resume"}'

# Next page
curl -X POST http://<ip>:5000/api/control \
  -H "Content-Type: application/json" \
  -d '{"action": "next"}'

# Go to specific page
curl -X POST http://<ip>:5000/api/control \
  -H "Content-Type: application/json" \
  -d '{"action": "goto", "page_id": 1}'
```

---

## Requirements

### Hardware
- Raspberry Pi 4 (recommended) or Pi 3
- MicroSD card (8GB+)
- Display connected via HDMI
- Network connection (Ethernet or WiFi)

### Software
- Raspberry Pi OS (Bullseye or newer)
- Python 3.7+
- Chromium browser (installed automatically)

---

## License

MIT License - Feel free to use and modify for your projects.
