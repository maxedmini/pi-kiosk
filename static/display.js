// Pi Kiosk Display - iframe-based viewer with rotation
// Uses iframe for smooth page transitions without browser relaunch
// Note: Some external sites block iframes (X-Frame-Options) - use Tab Mode for those

const socket = io();
const viewer = document.getElementById('viewer');
const emptyState = document.getElementById('emptyState');

let pages = [];
let currentIndex = 0;
let paused = false;
let rotationTimer = null;
let systemInfo = { hostname: 'Unknown', ip: 'Unknown' };

async function loadSystemInfo() {
    try {
        const response = await fetch('/api/system/hostname');
        const data = await response.json();
        systemInfo.hostname = data.hostname || 'Unknown';
        systemInfo.ip = data.ip || 'Unknown';
    } catch (error) {
        console.error('Error loading system info:', error);
    }
}

function sendStatus() {
    const page = pages[currentIndex];
    socket.emit('kiosk_status', {
        hostname: systemInfo.hostname,
        ip: systemInfo.ip,
        current_page_id: page ? page.id : null,
        current_url: page ? page.url : null,
        paused,
        current_index: currentIndex,
        total_pages: pages.length
    });
}

function showEmpty() {
    viewer.src = 'about:blank';
    emptyState.style.display = 'flex';
    sendStatus();
}

function showPage(index) {
    if (!pages.length) {
        showEmpty();
        return;
    }

    if (index < 0) index = pages.length - 1;
    if (index >= pages.length) index = 0;
    currentIndex = index;

    const page = pages[currentIndex];
    emptyState.style.display = 'none';
    viewer.src = page.url || 'about:blank';
    sendStatus();
    scheduleNext();
}

function scheduleNext() {
    if (rotationTimer) {
        clearTimeout(rotationTimer);
        rotationTimer = null;
    }
    if (paused || !pages.length) return;
    const page = pages[currentIndex];
    const duration = (page && page.duration) ? page.duration : 30;
    rotationTimer = setTimeout(() => showPage(currentIndex + 1), duration * 1000);
}

function refreshPage() {
    if (!pages.length) return;
    const page = pages[currentIndex];
    if (!page || !page.url) return;
    // Add cache buster to force reload
    const sep = page.url.includes('?') ? '&' : '?';
    viewer.src = `${page.url}${sep}_t=${Date.now()}`;
    sendStatus();
}

socket.on('connect', async () => {
    await loadSystemInfo();
    socket.emit('kiosk_connect', {
        hostname: systemInfo.hostname,
        ip: systemInfo.ip
    });
    socket.emit('request_pages', { hostname: systemInfo.hostname });
    sendStatus();
});

socket.on('pages_list', (data) => {
    pages = Array.isArray(data) ? data : [];
    if (!pages.length) {
        showEmpty();
        return;
    }
    if (currentIndex >= pages.length) currentIndex = 0;
    showPage(currentIndex);
});

socket.on('pages_updated', () => {
    socket.emit('request_pages', { hostname: systemInfo.hostname });
});

socket.on('control', (data) => {
    const action = data && data.action;
    if (!action) return;

    if (action === 'pause') {
        paused = true;
        if (rotationTimer) {
            clearTimeout(rotationTimer);
            rotationTimer = null;
        }
        sendStatus();
    } else if (action === 'resume') {
        paused = false;
        scheduleNext();
        sendStatus();
    } else if (action === 'next') {
        showPage(currentIndex + 1);
    } else if (action === 'prev') {
        showPage(currentIndex - 1);
    } else if (action === 'refresh') {
        refreshPage();
    } else if (action === 'goto') {
        const pageId = data.page_id;
        if (pageId != null) {
            const idx = pages.findIndex(p => p.id === pageId);
            if (idx >= 0) showPage(idx);
        }
    } else if (action === 'login_mode' || action === 'exit_login_mode') {
        // Handled by kiosk.py
    } else if (action === 'admin_mode' || action === 'exit_admin_mode') {
        // Handled by kiosk.py
    }
});

// Periodic heartbeat
setInterval(() => {
    sendStatus();
}, 10000);
