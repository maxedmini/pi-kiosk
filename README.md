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

## Cross-Network Access with Tailscale

**New Feature**: Access your pi-kiosk server remotely from anywhere using Tailscale VPN!

### What is Tailscale?

Tailscale creates a secure private network between your devices, allowing you to access your pi-kiosk server from home, office, or anywhere with internet - no port forwarding required!

### Use Cases

- **Remote admin**: Manage your venue's displays from home
- **Travel**: Update content while away
- **Multiple locations**: Server and clients on different networks
- **Mobile management**: Control displays from your phone anywhere

### Setup (One-Time)

#### 1. Install Master Pi with Tailscale

```bash
# Install pi-kiosk with Tailscale enabled
sudo ./install.sh master --enable-tailscale

# Follow the authentication URL shown (open in browser on any device)
# This authorizes the Pi on your Tailscale network
```

After installation, the Pi will have two IPs:
- **Local IP**: `192.168.x.x` (for same-network access)
- **Tailscale IP**: `100.x.x.x` (for remote access from anywhere)

#### 2. Install Client Pis with Tailscale

```bash
# From same network (faster initial setup)
curl -sSL http://192.168.1.100:5000/install.sh | \
  sudo bash -s -- client 192.168.1.100 --enable-tailscale

# OR from different network (using Tailscale IP)
curl -sSL http://100.64.0.1:5000/install.sh | \
  sudo bash -s -- client 100.64.0.1 --enable-tailscale

# Follow the authentication URL for this client
```

**Smart Connection**: Clients automatically use local network when available (fastest), and fall back to Tailscale when remote!

#### 3. Setup Admin Access (Laptop/Phone)

**On Your Laptop:**
```bash
# macOS
brew install tailscale
tailscale up  # Follow auth URL

# Windows
# Download from https://tailscale.com/download
# Install and sign in

# Linux
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up
```

**On Your Phone:**
- Install Tailscale app from App Store / Play Store
- Sign in with same account
- Toggle Tailscale ON

#### 4. Access from Anywhere

Once Tailscale is installed on your admin device:

```
# Access web interface using Tailscale IP
http://100.64.0.1:5000
```

**Bookmark this URL** - it works from anywhere with internet!

### How It Works (Hybrid Mode)

The pi-kiosk hybrid connection system automatically optimizes your network path:

**For Clients (Slaves):**
1. Tries local network first (192.168.x.x) - fastest, <1ms latency
2. Falls back to Tailscale (100.x.x.x) if local fails - ~10-30ms latency
3. Auto-switches to local when available (e.g., if Pi moves networks)
4. Seamless reconnection with no service interruption

**For Admin (Web Interface):**
- **Same network**: Connect via local IP or Tailscale IP
- **Different network**: Use Tailscale IP - works from anywhere!
- **Mobile**: Access from phone/tablet via Tailscale app

**Connection Type Display:**
The web interface shows how each display is connected:
- `(local)` - Direct local network connection
- `(tailscale)` - Connected via Tailscale VPN

### Example Scenarios

#### Scenario 1: Remote Admin Management (Common)
**Setup**: Server + 3 clients all on venue WiFi (192.168.1.x)

**Admin Actions**:
- **At venue**: Access `http://192.168.1.100:5000` OR `http://100.64.0.1:5000`
- **At home**: Access `http://100.64.0.1:5000` (works automatically!)
- **On phone**: Open Tailscale app, visit `http://100.64.0.1:5000` in browser
- **Traveling**: From hotel/airport, same Tailscale URL works

**Benefit**: Manage displays from anywhere without port forwarding!

#### Scenario 2: Mixed Deployment
**Setup**:
- Server at office (192.168.10.50)
- 2 clients at office (use local network - fast)
- 1 client at remote location (uses Tailscale)

**Result**: Office clients connect locally, remote client via Tailscale, all managed from one interface!

#### Scenario 3: Trade Show
- Set up on trade show WiFi
- Admin monitors from booth OR hotel via Tailscale
- After show, move clients to different locations → auto-switch to Tailscale

### Getting Your Tailscale IP

```bash
# On the Pi
tailscale ip -4

# Or check the web interface header
# Shows both local and Tailscale IPs
```

### Daily Usage

Once set up, remote access is seamless:

1. Open Tailscale app on your device
2. Visit `http://100.64.0.1:5000` in browser
3. Manage displays normally!

No VPN configuration, no port forwarding, no complex setup - it just works!

### Troubleshooting Tailscale

#### Check Tailscale Status
```bash
# On the Pi
tailscale status

# Should show "logged in" and list connected devices
```

#### Tailscale Not Connected
```bash
# Restart Tailscale
sudo systemctl restart tailscaled

# Re-authenticate
sudo tailscale up
```

#### Can't Access via Tailscale IP
1. Verify Tailscale is running on both server and admin device:
   ```bash
   tailscale status
   ```
2. Check firewall isn't blocking (Tailscale handles NAT automatically)
3. Ensure both devices are on same Tailscale network (same account)
4. Try pinging the Tailscale IP:
   ```bash
   ping 100.64.0.1
   ```

#### Client Stuck on Tailscale (Want Local)
Connection optimization runs every 60 seconds. If client should be on local network but uses Tailscale:

1. Verify both are on same LAN
2. Check server port 5000 is accessible on local network:
   ```bash
   curl http://192.168.1.100:5000/api/status
   ```
3. Wait up to 60 seconds for automatic optimization
4. Or restart client service to reconnect immediately:
   ```bash
   sudo systemctl restart pi-kiosk
   ```

#### View Connection Type in Logs
```bash
# Check which connection type is being used
journalctl -u pi-kiosk -f

# Look for lines like:
# "Connected successfully via http://192.168.1.100:5000 (local)"
# "Connected successfully via http://100.64.0.1:5000 (tailscale)"
```

### Security Notes

- **Encrypted**: Tailscale uses WireGuard (military-grade encryption)
- **Authenticated**: Each device requires OAuth authorization
- **Private**: No ports exposed to public internet
- **ACLs**: Optional fine-grained access control available
- **Free**: Up to 100 devices at no cost

### Tailscale vs Port Forwarding

| Feature | Tailscale (Recommended) | Port Forwarding |
|---------|-------------------------|-----------------|
| Setup Complexity | Easy (one command) | Complex (router config) |
| Security | Encrypted by default | Manual setup required |
| Works on cellular | ✓ Yes | ✗ No |
| Works behind NAT | ✓ Yes | ✗ Needs static IP |
| Multiple locations | ✓ Easy | ✗ Hard |
| Mobile access | ✓ Built-in app | ✗ Manual VPN |
| Cost | Free (100 devices) | Free (if you have static IP) |

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
