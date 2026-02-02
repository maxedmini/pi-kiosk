#!/usr/bin/env python3
"""
Network helper utilities for pi-kiosk
Handles Tailscale integration and connection priority management
"""

import subprocess
import socket
import time
import json
import os
import re
from typing import List, Dict, Optional, Tuple


def get_tailscale_ip() -> Optional[str]:
    """
    Get the Tailscale IP address (100.x.x.x) for this device.
    Returns None if Tailscale is not installed or not running.
    """
    try:
        # Try using tailscale ip command (most reliable)
        result = subprocess.run(
            ['tailscale', 'ip', '-4'],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            ip = result.stdout.strip()
            if ip and ip.startswith('100.'):
                return ip
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Fallback: Parse from tailscale status
    try:
        result = subprocess.run(
            ['tailscale', 'status', '--json'],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            status = json.loads(result.stdout)
            self_peer = status.get('Self', {})
            tailscale_ips = self_peer.get('TailscaleIPs', [])
            # Return first IPv4 address
            for ip in tailscale_ips:
                if ':' not in ip and ip.startswith('100.'):
                    return ip
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, KeyError):
        pass

    # Final fallback: Parse ip addr show tailscale0
    try:
        result = subprocess.run(
            ['ip', 'addr', 'show', 'tailscale0'],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            # Look for "inet 100.x.x.x/32"
            match = re.search(r'inet\s+(100\.\d+\.\d+\.\d+)', result.stdout)
            if match:
                return match.group(1)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None


def is_tailscale_active() -> bool:
    """
    Check if Tailscale daemon is running and connected.
    Returns True if Tailscale is active, False otherwise.
    """
    try:
        result = subprocess.run(
            ['tailscale', 'status'],
            capture_output=True,
            text=True,
            timeout=2
        )
        # If tailscale status succeeds and doesn't say "Stopped", it's active
        if result.returncode == 0:
            output = result.stdout.lower()
            # Check for common "not running" indicators
            if 'stopped' in output or 'not running' in output:
                return False
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return False


def get_tailscale_status() -> Dict[str, any]:
    """
    Get detailed Tailscale status information.
    Returns a dictionary with connection info, or empty dict if unavailable.
    """
    status_info = {
        'active': False,
        'ip': None,
        'hostname': None,
        'peers': [],
        'backend_state': 'Unknown'
    }

    try:
        result = subprocess.run(
            ['tailscale', 'status', '--json'],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)

            # Self info
            self_peer = data.get('Self', {})
            status_info['ip'] = get_tailscale_ip()
            status_info['hostname'] = self_peer.get('HostName', '')
            status_info['backend_state'] = data.get('BackendState', 'Unknown')
            status_info['active'] = status_info['backend_state'] in ['Running', 'Connected']

            # Peer info
            peers = data.get('Peer', {})
            for peer_key, peer_data in peers.items():
                status_info['peers'].append({
                    'hostname': peer_data.get('HostName', ''),
                    'dns_name': peer_data.get('DNSName', ''),
                    'online': peer_data.get('Online', False),
                    'last_seen': peer_data.get('LastSeen', ''),
                    'tailscale_ips': peer_data.get('TailscaleIPs', [])
                })
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass

    return status_info


def find_peer_by_name(name: str) -> Optional[Dict[str, any]]:
    """
    Find a Tailscale peer by hostname or DNS name.

    Args:
        name: Hostname or partial DNS name to search for (case-insensitive)

    Returns:
        Peer info dict with 'hostname', 'tailscale_ips', 'online', etc.
        Returns None if not found.
    """
    status = get_tailscale_status()
    if not status.get('active'):
        return None

    name_lower = name.lower().strip()

    for peer in status.get('peers', []):
        peer_hostname = peer.get('hostname', '').lower()
        peer_dns = peer.get('dns_name', '').lower()

        # Match by hostname or DNS name (partial match)
        if (name_lower == peer_hostname or
            name_lower in peer_hostname or
            name_lower in peer_dns or
            peer_dns.startswith(f"{name_lower}.")):
            return peer

    return None


def get_peer_tailscale_ip(name: str) -> Optional[str]:
    """
    Get the Tailscale IP (100.x.x.x) for a peer by name.

    Args:
        name: Hostname or DNS name of the peer

    Returns:
        IPv4 Tailscale address or None if not found
    """
    peer = find_peer_by_name(name)
    if not peer:
        return None

    tailscale_ips = peer.get('tailscale_ips', [])
    for ip in tailscale_ips:
        # Return first IPv4 address (100.x.x.x)
        if ':' not in ip and ip.startswith('100.'):
            return ip

    return None


def resolve_tailscale_name(name: str) -> Optional[str]:
    """
    Resolve a Tailscale Magic DNS name to an IP.
    Works with names like 'pi-server' or 'pi-server.tail1234.ts.net'

    Args:
        name: Tailscale hostname or FQDN

    Returns:
        Tailscale IP address or None
    """
    # First try direct peer lookup
    ip = get_peer_tailscale_ip(name)
    if ip:
        return ip

    # Try DNS resolution (works if Magic DNS is enabled)
    try:
        resolved = socket.gethostbyname(name)
        if resolved.startswith('100.'):
            return resolved
    except socket.gaierror:
        pass

    return None


def get_local_ip() -> Optional[str]:
    """
    Get the local network IP address (192.168.x.x, 10.x.x.x, etc.).
    Returns None if unable to determine.
    """
    try:
        # Create a socket connection to determine local IP
        # We don't actually connect, just use it to find which interface would be used
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        # Connect to Google DNS (doesn't actually send packets)
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        pass

    # Fallback: Try to get from hostname
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        if not local_ip.startswith('127.'):
            return local_ip
    except Exception:
        pass

    return None


def test_connection_health(url: str, timeout: int = 3) -> Tuple[bool, float]:
    """
    Test if a server URL is reachable and measure latency.

    Args:
        url: Server URL (e.g., 'http://192.168.1.100:5000')
        timeout: Connection timeout in seconds

    Returns:
        Tuple of (is_reachable: bool, latency_ms: float)
    """
    import urllib.request
    import urllib.error

    try:
        # Test the /api/status endpoint
        test_url = f"{url.rstrip('/')}/api/status"

        start_time = time.time()
        req = urllib.request.Request(test_url, method='GET')

        with urllib.request.urlopen(req, timeout=timeout) as response:
            latency = (time.time() - start_time) * 1000  # Convert to ms

            # Check if response is valid
            if response.status == 200:
                return (True, latency)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        pass

    return (False, float('inf'))


def scan_tailscale_peers_for_server(port: str = '5000', timeout: float = 1.0) -> Optional[str]:
    """
    Scan all online Tailscale peers to find one running a server on the specified port.

    Args:
        port: Port to check for server (default '5000')
        timeout: Connection timeout per peer in seconds

    Returns:
        Tailscale IP of the first peer responding on the port, or None if not found
    """
    if not is_tailscale_active():
        return None

    status = get_tailscale_status()
    peers = status.get('peers', [])

    # Only check online peers
    online_peers = [p for p in peers if p.get('online')]

    for peer in online_peers:
        for ts_ip in peer.get('tailscale_ips', []):
            # Skip IPv6 addresses
            if ':' in ts_ip or not ts_ip.startswith('100.'):
                continue

            # Quick TCP connect test to see if port is open
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                result = sock.connect_ex((ts_ip, int(port)))
                sock.close()

                if result == 0:
                    # Port is open, verify it's our server by checking /api/status
                    is_server, _ = test_connection_health(f'http://{ts_ip}:{port}', timeout=2)
                    if is_server:
                        return ts_ip
            except (socket.error, OSError):
                pass

    return None


def get_connection_candidates(configured_url: str, server_name: Optional[str] = None,
                               auto_scan_tailscale: bool = True) -> List[Dict[str, str]]:
    """
    Get list of server URLs to try, in priority order.
    Automatically discovers Tailscale IPs for fallback when local network fails.

    Args:
        configured_url: The URL configured by the user (--server argument)
        server_name: Optional Tailscale hostname of the server for auto-discovery
        auto_scan_tailscale: If True, automatically scan all Tailscale peers for server

    Returns:
        List of connection candidates with metadata:
        [
            {'url': 'http://192.168.1.100:5000', 'type': 'local', 'priority': 1},
            {'url': 'http://100.64.0.1:5000', 'type': 'tailscale', 'priority': 2}
        ]
    """
    candidates = []
    seen_urls = set()  # Track URLs to avoid duplicates

    # Extract host and port from configured URL
    configured_host = None
    configured_port = '5000'

    if configured_url:
        # Parse URL to extract host and port
        # Simple parsing for http://host:port format
        url_without_protocol = configured_url.replace('http://', '').replace('https://', '')
        if ':' in url_without_protocol:
            parts = url_without_protocol.split(':')
            configured_host = parts[0]
            configured_port = parts[1].rstrip('/')
        else:
            configured_host = url_without_protocol.rstrip('/')

    def add_candidate(url: str, conn_type: str, priority: int, hostname: str = None):
        """Helper to add candidate without duplicates"""
        if url not in seen_urls:
            seen_urls.add(url)
            candidate = {
                'url': url,
                'type': conn_type,
                'priority': priority
            }
            if hostname:
                candidate['hostname'] = hostname
            candidates.append(candidate)

    # Priority 0: localhost/127.x.x.x - highest priority (same machine)
    if configured_host and (
        configured_host == 'localhost' or
        configured_host.startswith('127.')
    ):
        add_candidate(f'http://{configured_host}:{configured_port}', 'localhost', 0)

    # Priority 1: If configured URL looks like a local IP, try it first
    if configured_host and (
        configured_host.startswith('192.168.') or
        configured_host.startswith('10.') or
        configured_host.startswith('172.')
    ):
        add_candidate(f'http://{configured_host}:{configured_port}', 'local', 1)

    # Priority 2: If configured URL looks like a Tailscale IP, add it
    if configured_host and configured_host.startswith('100.'):
        add_candidate(f'http://{configured_host}:{configured_port}', 'tailscale', 2)

    # Auto-discover Tailscale IP for the server
    tailscale_ip = None

    # Method 1: Use explicit server_name if provided
    if server_name and is_tailscale_active():
        tailscale_ip = get_peer_tailscale_ip(server_name)
        if tailscale_ip:
            add_candidate(f'http://{tailscale_ip}:{configured_port}', 'tailscale', 2, hostname=server_name)

    # Method 2: If configured host is a hostname (not IP), try to resolve via Tailscale
    if configured_host and not configured_host[0].isdigit() and is_tailscale_active():
        # It's a hostname, try Tailscale resolution
        tailscale_ip = resolve_tailscale_name(configured_host)
        if tailscale_ip:
            add_candidate(f'http://{tailscale_ip}:{configured_port}', 'tailscale', 2, hostname=configured_host)

        # Also try local DNS resolution for the hostname
        try:
            local_ip = socket.gethostbyname(configured_host)
            if local_ip.startswith(('192.168.', '10.', '172.')):
                add_candidate(f'http://{local_ip}:{configured_port}', 'local', 1)
            elif local_ip.startswith('100.'):
                add_candidate(f'http://{local_ip}:{configured_port}', 'tailscale', 2)
        except socket.gaierror:
            pass

    # Method 3: Auto-scan all Tailscale peers if no Tailscale candidate found yet
    # This is the "zero config" fallback - scans all online peers for a server
    if auto_scan_tailscale and not any(c['type'] == 'tailscale' for c in candidates):
        if is_tailscale_active():
            # Add all online peers as potential candidates
            status = get_tailscale_status()
            for peer in status.get('peers', []):
                if peer.get('online'):
                    for ts_ip in peer.get('tailscale_ips', []):
                        if ':' not in ts_ip and ts_ip.startswith('100.'):
                            add_candidate(
                                f'http://{ts_ip}:{configured_port}',
                                'tailscale',
                                2,
                                hostname=peer.get('hostname')
                            )

    # If no candidates yet, add the configured URL as-is
    if not candidates and configured_url:
        # Determine type based on IP pattern
        url_type = 'unknown'
        if configured_host:
            if configured_host.startswith('100.'):
                url_type = 'tailscale'
            elif configured_host.startswith(('192.168.', '10.', '172.')):
                url_type = 'local'

        add_candidate(configured_url, url_type, 1)

    # Sort by priority (local network first, then Tailscale)
    candidates.sort(key=lambda x: x['priority'])

    return candidates


def discover_server_addresses(local_ip: Optional[str] = None,
                               tailscale_name: Optional[str] = None,
                               port: str = '5000') -> List[Dict[str, str]]:
    """
    Discover all possible server addresses (local + Tailscale).

    This is the main function for automatic connection management.
    It will find all ways to reach the server and return them in priority order.

    Args:
        local_ip: Known local network IP of the server (e.g., '192.168.1.100')
        tailscale_name: Tailscale hostname of the server (e.g., 'pi-server')
        port: Server port (default '5000')

    Returns:
        List of connection candidates, local network prioritized over Tailscale
    """
    candidates = []
    seen = set()

    def add(url: str, conn_type: str, priority: int):
        if url not in seen:
            seen.add(url)
            candidates.append({'url': url, 'type': conn_type, 'priority': priority})

    # Add local IP if provided
    if local_ip:
        add(f'http://{local_ip}:{port}', 'local', 1)

    # Discover Tailscale IP
    if tailscale_name and is_tailscale_active():
        ts_ip = get_peer_tailscale_ip(tailscale_name)
        if ts_ip:
            add(f'http://{ts_ip}:{port}', 'tailscale', 2)

    # If we have local IP but no Tailscale name, try to find server in peers
    # by checking if any peer is reachable on the same port
    if local_ip and not tailscale_name and is_tailscale_active():
        status = get_tailscale_status()
        for peer in status.get('peers', []):
            if peer.get('online'):
                for ts_ip in peer.get('tailscale_ips', []):
                    if ':' not in ts_ip and ts_ip.startswith('100.'):
                        # Add as potential Tailscale fallback
                        add(f'http://{ts_ip}:{port}', 'tailscale', 2)

    candidates.sort(key=lambda x: x['priority'])
    return candidates


def get_optimal_server_url(configured_url: str, test_timeout: int = 2) -> Optional[Dict[str, any]]:
    """
    Determine the optimal server URL to use based on availability and latency.

    Args:
        configured_url: The URL configured by the user
        test_timeout: Timeout for each connection test

    Returns:
        Dictionary with optimal connection info:
        {
            'url': 'http://192.168.1.100:5000',
            'type': 'local',
            'latency_ms': 1.5,
            'priority': 1
        }
        Returns None if no connection is available.
    """
    candidates = get_connection_candidates(configured_url)

    for candidate in candidates:
        is_reachable, latency = test_connection_health(candidate['url'], timeout=test_timeout)
        if is_reachable:
            return {
                **candidate,
                'latency_ms': round(latency, 2)
            }

    return None


def install_tailscale() -> bool:
    """
    Install Tailscale on the system.
    Returns True if installation succeeded, False otherwise.

    Note: This requires root privileges and should be called from install.sh
    """
    try:
        # Download and run Tailscale install script
        install_cmd = "curl -fsSL https://tailscale.com/install.sh | sh"
        result = subprocess.run(
            install_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=120
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, Exception):
        return False


def setup_tailscale_auth(authkey: Optional[str] = None) -> Tuple[bool, str]:
    """
    Start Tailscale and authenticate the device.

    Args:
        authkey: Optional pre-auth key for unattended setup

    Returns:
        Tuple of (success: bool, message: str)
        If authkey is not provided, message contains the auth URL
    """
    try:
        if authkey:
            # Use auth key for unattended setup
            result = subprocess.run(
                ['tailscale', 'up', '--authkey', authkey],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                return (True, "Tailscale authenticated successfully")
            else:
                return (False, f"Authentication failed: {result.stderr}")
        else:
            # Interactive auth - start Tailscale and get auth URL
            result = subprocess.run(
                ['tailscale', 'up'],
                capture_output=True,
                text=True,
                timeout=30
            )

            # Parse output for auth URL
            output = result.stdout + result.stderr
            url_match = re.search(r'https://login\.tailscale\.com/[^\s]+', output)

            if url_match:
                auth_url = url_match.group(0)
                return (False, f"Please authenticate at: {auth_url}")
            elif result.returncode == 0:
                return (True, "Tailscale already authenticated")
            else:
                return (False, f"Failed to start Tailscale: {output}")
    except subprocess.TimeoutExpired:
        return (False, "Tailscale authentication timed out")
    except Exception as e:
        return (False, f"Error during authentication: {str(e)}")


if __name__ == '__main__':
    """Quick test of network helper functions"""
    import sys

    print("=== Network Helper Test ===")
    print(f"Local IP: {get_local_ip()}")
    print(f"Tailscale IP: {get_tailscale_ip()}")
    print(f"Tailscale Active: {is_tailscale_active()}")

    status = get_tailscale_status()
    print(f"\nTailscale Status:")
    print(f"  Backend State: {status.get('backend_state')}")
    print(f"  This device: {status.get('hostname')} ({status.get('ip')})")

    online_peers = []
    if status.get('peers'):
        print(f"\n  Tailscale Peers ({len(status['peers'])} total):")
        for peer in status['peers']:
            online = "online" if peer.get('online') else "offline"
            ips = ', '.join(peer.get('tailscale_ips', []))
            print(f"    - {peer.get('hostname')}: {ips} [{online}]")
            if peer.get('online'):
                online_peers.append(peer)

    # Test connection candidates with a local URL
    test_url = "http://192.168.1.100:5000"
    print(f"\n=== Connection Candidates (Auto-Scan Mode) ===")
    print(f"Configured URL: {test_url}")
    print(f"This simulates what kiosk.py does at startup:\n")

    candidates = get_connection_candidates(test_url, auto_scan_tailscale=True)

    local_candidates = [c for c in candidates if c['type'] == 'local']
    tailscale_candidates = [c for c in candidates if c['type'] == 'tailscale']

    if local_candidates:
        print(f"  Local network ({len(local_candidates)}):")
        for c in local_candidates:
            print(f"    [Priority {c['priority']}] {c['url']}")

    if tailscale_candidates:
        print(f"\n  Tailscale fallback ({len(tailscale_candidates)}):")
        for c in tailscale_candidates:
            hostname = c.get('hostname', '')
            if hostname:
                print(f"    [Priority {c['priority']}] {c['url']} ({hostname})")
            else:
                print(f"    [Priority {c['priority']}] {c['url']}")

    print(f"\n=== How it works ===")
    print("1. Kiosk tries local network first (192.168.x.x)")
    print("2. If local fails, tries each Tailscale peer on port 5000")
    print("3. Connects to first peer that responds")
    print("4. Periodically checks if local network becomes available again")

    # Test scanning for server
    if online_peers:
        print(f"\n=== Scanning for Server on Port 5000 ===")
        server_ip = scan_tailscale_peers_for_server(port='5000', timeout=2)
        if server_ip:
            # Find hostname for this IP
            for peer in online_peers:
                if server_ip in peer.get('tailscale_ips', []):
                    print(f"  Found server: {peer.get('hostname')} ({server_ip})")
                    break
            else:
                print(f"  Found server at: {server_ip}")
        else:
            print("  No server found on port 5000 among Tailscale peers")
