// Pi Kiosk Manager - Frontend JavaScript

const socket = io();
let pages = [];
let displays = [];
let systemInfo = { hostname: '', ip: '' };
let sortable = null;
let scheduleClipboard = [];
let selectedPageIds = new Set();

// DOM Elements
const systemHostname = document.getElementById('systemHostname');
const systemIp = document.getElementById('systemIp');
const displayCount = document.getElementById('displayCount');
const displaysList = document.getElementById('displaysList');
const noDisplays = document.getElementById('noDisplays');
const pagesList = document.getElementById('pagesList');
const pageCount = document.getElementById('pageCount');
const emptyMessage = document.getElementById('emptyMessage');
const addPageForm = document.getElementById('addPageForm');
const generatePagesForm = document.getElementById('generatePagesForm');
const uploadImageForm = document.getElementById('uploadImageForm');
const editModal = document.getElementById('editModal');
const editPageForm = document.getElementById('editPageForm');
const settingsModal = document.getElementById('settingsModal');
const settingsForm = document.getElementById('settingsForm');
const tailscaleForm = document.getElementById('tailscaleForm');
const tailscaleAuthKey = document.getElementById('tailscaleAuthKey');
const tailscaleKeyStatus = document.getElementById('tailscaleKeyStatus');
const tailscaleStatus = document.getElementById('tailscaleStatus');
const githubSyncForm = document.getElementById('githubSyncForm');
const githubSyncEnabled = document.getElementById('githubSyncEnabled');
const githubSyncRepo = document.getElementById('githubSyncRepo');
const githubSyncBranch = document.getElementById('githubSyncBranch');
const githubSyncPath = document.getElementById('githubSyncPath');
const githubSyncToken = document.getElementById('githubSyncToken');
const githubSyncTokenStatus = document.getElementById('githubSyncTokenStatus');
const githubSyncStatus = document.getElementById('githubSyncStatus');
const filePreview = document.getElementById('filePreview');
const installIp = document.getElementById('installIp');
const installIp2 = document.getElementById('installIp2');

// Control buttons
const btnPrev = document.getElementById('btnPrev');
const btnNext = document.getElementById('btnNext');
const btnPause = document.getElementById('btnPause');
const btnRefresh = document.getElementById('btnRefresh');
const btnSync = document.getElementById('btnSync');
const btnForceAm = document.getElementById('btnForceAm');
const btnForcePm = document.getElementById('btnForcePm');
const btnForceAuto = document.getElementById('btnForceAuto');
const pauseIcon = document.getElementById('pauseIcon');
const pauseText = document.getElementById('pauseText');
const btnSettings = document.getElementById('btnSettings');
const btnGithubBackupNow = document.getElementById('btnGithubBackupNow');
const btnGithubClearToken = document.getElementById('btnGithubClearToken');
const copyInstallCmd = document.getElementById('copyInstallCmd');
const selectAllPages = document.getElementById('selectAllPages');
const selectedCount = document.getElementById('selectedCount');
const btnBulkEnable = document.getElementById('btnBulkEnable');
const btnBulkDisable = document.getElementById('btnBulkDisable');
const btnBulkDelete = document.getElementById('btnBulkDelete');
const btnBulkDuration = document.getElementById('btnBulkDuration');
const btnBulkAssignDisplay = document.getElementById('btnBulkAssignDisplay');
const bulkDisplaySelect = document.getElementById('bulkDisplaySelect');
const clearSelectedPages = document.getElementById('clearSelectedPages');

function setSessionMode(mode) {
    const normalized = mode === 'AM' || mode === 'PM' ? mode : 'AUTO';
    [
        [btnForceAm, 'AM'],
        [btnForcePm, 'PM'],
        [btnForceAuto, 'AUTO']
    ].forEach(([button, value]) => {
        if (!button) return;
        button.classList.toggle('active-session', normalized === value);
        button.setAttribute('aria-pressed', normalized === value ? 'true' : 'false');
    });
}

// Socket.IO Events
socket.on('connect', () => {
    console.log('Connected to server');
    loadPages();
    loadSystemInfo();
    loadDisplays();
});

socket.on('disconnect', () => {
    console.log('Disconnected from server');
});

socket.on('displays_updated', (data) => {
    console.log('Displays updated:', data);
    displays = data;
    renderDisplays();
    updateDisplaySelects();
    updateGlobalPauseButton();
});

socket.on('pages_updated', () => {
    console.log('Pages updated, reloading...');
    loadPages();
});

// Functions
async function loadSystemInfo() {
    try {
        systemInfo = await fetchJson('/api/system/hostname');
        systemHostname.textContent = systemInfo.hostname;

        // Display both local and Tailscale IP if available
        let ipDisplay = `(${systemInfo.ip})`;
        if (systemInfo.tailscale_ip) {
            ipDisplay += ` Tailscale: ${systemInfo.tailscale_ip}`;
        }
        systemIp.textContent = ipDisplay;
        setSessionMode(systemInfo.session_mode);

        // For install commands, prefer Tailscale IP if available (for remote access)
        const installIpToUse = systemInfo.tailscale_ip || systemInfo.ip;
        installIp.textContent = installIpToUse;
        installIp2.textContent = installIpToUse;
    } catch (error) {
        console.error('Error loading system info:', error);
    }
}

function absoluteUrl(url) {
    if (!url) return '';
    if (url.startsWith('http://') || url.startsWith('https://')) return url;
    const base = systemInfo?.ip ? `http://${systemInfo.ip}:5000` : window.location.origin;
    return `${base}${url.startsWith('/') ? '' : '/'}${url}`;
}

async function fetchJson(url, options) {
    const response = await fetch(url, options);
    let data = null;
    try {
        data = await response.json();
    } catch (_) {
        data = null;
    }
    if (!response.ok) {
        const message = data?.error || response.statusText || 'Request failed';
        throw new Error(message);
    }
    return data;
}

function normalizeScheduleRanges(page) {
    if (!page || !Array.isArray(page.schedule_ranges)) return [];
    return page.schedule_ranges.filter(r => r && r.start && r.end);
}

function formatScheduleRanges(page) {
    if (!page || !page.schedule_enabled) return '';
    const ranges = normalizeScheduleRanges(page)
        .slice()
        .sort((a, b) => (a.start || '').localeCompare(b.start || ''));
    if (!ranges.length) return '';
    return ranges.map(r => `${r.start}-${r.end}`).join(', ');
}

function addScheduleRow(container, start, end) {
    if (!container) return;
    const row = document.createElement('div');
    row.className = 'schedule-row';
    row.innerHTML = `
        <input type="time" class="schedule-start" value="${start}">
        <span>to</span>
        <input type="time" class="schedule-end" value="${end}">
        <button type="button" class="btn btn-small btn-secondary remove-schedule" title="Remove time">×</button>
    `;
    container.appendChild(row);
}

function setScheduleRows(containerId, ranges) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '';
    const list = ranges && ranges.length ? ranges : [{ start: '09:00', end: '17:00' }];
    list.forEach(r => addScheduleRow(container, r.start, r.end));
    updateScheduleTimeline(containerId);
}

function collectScheduleRanges(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return [];
    const rows = Array.from(container.querySelectorAll('.schedule-row'));
    return rows.map(row => {
        const start = row.querySelector('.schedule-start')?.value;
        const end = row.querySelector('.schedule-end')?.value;
        return start && end ? { start, end } : null;
    }).filter(Boolean);
}

function timeToMinutes(value) {
    if (!value || !value.includes(':')) return null;
    const [h, m] = value.split(':').map(Number);
    if (Number.isNaN(h) || Number.isNaN(m)) return null;
    return (h * 60) + m;
}

function rangesToSegments(ranges) {
    const segments = [];
    ranges.forEach(r => {
        const start = timeToMinutes(r.start);
        const end = timeToMinutes(r.end);
        if (start === null || end === null) return;
        if (start === end) {
            segments.push({ start: 0, end: 1440 });
            return;
        }
        if (start < end) {
            segments.push({ start, end });
        } else {
            segments.push({ start, end: 1440 });
            segments.push({ start: 0, end });
        }
    });
    return segments.sort((a, b) => a.start - b.start);
}

function renderScheduleTimelineMini(ranges) {
    const segments = rangesToSegments(ranges || []);
    if (!segments.length) return '';
    const bars = segments.map(seg => {
        const width = Math.max(0, seg.end - seg.start);
        if (!width) return '';
        const leftPct = (seg.start / 1440) * 100;
        const widthPct = (width / 1440) * 100;
        return `<span class="schedule-mini-segment" style="left:${leftPct}%;width:${widthPct}%"></span>`;
    }).join('');
    return `<div class="schedule-mini"><div class="schedule-mini-track">${bars}</div></div>`;
}

function updateScheduleTimeline(containerId) {
    const timeline = document.querySelector(`.schedule-timeline[data-source="${containerId}"]`);
    if (!timeline) return;
    const track = timeline.querySelector('.schedule-timeline-track');
    if (!track) return;
    track.innerHTML = '';
    const ranges = collectScheduleRanges(containerId);
    const segments = rangesToSegments(ranges);
    segments.forEach(seg => {
        const width = Math.max(0, seg.end - seg.start);
        if (!width) return;
        const bar = document.createElement('div');
        bar.className = 'schedule-segment';
        bar.style.left = `${(seg.start / 1440) * 100}%`;
        bar.style.width = `${(width / 1440) * 100}%`;
        track.appendChild(bar);
    });
}

function sortScheduleRows(containerId) {
    const ranges = collectScheduleRanges(containerId);
    if (!ranges.length) return;
    ranges.sort((a, b) => {
        const aStart = timeToMinutes(a.start) ?? 0;
        const bStart = timeToMinutes(b.start) ?? 0;
        if (aStart !== bStart) return aStart - bStart;
        const aEnd = timeToMinutes(a.end) ?? 0;
        const bEnd = timeToMinutes(b.end) ?? 0;
        return aEnd - bEnd;
    });
    setScheduleRows(containerId, ranges);
}

function parseScheduleText(text) {
    if (!text) return [];
    const trimmed = text.trim();
    if (!trimmed) return [];
    try {
        const parsed = JSON.parse(trimmed);
        if (Array.isArray(parsed)) {
            return parsed.filter(r => r && r.start && r.end);
        }
    } catch (_) {
        // fallthrough
    }
    const ranges = [];
    const regex = /(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})/g;
    let match;
    while ((match = regex.exec(trimmed)) !== null) {
        ranges.push({ start: match[1], end: match[2] });
    }
    return ranges;
}

document.addEventListener('click', (e) => {
    const addBtn = e.target.closest('.add-schedule');
    if (addBtn) {
        const targetId = addBtn.getAttribute('data-target');
        addScheduleRow(document.getElementById(targetId), '09:00', '17:00');
        updateScheduleTimeline(targetId);
        return;
    }
    const removeBtn = e.target.closest('.remove-schedule');
    if (removeBtn) {
        const row = removeBtn.closest('.schedule-row');
        const container = row?.parentElement;
        if (row && container) {
            container.removeChild(row);
            if (!container.children.length) {
                addScheduleRow(container, '09:00', '17:00');
            }
            if (container?.id) {
                updateScheduleTimeline(container.id);
            }
        }
    }
    const copyBtn = e.target.closest('.schedule-copy');
    if (copyBtn) {
        const targetId = copyBtn.getAttribute('data-target');
        const ranges = collectScheduleRanges(targetId);
        scheduleClipboard = ranges;
        const text = ranges.map(r => `${r.start}-${r.end}`).join(', ');
        if (navigator.clipboard?.writeText) {
            navigator.clipboard.writeText(text).catch(() => {});
        }
        return;
    }
    const pasteBtn = e.target.closest('.schedule-paste');
    if (pasteBtn) {
        const targetId = pasteBtn.getAttribute('data-target');
        const applyRanges = (ranges) => {
            if (!ranges || !ranges.length) return;
            setScheduleRows(targetId, ranges);
            sortScheduleRows(targetId);
            updateScheduleTimeline(targetId);
        };
        if (navigator.clipboard?.readText) {
            navigator.clipboard.readText()
                .then(text => {
                    const ranges = parseScheduleText(text);
                    applyRanges(ranges.length ? ranges : scheduleClipboard);
                })
                .catch(() => applyRanges(scheduleClipboard));
        } else {
            applyRanges(scheduleClipboard);
        }
    }
});

document.addEventListener('input', (e) => {
    if (e.target.classList.contains('schedule-start') || e.target.classList.contains('schedule-end')) {
        const container = e.target.closest('.schedule-rows');
        if (container?.id) updateScheduleTimeline(container.id);
    }
});

document.addEventListener('change', (e) => {
    if (e.target.classList.contains('schedule-start') || e.target.classList.contains('schedule-end')) {
        const container = e.target.closest('.schedule-rows');
        if (container?.id) sortScheduleRows(container.id);
    }
});

function updateDisplaySelects() {
    // Update all display select dropdowns
    const selects = [
        document.getElementById('newDisplayId'),
        document.getElementById('generateDisplayId'),
        document.getElementById('imageDisplayId'),
        document.getElementById('editDisplayId'),
        bulkDisplaySelect
    ];

    selects.forEach(select => {
        if (!select) return;
        const currentValue = select.value;
        select.innerHTML = '<option value="">All Displays</option>';
        displays.forEach(d => {
            const option = document.createElement('option');
            option.value = d.hostname;
            option.textContent = d.hostname;
            select.appendChild(option);
        });
        // Restore previous selection if still valid
        if (currentValue) {
            select.value = currentValue;
        }
    });
}

function wifiQualityLabel(rssi) {
    if (rssi == null || Number.isNaN(rssi)) return '';
    if (rssi >= -55) return 'Excellent';
    if (rssi >= -67) return 'Good';
    if (rssi >= -75) return 'Fair';
    if (rssi >= -85) return 'Weak';
    return 'Very weak';
}

function updateBulkSelectionUI() {
    const count = selectedPageIds.size;
    if (selectedCount) {
        selectedCount.textContent = `${count} selected`;
    }
    const hasSelection = count > 0;
    if (btnBulkEnable) btnBulkEnable.disabled = !hasSelection;
    if (btnBulkDisable) btnBulkDisable.disabled = !hasSelection;
    if (btnBulkDelete) btnBulkDelete.disabled = !hasSelection;
    if (btnBulkDuration) btnBulkDuration.disabled = !hasSelection;
    if (btnBulkAssignDisplay) btnBulkAssignDisplay.disabled = !hasSelection;
    if (clearSelectedPages) clearSelectedPages.disabled = !hasSelection;
    if (selectAllPages) {
        selectAllPages.checked = pages.length > 0 && count === pages.length;
        selectAllPages.indeterminate = count > 0 && count < pages.length;
    }
}

function renderDisplays() {
    displayCount.textContent = `${displays.length} display${displays.length !== 1 ? 's' : ''} connected`;

    if (displays.length === 0) {
        noDisplays.style.display = 'block';
        displaysList.style.display = 'none';
        return;
    }

    noDisplays.style.display = 'none';
    displaysList.style.display = 'grid';

    const displayOrder = displays.slice().sort((a, b) => {
        const serverHost = systemInfo?.hostname || '';
        const aIsServer = a.hostname === serverHost;
        const bIsServer = b.hostname === serverHost;
        if (aIsServer && !bIsServer) return -1;
        if (!aIsServer && bIsServer) return 1;
        return (a.hostname || '').localeCompare(b.hostname || '');
    });

    displaysList.innerHTML = displayOrder.map(display => {
        const page = pages.find(p => p.id === display.current_page_id);
        const pageName = page?.name || 'Unknown';
        const isSafe = !!display.safe_mode;
        const statusClass = isSafe ? 'safe' : (display.paused ? 'paused' : 'online');
        const screenshotVersion = display.last_screenshot || '';
        const screenshotUrl = display.screenshot_url
            ? `${absoluteUrl(display.screenshot_url)}${screenshotVersion ? `?v=${encodeURIComponent(screenshotVersion)}` : ''}`
            : '';
        const fallbackUrl = page?.type === 'image'
            ? absoluteUrl(page?.thumbnail || display.current_url)
            : absoluteUrl(display.current_url || page?.url || '');
        const previewHtml = screenshotUrl
            ? `<img src="${escapeHtml(screenshotUrl)}" alt="Screenshot">`
            : (fallbackUrl
                ? (page?.type === 'image'
                    ? `<img src="${escapeHtml(fallbackUrl)}" alt="Preview">`
                    : `<iframe src="${escapeHtml(fallbackUrl)}" loading="lazy" referrerpolicy="no-referrer" sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe>`)
                : `<div class="display-preview-empty">No preview</div>`);
        const previewLink = fallbackUrl
            ? `<a href="${escapeHtml(fallbackUrl)}" target="_blank" rel="noopener">Open</a>`
            : '';

        const temp = display.temp_c != null ? `${display.temp_c.toFixed(1)}°C` : '—';
        const memFree = display.mem_free_mb != null ? `${Math.round(display.mem_free_mb)} MB free` : '—';
        const uptime = display.uptime_sec != null ? `${Math.floor(display.uptime_sec / 3600)}h` : '—';
        const wifi = display.wifi_rssi_dbm != null
            ? `${display.wifi_rssi_dbm} dBm (${wifiQualityLabel(display.wifi_rssi_dbm)})`
            : '—';
        const currentIndex = Number.isFinite(display.current_index) ? display.current_index + 1 : '—';
        const totalPages = Number.isFinite(display.total_pages) ? display.total_pages : '?';
        const pageLabel = pageName || '—';
        const adminActive = !!display.admin_mode_active;

        return `
            <div class="display-card ${statusClass}">
                <div class="display-header">
                    <span class="display-name">${escapeHtml(display.hostname)}</span>
                    <span class="display-status ${statusClass}">${isSafe ? 'Safe' : (display.paused ? 'Paused' : 'Playing')}</span>
                </div>
                <div class="display-info">
                    <div>IP: ${escapeHtml(display.ip)}${display.connection_type ? ` (${display.connection_type})` : ''}${display.tailscale_ip ? ` TS: ${display.tailscale_ip}` : ''}</div>
                    <div>Page: ${escapeHtml(pageLabel)} (${currentIndex}/${totalPages})</div>
                    <div class="display-health">
                        <span class="health-metric">Temp ${temp}</span>
                        <span class="health-metric">Mem ${memFree}</span>
                        <span class="health-metric">Uptime ${uptime}</span>
                        <span class="health-metric">Wi‑Fi ${wifi}</span>
                    </div>
                </div>
                <div class="display-preview">
                    ${previewHtml}
                    <div class="display-preview-link">${previewLink}</div>
                </div>
                <div class="display-controls">
                    <button class="btn btn-small btn-secondary" onclick="sendControlToDisplay('${display.id}', 'prev')" title="Previous">&#9664;</button>
                    <button class="btn btn-small btn-${display.paused ? 'success' : 'primary'}" onclick="sendControlToDisplay('${display.id}', '${display.paused ? 'resume' : 'pause'}')" title="${display.paused ? 'Resume' : 'Pause'}">
                        ${display.paused ? '&#9654;' : '&#10074;&#10074;'}
                    </button>
                    <button class="btn btn-small btn-secondary" onclick="sendControlToDisplay('${display.id}', 'next')" title="Next">&#9654;</button>
                    <button class="btn btn-small btn-secondary" onclick="sendControlToDisplay('${display.id}', 'refresh')" title="Refresh">&#8635;</button>
                    <button class="btn btn-small btn-danger" onclick="rebootDisplay('${display.id}', '${escapeHtml(display.hostname)}')" title="Reboot this Pi">Reboot</button>
                    ${adminActive
                        ? `<button class="btn btn-small btn-secondary" onclick="exitAdminMode('${display.id}')" title="Exit Admin Mode">Exit</button>`
                        : `<button class="btn btn-small btn-warning" onclick="openAdminMode('${display.id}')" title="Admin Mode">Admin</button>`}
                </div>
            </div>
        `;
    }).join('');
}

async function loadPages() {
    try {
        pages = await fetchJson('/api/pages');
        const validIds = new Set(pages.map(p => p.id));
        selectedPageIds = new Set(Array.from(selectedPageIds).filter(id => validIds.has(id)));
        renderPages();
    } catch (error) {
        console.error('Error loading pages:', error);
    }
}

async function loadDisplays() {
    try {
        displays = await fetchJson('/api/displays');
        renderDisplays();
        updateDisplaySelects();
        updateGlobalPauseButton();
    } catch (error) {
        console.error('Error loading displays:', error);
    }
}

function renderPages() {
    pageCount.textContent = `(${pages.length})`;

    if (pages.length === 0) {
        emptyMessage.style.display = 'block';
        pagesList.style.display = 'none';
        updateBulkSelectionUI();
        return;
    }

    emptyMessage.style.display = 'none';
    pagesList.style.display = 'block';

    // Find current page IDs from all displays
    const currentPageIds = displays.map(d => d.current_page_id);
    const currentPages = pages.filter(p => currentPageIds.includes(p.id));
    const activeGroup = currentPages.some(p => (p.name || '').trim().startsWith('PM '))
        ? 'PM'
        : currentPages.some(p => (p.name || '').trim().startsWith('AM '))
            ? 'AM'
            : null;

    pagesList.innerHTML = pages.map(page => {
        const isCurrent = currentPageIds.includes(page.id);
        const pageName = (page.name || '').trim();
        const isCurrentGroup = activeGroup && pageName.startsWith(`${activeGroup} `);
        const isImage = page.type === 'image';
        const thumbnailHtml = isImage && page.thumbnail
            ? `<img src="${escapeHtml(page.thumbnail)}" alt="">`
            : `<span class="icon">${isImage ? '&#128444;' : '&#127760;'}</span>`;
        const displayBadge = page.display_id
            ? `<span class="page-display-badge" title="Assigned to ${escapeHtml(page.display_id)}">${escapeHtml(page.display_id)}</span>`
            : `<span class="page-display-badge all" title="Shows on all displays">All</span>`;
        const scheduleLabel = formatScheduleRanges(page);
        const scheduleBadge = page.schedule_enabled && scheduleLabel
            ? `<span class="page-schedule-badge ${page.is_active ? 'active' : 'inactive'}" title="Scheduled: ${escapeHtml(scheduleLabel)}">${escapeHtml(scheduleLabel)}</span>`
            : '';
        const nowPlayingBadge = isCurrentGroup
            ? `<span class="page-now-badge" title="${isCurrent ? 'Currently on screen' : 'Part of the active session'}">${isCurrent ? 'On screen' : `${activeGroup} active`}</span>`
            : '';
        const scheduleMini = page.schedule_enabled
            ? renderScheduleTimelineMini(normalizeScheduleRanges(page))
            : '';
        const isSelected = selectedPageIds.has(page.id);

        return `
            <li class="page-item ${isCurrentGroup ? 'current' : ''} ${isCurrent ? 'onscreen' : ''} ${!page.enabled ? 'disabled' : ''}" data-id="${page.id}">
                <div class="page-select">
                    <input type="checkbox" class="page-select-checkbox" data-id="${page.id}" ${isSelected ? 'checked' : ''}>
                </div>
                <div class="drag-handle" title="Drag to reorder">&#9776;</div>
                <div class="page-thumbnail">${thumbnailHtml}</div>
                <div class="page-info">
                    <div class="page-name">
                        ${escapeHtml(page.name || 'Unnamed')}
                        <span class="page-type-badge ${isImage ? 'image' : ''}">${isImage ? 'Image' : 'URL'}</span>
                        ${displayBadge}
                        ${nowPlayingBadge}
                        ${scheduleBadge}
                    </div>
                    <div class="page-url" title="${escapeHtml(page.url || '')}">${escapeHtml(page.url || '')}</div>
                    ${scheduleMini}
                </div>
                <div class="page-duration">${page.duration}s</div>
                <div class="page-toggle">
                    <label class="toggle-switch" title="${page.enabled ? 'Click to disable' : 'Click to enable'}">
                        <input type="checkbox" ${page.enabled ? 'checked' : ''} onchange="togglePage(${page.id}, this.checked)">
                        <span class="toggle-slider"></span>
                    </label>
                </div>
                <div class="page-actions">
                    <button class="btn btn-small btn-secondary" onclick="goToPage(${page.id})" title="Go to this page">Go</button>
                    <button class="btn btn-small btn-secondary" onclick="editPage(${page.id})" title="Edit">Edit</button>
                    <button class="btn btn-small btn-danger" onclick="deletePage(${page.id})" title="Delete">Delete</button>
                </div>
            </li>
        `;
    }).join('');

    // Initialize sortable
    if (sortable) {
        sortable.destroy();
    }

    sortable = new Sortable(pagesList, {
        animation: 150,
        handle: '.drag-handle',
        ghostClass: 'sortable-ghost',
        chosenClass: 'sortable-chosen',
        onEnd: async (evt) => {
            const order = Array.from(pagesList.children).map(el => parseInt(el.dataset.id));
            try {
                await fetch('/api/pages/reorder', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ order })
                });
            } catch (error) {
                console.error('Error reordering:', error);
                loadPages();
            }
        }
    });
    updateBulkSelectionUI();
}

function clearBulkSelection() {
    selectedPageIds.clear();
    updateBulkSelectionUI();
    renderPages();
}

async function updatePagesBulk(payload) {
    const ids = Array.from(selectedPageIds);
    if (!ids.length) return;
    try {
        await fetchJson('/api/pages/bulk', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids, updates: payload })
        });
    } catch (error) {
        console.error('Bulk update failed:', error);
        alert(`Bulk update failed: ${error.message}`);
    }
    await loadPages();
}

async function deletePagesBulk() {
    const ids = Array.from(selectedPageIds);
    if (!ids.length) return;
    if (!confirm(`Delete ${ids.length} page${ids.length !== 1 ? 's' : ''}?`)) {
        return;
    }
    try {
        await fetchJson('/api/pages/bulk', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids })
        });
    } catch (error) {
        console.error('Bulk delete failed:', error);
        alert(`Bulk delete failed: ${error.message}`);
    }
    selectedPageIds.clear();
    await loadPages();
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Control handlers
btnPrev.addEventListener('click', () => sendControl('prev'));
btnNext.addEventListener('click', () => sendControl('next'));
btnRefresh.addEventListener('click', () => sendControl('refresh'));
btnSync.addEventListener('click', async () => {
    try {
        btnSync.disabled = true;
        await loadPages();
        const target = pages.find(p => p.enabled && p.is_active) || pages.find(p => p.enabled);
        await fetch('/api/displays/sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ page_id: target ? target.id : null, delay_ms: 3000 })
        });
    } catch (error) {
        console.error('Error syncing displays:', error);
        alert('Failed to sync displays');
    } finally {
        btnSync.disabled = false;
    }
});

async function forceSession(sessionName) {
    const button = sessionName === 'AM' ? btnForceAm : btnForcePm;
    const pageName = `${sessionName} Overview`;
    try {
        button.disabled = true;
        await fetchJson('/api/displays/force-session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session: sessionName })
        });
        setSessionMode(sessionName);
        await loadPages();
        const target = pages.find(p => p.enabled && (p.name || '').trim() === pageName);
        if (!target) {
            alert(`${pageName} page not found. Generate pages first.`);
            return;
        }
        await fetch('/api/displays/sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ page_id: target.id, delay_ms: 3000, reload: true })
        });
    } catch (error) {
        console.error(`Error forcing ${sessionName}:`, error);
        alert(`Failed to force ${sessionName}`);
    } finally {
        button.disabled = false;
    }
}

btnForceAm.addEventListener('click', () => forceSession('AM'));
btnForcePm.addEventListener('click', () => forceSession('PM'));
btnForceAuto.addEventListener('click', async () => {
    try {
        btnForceAuto.disabled = true;
        await fetchJson('/api/displays/force-session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session: 'AUTO' })
        });
        setSessionMode('AUTO');
        await fetch('/api/displays/sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ delay_ms: 3000, reload: true })
        });
    } catch (error) {
        console.error('Error returning to auto schedule:', error);
        alert('Failed to return to auto schedule');
    } finally {
        btnForceAuto.disabled = false;
    }
});

// Bulk selection handlers
if (pagesList) {
    pagesList.addEventListener('change', (e) => {
        const checkbox = e.target.closest('.page-select-checkbox');
        if (!checkbox) return;
        const id = parseInt(checkbox.getAttribute('data-id'));
        if (!Number.isNaN(id)) {
            if (checkbox.checked) {
                selectedPageIds.add(id);
            } else {
                selectedPageIds.delete(id);
            }
            updateBulkSelectionUI();
        }
    });
}

if (selectAllPages) {
    selectAllPages.addEventListener('change', (e) => {
        if (e.target.checked) {
            pages.forEach(p => selectedPageIds.add(p.id));
        } else {
            selectedPageIds.clear();
        }
        renderPages();
    });
}

if (clearSelectedPages) {
    clearSelectedPages.addEventListener('click', () => {
        clearBulkSelection();
    });
}

if (btnBulkEnable) {
    btnBulkEnable.addEventListener('click', async () => {
        await updatePagesBulk({ enabled: true });
    });
}

if (btnBulkDisable) {
    btnBulkDisable.addEventListener('click', async () => {
        await updatePagesBulk({ enabled: false });
    });
}

if (btnBulkDelete) {
    btnBulkDelete.addEventListener('click', async () => {
        await deletePagesBulk();
    });
}

if (btnBulkDuration) {
    btnBulkDuration.addEventListener('click', async () => {
        const value = prompt('Set duration (seconds):');
        if (value === null) return;
        const duration = parseInt(value, 10);
        if (!duration || duration < 1) {
            alert('Please enter a valid duration in seconds.');
            return;
        }
        await updatePagesBulk({ duration });
    });
}

if (btnBulkAssignDisplay) {
    btnBulkAssignDisplay.addEventListener('click', async () => {
        const displayId = bulkDisplaySelect?.value || null;
        await updatePagesBulk({ display_id: displayId || null });
    });
}

btnPause.addEventListener('click', () => {
    // Check if any display is playing
    const anyPlaying = displays.some(d => !d.paused);
    sendControl(anyPlaying ? 'pause' : 'resume');
});

function updateGlobalPauseButton() {
    const anyPlaying = displays.some(d => !d.paused);
    if (anyPlaying) {
        pauseIcon.innerHTML = '&#10074;&#10074;';
        pauseText.textContent = 'Pause All';
        btnPause.classList.remove('btn-success');
        btnPause.classList.add('btn-primary');
    } else {
        pauseIcon.innerHTML = '&#9654;';
        pauseText.textContent = 'Resume All';
        btnPause.classList.remove('btn-primary');
        btnPause.classList.add('btn-success');
    }
}

// Login mode handlers
const btnLoginMode = document.getElementById('btnLoginMode');
const btnExitLoginMode = document.getElementById('btnExitLoginMode');
const loginModeHelp = document.getElementById('loginModeHelp');

btnLoginMode.addEventListener('click', () => {
    sendControl('login_mode');
    loginModeHelp.style.display = 'block';
    btnLoginMode.style.display = 'none';
});

btnExitLoginMode.addEventListener('click', () => {
    sendControl('exit_login_mode');
    loginModeHelp.style.display = 'none';
    btnLoginMode.style.display = 'inline-flex';
});

// Admin mode handlers
const btnAdminMode = document.getElementById('btnAdminMode');
const btnExitAdminMode = document.getElementById('btnExitAdminMode');
const adminModeHelp = document.getElementById('adminModeHelp');

btnAdminMode.addEventListener('click', () => {
    sendControl('admin_mode');
    adminModeHelp.style.display = 'block';
    btnAdminMode.style.display = 'none';
});

btnExitAdminMode.addEventListener('click', () => {
    sendControl('exit_admin_mode');
    adminModeHelp.style.display = 'none';
    btnAdminMode.style.display = 'inline-flex';
});

async function sendControl(action, data = {}) {
    try {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action, ...data })
        });
    } catch (error) {
        console.error('Error sending control:', error);
    }
}

async function sendControlToDisplay(displayId, action) {
    try {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action, display_id: displayId })
        });
    } catch (error) {
        console.error('Error sending control:', error);
    }
}

function openAdminMode(displayId) {
    if (!confirm('Enter admin mode for this display?')) {
        return;
    }
    sendControlToDisplay(displayId, 'admin_mode');
}

function exitAdminMode(displayId) {
    sendControlToDisplay(displayId, 'exit_admin_mode');
}

async function rebootDisplay(displayId, hostname) {
    if (!confirm(`Reboot ${hostname || 'this Pi'} now?`)) {
        return;
    }

    try {
        const data = await fetchJson('/api/system/reboot-display', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ display_id: displayId })
        });
        alert(data.message || 'Reboot command sent');
    } catch (error) {
        console.error('Error rebooting display:', error);
        alert('Failed to reboot display: ' + error.message);
    }
}

function goToPage(pageId) {
    sendControl('goto', { page_id: pageId });
}

// Tabs
document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;

        // Update tab buttons
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        // Update tab content
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        document.querySelector(`.tab-content[data-tab="${tab}"]`).classList.add('active');
    });
});

// Refresh checkbox toggle handlers
document.getElementById('newRefresh').addEventListener('change', (e) => {
    document.getElementById('newRefreshIntervalGroup').style.display = e.target.checked ? 'block' : 'none';
});

document.getElementById('generateRefresh').addEventListener('change', (e) => {
    const intervalGroup = document.getElementById('generateRefreshIntervalGroup');
    if (intervalGroup) {
        intervalGroup.style.display = e.target.checked ? 'block' : 'none';
    }
});

document.getElementById('imageRefresh').addEventListener('change', (e) => {
    document.getElementById('imageRefreshIntervalGroup').style.display = e.target.checked ? 'block' : 'none';
});

document.getElementById('editRefresh').addEventListener('change', (e) => {
    document.getElementById('editRefreshIntervalGroup').style.display = e.target.checked ? 'block' : 'none';
});

// Schedule checkbox toggle handlers
document.getElementById('newScheduleEnabled').addEventListener('change', (e) => {
    document.getElementById('newScheduleFields').style.display = e.target.checked ? 'flex' : 'none';
    if (e.target.checked) {
        setScheduleRows('newScheduleRows', collectScheduleRanges('newScheduleRows'));
    }
});

document.getElementById('imageScheduleEnabled').addEventListener('change', (e) => {
    document.getElementById('imageScheduleFields').style.display = e.target.checked ? 'flex' : 'none';
    if (e.target.checked) {
        setScheduleRows('imageScheduleRows', collectScheduleRanges('imageScheduleRows'));
    }
});

document.getElementById('editScheduleEnabled').addEventListener('change', (e) => {
    document.getElementById('editScheduleFields').style.display = e.target.checked ? 'flex' : 'none';
    if (e.target.checked) {
        setScheduleRows('editScheduleRows', collectScheduleRanges('editScheduleRows'));
    }
});

// Add page form
addPageForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const url = document.getElementById('newUrl').value.trim();
    const name = document.getElementById('newName').value.trim();
    const duration = parseInt(document.getElementById('newDuration').value) || 30;
    const display_id = document.getElementById('newDisplayId').value || null;
    const refresh = document.getElementById('newRefresh').checked;
    const refresh_interval = parseInt(document.getElementById('newRefreshInterval').value) || 1;
    const schedule_enabled = document.getElementById('newScheduleEnabled').checked;
    const schedule_ranges = collectScheduleRanges('newScheduleRows');
    const primaryRange = schedule_ranges[0] || {};
    const schedule_start = primaryRange.start || null;
    const schedule_end = primaryRange.end || null;

    if (!url) return;

    try {
        await fetch('/api/pages', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, name, duration, display_id, refresh, refresh_interval, schedule_enabled, schedule_start, schedule_end, schedule_ranges })
        });

        // Reset form
        document.getElementById('newUrl').value = '';
        document.getElementById('newName').value = '';
        document.getElementById('newDuration').value = '30';
        document.getElementById('newDisplayId').value = '';
        document.getElementById('newRefresh').checked = false;
        document.getElementById('newRefreshInterval').value = '1';
        document.getElementById('newRefreshIntervalGroup').style.display = 'none';
        document.getElementById('newScheduleEnabled').checked = false;
        document.getElementById('newScheduleFields').style.display = 'none';
        setScheduleRows('newScheduleRows', []);

        loadPages();
    } catch (error) {
        console.error('Error adding page:', error);
        alert('Failed to add page');
    }
});

function buildGeneratedMatPages(baseUrl, count, namePrefix) {
    const match = baseUrl.match(/\/mat\/o\/(\d+)(?=\/|\?|#|$)/);
    if (!match) {
        throw new Error('Use a Smoothcomp mat URL containing /mat/o/<number>.');
    }

    const baseId = parseInt(match[1], 10);
    if (!Number.isFinite(baseId)) {
        throw new Error('Could not read the mat id from the URL.');
    }

    const prefix = namePrefix.trim() || 'Mat';
    return Array.from({ length: count }, (_, index) => {
        const matNumber = index + 1;
        const matId = baseId + index;
        return {
            name: `${prefix} ${matNumber}`,
            url: baseUrl.replace(/\/mat\/o\/\d+(?=\/|\?|#|$)/, `/mat/o/${matId}`)
        };
    });
}

function inferMatUrl(baseUrl, offset) {
    const match = baseUrl.match(/\/mat\/o\/(\d+)(?=\/|\?|#|$)/);
    if (!match) {
        throw new Error('Use a Smoothcomp mat URL containing /mat/o/<number>.');
    }
    const baseId = parseInt(match[1], 10);
    if (!Number.isFinite(baseId)) {
        throw new Error('Could not read the mat id from the URL.');
    }
    return baseUrl.replace(/\/mat\/o\/\d+(?=\/|\?|#|$)/, `/mat/o/${baseId + offset}`);
}

function matIdFromUrl(url) {
    const match = (url || '').match(/\/mat\/o\/(\d+)(?=\/|\?|#|$)/);
    return match ? parseInt(match[1], 10) : null;
}

function matsToPages(mats, count, prefix) {
    return (mats || []).slice(0, count).map((mat, index) => ({
        name: `${prefix} ${index + 1}`,
        url: mat.url
    }));
}

function buildMatPagesFromId(eventNumber, startId, count, prefix) {
    return Array.from({ length: count }, (_, index) => ({
        name: `${prefix} ${index + 1}`,
        url: `https://oddfoxevents.smoothcomp.com/en/event/${eventNumber}/schedule/mat/o/${startId + index}?tvmode=1`
    }));
}

function buildGeneratedEventPages(config, amDiscoveredMats, pmDiscoveredMats) {
    const amRange = [{ start: config.amStart, end: config.pmStart }];
    const pmRange = [{ start: config.pmStart, end: config.pmEnd }];
    const overviewUrl = `https://oddfoxevents.smoothcomp.com/en/event/${config.eventNumber}/schedule/matches?tvmode=1&listView=full`;
    const pmOverviewUrl = config.pmCategoryId
        ? `https://oddfoxevents.smoothcomp.com/en/event/${config.eventNumber}/schedule/matches?catid=${encodeURIComponent(config.pmCategoryId)}&tvmode=1&listView=full`
        : overviewUrl;
    const normalDiscovered = Array.isArray(amDiscoveredMats) ? amDiscoveredMats : [];
    const categoryDiscovered = Array.isArray(pmDiscoveredMats) ? pmDiscoveredMats : [];
    const normalAm = normalDiscovered.filter(mat => mat.session === 'AM');
    const normalPm = normalDiscovered.filter(mat => mat.session === 'PM');
    const categoryPm = categoryDiscovered.filter(mat => mat.session === 'PM');
    const unsessioned = normalDiscovered.filter(mat => !mat.session);

    let amMats = matsToPages(normalAm, config.amCount, 'AM Mat');
    let pmMats = matsToPages(categoryPm.length ? categoryPm : normalPm, config.pmCount, 'PM Mat');

    if (!amMats.length && !pmMats.length && unsessioned.length) {
        amMats = matsToPages(unsessioned, config.amCount, 'AM Mat');
        pmMats = matsToPages(unsessioned, config.pmCount, 'PM Mat');
    }

    if (amMats.length < config.amCount && pmMats.length >= config.pmCount) {
        const firstPmId = matIdFromUrl(pmMats[0].url);
        if (Number.isFinite(firstPmId) && firstPmId > config.amCount) {
            amMats = buildMatPagesFromId(config.eventNumber, firstPmId - config.amCount, config.amCount, 'AM Mat');
        }
    }

    if (pmMats.length < config.pmCount && amMats.length >= config.amCount) {
        const firstAmId = matIdFromUrl(amMats[0].url);
        if (Number.isFinite(firstAmId)) {
            pmMats = buildMatPagesFromId(config.eventNumber, firstAmId + config.amCount, config.pmCount, 'PM Mat');
        }
    }

    if (amMats.length < config.amCount && config.amMatUrl) {
        amMats = buildGeneratedMatPages(config.amMatUrl, config.amCount, 'AM Mat');
    }

    if (pmMats.length < config.pmCount) {
        if (config.pmMatUrl) {
            pmMats = buildGeneratedMatPages(config.pmMatUrl, config.pmCount, 'PM Mat');
        } else if (config.amMatUrl) {
            pmMats = buildGeneratedMatPages(inferMatUrl(config.amMatUrl, config.amCount), config.pmCount, 'PM Mat');
        }
    }

    if (amMats.length < config.amCount || pmMats.length < config.pmCount) {
        throw new Error(`Could not confidently find ${config.amCount} AM and ${config.pmCount} PM mats for this event.`);
    }

    return [
        {
            name: 'AM Overview',
            url: overviewUrl,
            schedule_ranges: amRange
        },
        ...amMats.map(page => ({
            ...page,
            schedule_ranges: amRange
        })),
        {
            name: 'PM Overview',
            url: pmOverviewUrl,
            schedule_ranges: pmRange
        },
        ...pmMats.map(page => ({
            ...page,
            schedule_ranges: pmRange
        }))
    ];
}

function isGeneratedEventPage(page) {
    const name = (page?.name || '').trim();
    return name === 'AM Overview'
        || name === 'PM Overview'
        || /^AM Mat \d+$/.test(name)
        || /^PM Mat \d+$/.test(name);
}

async function deleteExistingGeneratedPages() {
    const ids = pages.filter(isGeneratedEventPage).map(page => page.id);
    if (!ids.length) return 0;
    await fetchJson('/api/pages/bulk', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids })
    });
    selectedPageIds.forEach(id => {
        if (ids.includes(id)) selectedPageIds.delete(id);
    });
    return ids.length;
}

function getGenerateConfig() {
    const eventNumber = document.getElementById('generateEventNumber').value.trim();
    const pmCategoryId = document.getElementById('generatePmCategoryId').value.trim();
    const amMatUrl = document.getElementById('generateAmMatUrl').value.trim();
    const pmMatUrl = document.getElementById('generatePmMatUrl').value.trim();
    const matCount = Math.min(Math.max(parseInt(document.getElementById('generateMatCount').value, 10) || 3, 1), 50);
    const amCount = matCount;
    const pmCount = matCount;
    const amStart = document.getElementById('generateAmStart').value || '08:50';
    const pmStart = document.getElementById('generatePmStart').value || '12:00';
    const pmEnd = document.getElementById('generatePmEnd').value || '17:00';

    if (!/^\d+$/.test(eventNumber)) throw new Error('Smoothcomp event number is required.');
    if (timeToMinutes(amStart) === null || timeToMinutes(pmStart) === null || timeToMinutes(pmEnd) === null) {
        throw new Error('AM start, PM start, and PM end times are required.');
    }
    if (amStart === pmStart) {
        throw new Error('AM start and PM start must be different.');
    }
    if (pmStart === pmEnd) {
        throw new Error('PM start and PM end must be different.');
    }

    if (pmCategoryId && !/^\d+$/.test(pmCategoryId)) throw new Error('PM category ID must be numeric.');

    return { eventNumber, pmCategoryId, amMatUrl, pmMatUrl, amCount, pmCount, amStart, pmStart, pmEnd };
}

function updateGeneratePreview() {
    const preview = document.getElementById('generatePreview');
    if (!preview) return;

    const eventNumber = document.getElementById('generateEventNumber').value.trim();
    if (!eventNumber) {
        preview.textContent = 'Creates AM/PM overview and mat pages from the event number.';
        return;
    }

    try {
        const config = getGenerateConfig();
        const total = 2 + config.amCount + config.pmCount;
        preview.textContent = `${total} pages: AM Overview, ${config.amCount} AM mats, PM Overview, ${config.pmCount} PM mats`;
    } catch (error) {
        preview.textContent = error.message;
    }
}

async function discoverSmoothcompMats(eventNumber, categoryId = '') {
    const url = categoryId
        ? `/api/smoothcomp/events/${encodeURIComponent(eventNumber)}/mats?catid=${encodeURIComponent(categoryId)}`
        : `/api/smoothcomp/events/${encodeURIComponent(eventNumber)}/mats`;
    const data = await fetchJson(url);
    return Array.isArray(data.mats) ? data.mats : [];
}

[
    'generateEventNumber',
    'generatePmCategoryId',
    'generateMatCount',
    'generateAmStart',
    'generatePmStart',
    'generatePmEnd'
].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', updateGeneratePreview);
});

generatePagesForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const overviewDuration = parseInt(document.getElementById('generateOverviewDuration').value, 10) || 60;
    const matDuration = parseInt(document.getElementById('generateMatDuration').value, 10) || 5;
    const display_id = document.getElementById('generateDisplayId').value || null;
    const refresh = document.getElementById('generateRefresh').checked;
    const refresh_interval = parseInt(document.getElementById('generateRefreshInterval').value, 10) || 1;

    let config;
    let generated;
    try {
        config = getGenerateConfig();
        const amDiscoveredMats = await discoverSmoothcompMats(config.eventNumber);
        const pmDiscoveredMats = await discoverSmoothcompMats(config.eventNumber, config.pmCategoryId);
        generated = buildGeneratedEventPages(config, amDiscoveredMats, pmDiscoveredMats);
    } catch (error) {
        alert(error.message);
        return;
    }

    try {
        await deleteExistingGeneratedPages();

        for (const page of generated) {
            await fetchJson('/api/pages', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        ...page,
                    duration: page.name.includes('Overview') ? overviewDuration : matDuration,
                    display_id,
                    refresh,
                    refresh_interval,
                    schedule_enabled: true,
                    schedule_start: page.schedule_ranges[0].start,
                    schedule_end: page.schedule_ranges[0].end,
                    schedule_ranges: page.schedule_ranges
                })
            });
        }

        document.getElementById('generateEventNumber').value = '';
        document.getElementById('generatePmCategoryId').value = '45664';
        document.getElementById('generateMatCount').value = '3';
        document.getElementById('generateAmStart').value = '08:50';
        document.getElementById('generatePmStart').value = '12:00';
        document.getElementById('generatePmEnd').value = '17:00';
        document.getElementById('generateOverviewDuration').value = '60';
        document.getElementById('generateMatDuration').value = '5';
        document.getElementById('generateDisplayId').value = '';
        document.getElementById('generateRefresh').checked = false;
        document.getElementById('generateRefreshInterval').value = '1';
        const generateRefreshIntervalGroup = document.getElementById('generateRefreshIntervalGroup');
        if (generateRefreshIntervalGroup) {
            generateRefreshIntervalGroup.style.display = 'none';
        }
        updateGeneratePreview();
        loadPages();
    } catch (error) {
        console.error('Error generating pages:', error);
        alert('Failed to generate pages: ' + error.message);
    }
});

// Image upload
document.getElementById('imageFile').addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
        const reader = new FileReader();
        reader.onload = (e) => {
            filePreview.innerHTML = `<img src="${e.target.result}" alt="Preview">`;
        };
        reader.readAsDataURL(file);

        // Auto-fill name if empty
        const nameInput = document.getElementById('imageName');
        if (!nameInput.value) {
            nameInput.value = file.name.replace(/\.[^/.]+$/, '');
        }
    } else {
        filePreview.innerHTML = '';
    }
});

uploadImageForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const fileInput = document.getElementById('imageFile');
    const file = fileInput.files[0];

    if (!file) {
        alert('Please select an image file');
        return;
    }

    const formData = new FormData();
    formData.append('file', file);
    formData.append('name', document.getElementById('imageName').value.trim() || file.name);
    formData.append('duration', document.getElementById('imageDuration').value || '30');
    formData.append('display_id', document.getElementById('imageDisplayId').value || '');
    formData.append('refresh', document.getElementById('imageRefresh').checked ? 'true' : 'false');
    formData.append('refresh_interval', document.getElementById('imageRefreshInterval').value || '1');
    const imageScheduleEnabled = document.getElementById('imageScheduleEnabled').checked;
    const imageScheduleRanges = collectScheduleRanges('imageScheduleRows');
    const imagePrimaryRange = imageScheduleRanges[0] || {};
    formData.append('schedule_enabled', imageScheduleEnabled ? 'true' : 'false');
    formData.append('schedule_start', imagePrimaryRange.start || '');
    formData.append('schedule_end', imagePrimaryRange.end || '');
    formData.append('schedule_ranges', JSON.stringify(imageScheduleRanges));

    try {
        const response = await fetch('/api/images', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || 'Upload failed');
        }

        // Reset form
        fileInput.value = '';
        document.getElementById('imageName').value = '';
        document.getElementById('imageDuration').value = '30';
        document.getElementById('imageDisplayId').value = '';
        document.getElementById('imageRefresh').checked = false;
        document.getElementById('imageRefreshInterval').value = '1';
        document.getElementById('imageRefreshIntervalGroup').style.display = 'none';
        document.getElementById('imageScheduleEnabled').checked = false;
        document.getElementById('imageScheduleFields').style.display = 'none';
        filePreview.innerHTML = '';

        loadPages();
    } catch (error) {
        console.error('Error uploading image:', error);
        alert('Failed to upload image: ' + error.message);
    }
});

// Edit page
function editPage(pageId) {
    const page = pages.find(p => p.id === pageId);
    if (!page) return;

    const editUrlEl = document.getElementById('editUrl');
    document.getElementById('editId').value = page.id;
    editUrlEl.value = page.url || '';
    document.getElementById('editName').value = page.name || '';
    document.getElementById('editDuration').value = page.duration;
    document.getElementById('editDisplayId').value = page.display_id || '';
    document.getElementById('editEnabled').checked = page.enabled;
    document.getElementById('editRefresh').checked = page.refresh;
    document.getElementById('editRefreshInterval').value = page.refresh_interval || 1;
    document.getElementById('editRefreshIntervalGroup').style.display = page.refresh ? 'block' : 'none';
    document.getElementById('editScheduleEnabled').checked = page.schedule_enabled;
    const editRanges = normalizeScheduleRanges(page);
    setScheduleRows('editScheduleRows', editRanges);
    document.getElementById('editScheduleFields').style.display = page.schedule_enabled ? 'flex' : 'none';

    if (page.type === 'image') {
        editUrlEl.disabled = true;
        editUrlEl.removeAttribute('required');
    } else {
        editUrlEl.disabled = false;
        editUrlEl.setAttribute('required', 'required');
    }

    // Update the display select options before showing
    updateDisplaySelects();

    // Set the display value again after updating options
    setTimeout(() => {
        document.getElementById('editDisplayId').value = page.display_id || '';
    }, 0);

    editModal.classList.add('show');
}

function closeEditModal() {
    editModal.classList.remove('show');
}

document.getElementById('closeModal').addEventListener('click', closeEditModal);
document.getElementById('cancelEdit').addEventListener('click', closeEditModal);

editModal.addEventListener('click', (e) => {
    if (e.target === editModal) {
        closeEditModal();
    }
});

editPageForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const id = document.getElementById('editId').value;
    const editUrlEl = document.getElementById('editUrl');
    const data = {
        name: document.getElementById('editName').value.trim(),
        duration: parseInt(document.getElementById('editDuration').value) || 30,
        display_id: document.getElementById('editDisplayId').value || null,
        enabled: document.getElementById('editEnabled').checked,
        refresh: document.getElementById('editRefresh').checked,
        refresh_interval: parseInt(document.getElementById('editRefreshInterval').value) || 1,
        schedule_enabled: document.getElementById('editScheduleEnabled').checked,
        schedule_ranges: collectScheduleRanges('editScheduleRows')
    };
    const editPrimaryRange = data.schedule_ranges[0] || {};
    data.schedule_start = editPrimaryRange.start || null;
    data.schedule_end = editPrimaryRange.end || null;
    if (!editUrlEl.disabled) {
        data.url = editUrlEl.value.trim();
    }

    try {
        await fetch(`/api/pages/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        closeEditModal();
        loadPages();
    } catch (error) {
        console.error('Error updating page:', error);
        alert('Failed to update page');
    }
});

// Settings modal
btnSettings.addEventListener('click', () => {
    document.getElementById('settingsHostname').value = systemInfo.hostname;
    document.getElementById('settingsIp').value = systemInfo.ip;
    loadSyncSetting();
    loadTailscaleSetting();
    loadGithubSyncSetting();
    settingsModal.classList.add('show');
});

function closeSettingsModal() {
    settingsModal.classList.remove('show');
}

async function saveGithubSyncSettings(options = {}) {
    const githubEnabled = githubSyncEnabled.checked;
    const githubRepoValue = githubSyncRepo.value.trim();
    const githubBranchValue = githubSyncBranch.value.trim() || 'main';
    const githubPathValue = githubSyncPath.value.trim() || 'backups/pi-kiosk-state.tgz';
    const githubTokenValue = githubSyncToken.value.trim();
    const payload = {
        enabled: options.enabledOverride ?? githubEnabled,
        repo: githubRepoValue,
        branch: githubBranchValue,
        path: githubPathValue
    };

    if (options.clearToken) {
        payload.clear_token = true;
    } else if (githubTokenValue) {
        payload.token = githubTokenValue;
    }

    const response = await fetch('/api/settings/github-sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });

    const result = await response.json();
    if (!response.ok) {
        throw new Error(result.error || 'Failed to save GitHub backup settings');
    }

    renderGithubSyncState(result);
    githubSyncToken.value = '';
    return result;
}

document.getElementById('closeSettings').addEventListener('click', closeSettingsModal);
document.getElementById('cancelSettings').addEventListener('click', closeSettingsModal);
document.getElementById('btnReboot').addEventListener('click', async () => {
    if (!confirm('Reboot the Pi now? This will temporarily disconnect all displays.')) {
        return;
    }

    try {
        const response = await fetch('/api/system/reboot', { method: 'POST' });
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || 'Failed to reboot');
        }
        alert(result.message || 'Reboot initiated');
    } catch (error) {
        console.error('Error rebooting:', error);
        alert('Failed to reboot: ' + error.message);
    }
});

settingsModal.addEventListener('click', (e) => {
    if (e.target === settingsModal) {
        closeSettingsModal();
    }
});

settingsForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const newHostname = document.getElementById('settingsHostname').value.trim();
    const syncEnabled = document.getElementById('settingsSyncEnabled').checked;

    if (!newHostname) {
        alert('Hostname cannot be empty');
        return;
    }

    try {
        await fetch('/api/settings/sync', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sync_enabled: syncEnabled })
        });

        if (newHostname !== systemInfo.hostname) {
            const response = await fetch('/api/system/hostname', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ hostname: newHostname })
            });

            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.error || 'Failed to change hostname');
            }

            alert(result.message || 'Hostname changed successfully');
            loadSystemInfo();
        }
        closeSettingsModal();
    } catch (error) {
        console.error('Error changing hostname:', error);
        alert('Failed to save system settings: ' + error.message);
    }
});

githubSyncForm?.addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
        setStatusMessage(githubSyncStatus, 'Saving GitHub backup settings...');
        const data = await saveGithubSyncSettings();
        setStatusMessage(githubSyncStatus, data.last_message || 'GitHub backup settings saved.');
    } catch (error) {
        console.error('Error saving GitHub backup settings:', error);
        setStatusMessage(githubSyncStatus, `Failed to save GitHub backup settings: ${error.message}`, true);
    }
});

async function loadSyncSetting() {
    try {
        const data = await fetchJson('/api/settings/sync');
        document.getElementById('settingsSyncEnabled').checked = !!data.sync_enabled;
    } catch (error) {
        console.error('Error loading sync setting:', error);
    }
}

async function loadTailscaleSetting() {
    if (!tailscaleKeyStatus) return;
    try {
        const data = await fetchJson('/api/settings/tailscale');
        const hasKey = !!data.has_key;
        tailscaleKeyStatus.textContent = hasKey ? 'Key stored on server' : 'No key stored';
    } catch (error) {
        console.error('Error loading Tailscale setting:', error);
    }
}

function setStatusMessage(el, message, isError = false) {
    if (!el) return;
    if (!message) {
        el.style.display = 'none';
        el.textContent = '';
        return;
    }
    el.style.display = 'block';
    el.textContent = message;
    el.style.color = isError ? '#fca5a5' : '#86efac';
}

function renderGithubSyncState(data) {
    if (!data) return;
    githubSyncEnabled.checked = !!data.enabled;
    githubSyncRepo.value = data.repo || '';
    githubSyncBranch.value = data.branch || 'main';
    githubSyncPath.value = data.path || 'backups/pi-kiosk-state.tgz';
    githubSyncTokenStatus.textContent = data.has_token ? 'Token stored on server' : 'No token stored';

    let message = '';
    let isError = false;
    if (data.last_error) {
        message = `Last backup failed: ${data.last_error}`;
        isError = true;
    } else if (data.last_uploaded_at) {
        message = `Last backup: ${new Date(data.last_uploaded_at).toLocaleString()}`;
        if (data.last_message) {
            message += ` | ${data.last_message}`;
        }
    } else if (data.last_message) {
        message = data.last_message;
    }
    setStatusMessage(githubSyncStatus, message, isError);
}

async function loadGithubSyncSetting() {
    if (!githubSyncRepo) return;
    try {
        const data = await fetchJson('/api/settings/github-sync');
        renderGithubSyncState(data);
    } catch (error) {
        console.error('Error loading GitHub sync setting:', error);
        setStatusMessage(githubSyncStatus, `Failed to load GitHub backup settings: ${error.message}`, true);
    }
}

btnGithubBackupNow?.addEventListener('click', async () => {
    try {
        setStatusMessage(githubSyncStatus, 'Uploading backup...');
        const data = await fetchJson('/api/settings/github-sync/backup', {
            method: 'POST'
        });
        renderGithubSyncState(data);
    } catch (error) {
        console.error('Error running GitHub backup:', error);
        setStatusMessage(githubSyncStatus, `Backup failed: ${error.message}`, true);
    }
});

btnGithubClearToken?.addEventListener('click', async () => {
    try {
        setStatusMessage(githubSyncStatus, 'Clearing GitHub token...');
        const data = await saveGithubSyncSettings({ clearToken: true, enabledOverride: false });
        setStatusMessage(githubSyncStatus, data.last_message || 'GitHub token cleared.');
    } catch (error) {
        console.error('Error clearing GitHub token:', error);
        setStatusMessage(githubSyncStatus, `Failed to clear token: ${error.message}`, true);
    }
});

// Toggle page enabled/disabled
async function togglePage(pageId, enabled) {
    try {
        await fetch(`/api/pages/${pageId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled })
        });
        loadPages();
    } catch (error) {
        console.error('Error toggling page:', error);
        alert('Failed to toggle page');
        loadPages(); // Reload to reset checkbox state
    }
}

// Delete page
async function deletePage(pageId) {
    if (!confirm('Are you sure you want to delete this page?')) {
        return;
    }

    try {
        await fetch(`/api/pages/${pageId}`, {
            method: 'DELETE'
        });
        loadPages();
    } catch (error) {
        console.error('Error deleting page:', error);
        alert('Failed to delete page');
    }
}

// Copy install command
copyInstallCmd.addEventListener('click', () => {
    const cmd = `curl -sSL http://${systemInfo.ip}:5000/install.sh | sudo bash -s -- client ${systemInfo.ip}`;
    navigator.clipboard.writeText(cmd).then(() => {
        copyInstallCmd.textContent = 'Copied!';
        setTimeout(() => {
            copyInstallCmd.textContent = 'Copy';
        }, 2000);
    }).catch(() => {
        alert('Failed to copy. Command: ' + cmd);
    });
});

// Restart all displays
document.getElementById('btnRestartAllDisplays').addEventListener('click', async () => {
    if (!confirm('Restart the kiosk service on all connected displays?')) {
        return;
    }

    try {
        const response = await fetch('/api/system/restart-displays', { method: 'POST' });
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || 'Failed to restart displays');
        }
        alert(result.message || 'Restart command sent');
    } catch (error) {
        console.error('Error restarting displays:', error);
        alert('Failed to restart displays: ' + error.message);
    }
});

// Reboot all Pis
document.getElementById('btnRebootAll').addEventListener('click', async () => {
    if (!confirm('Reboot ALL Pis including this master? This will disconnect everything temporarily.')) {
        return;
    }

    try {
        const response = await fetch('/api/system/reboot-all', { method: 'POST' });
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || 'Failed to reboot');
        }
        alert(result.message || 'Reboot command sent');
    } catch (error) {
        console.error('Error rebooting:', error);
        alert('Failed to reboot: ' + error.message);
    }
});

// WiFi form
const wifiForm = document.getElementById('wifiForm');
const wifiStatus = document.getElementById('wifiStatus');
const btnUpdateAllDisplays = document.getElementById('btnUpdateAllDisplays');

socket.on('update_result', (data) => {
    if (!wifiStatus) return;
    const name = data?.hostname || 'Unknown';
    const ok = data?.success;
    const msg = ok ? 'Updated' : (data?.error || 'Update failed');
    const color = ok ? '#22c55e' : '#ef4444';
    wifiStatus.style.display = 'block';
    wifiStatus.innerHTML += `<div style="color: ${color}; margin-top: 6px;">${escapeHtml(name)}: ${escapeHtml(msg)}</div>`;
});

socket.on('wifi_result', (data) => {
    if (!wifiStatus) return;
    const name = data?.hostname || 'Unknown';
    const ok = data?.success;
    const msg = ok ? (data?.message || 'OK') : (data?.error || 'Failed');
    const color = ok ? '#22c55e' : '#ef4444';
    wifiStatus.style.display = 'block';
    wifiStatus.innerHTML += `<div style="color: ${color}; margin-top: 6px;">${escapeHtml(name)}: ${escapeHtml(msg)}</div>`;
});

wifiForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const ssid = document.getElementById('wifiSsid').value.trim();
    const password = document.getElementById('wifiPassword').value;
    const hidden = document.getElementById('wifiHidden').checked;

    if (!ssid) {
        alert('Please enter a WiFi network name');
        return;
    }

    wifiStatus.style.display = 'block';
    wifiStatus.innerHTML = '<span style="color: #fbbf24;">Pushing WiFi credentials...</span>';

    try {
        const response = await fetch('/api/system/wifi', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ssid, password, hidden })
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || 'Failed to push WiFi credentials');
        }

        wifiStatus.innerHTML = `<span style="color: #22c55e;">${result.message}</span>`;

        // Clear form
        document.getElementById('wifiSsid').value = '';
        document.getElementById('wifiPassword').value = '';
        document.getElementById('wifiHidden').checked = false;

        // Hide status after 5 seconds
        setTimeout(() => {
            wifiStatus.style.display = 'none';
        }, 5000);
    } catch (error) {
        console.error('Error pushing WiFi:', error);
        wifiStatus.innerHTML = `<span style="color: #ef4444;">Error: ${error.message}</span>`;
    }
});

tailscaleForm?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const key = tailscaleAuthKey?.value.trim();
    if (!key) {
        alert('Please enter a Tailscale auth key');
        return;
    }
    if (!confirm('Save this key and push to all displays now?')) {
        return;
    }
    try {
        if (tailscaleStatus) {
            tailscaleStatus.style.display = 'block';
            tailscaleStatus.textContent = 'Saving and pushing...';
        }
        await fetch('/api/settings/tailscale', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ authkey: key, push: true })
        });
        if (tailscaleAuthKey) tailscaleAuthKey.value = '';
        if (tailscaleKeyStatus) tailscaleKeyStatus.textContent = 'Key stored on server';
        if (tailscaleStatus) tailscaleStatus.textContent = 'Pushed to all displays';
    } catch (error) {
        console.error('Error pushing Tailscale key:', error);
        if (tailscaleStatus) {
            tailscaleStatus.style.display = 'block';
            tailscaleStatus.textContent = 'Failed to push Tailscale key';
        }
    }
});

btnUpdateAllDisplays?.addEventListener('click', async () => {
    if (!confirm('Push the latest code to all connected displays?')) return;
    if (!wifiStatus) return;
    wifiStatus.style.display = 'block';
    wifiStatus.innerHTML = '<span style="color: #fbbf24;">Pushing update to displays...</span>';
    try {
        const response = await fetch('/api/system/update', { method: 'POST' });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Update failed');
        wifiStatus.innerHTML += `<div style="color: #22c55e; margin-top: 6px;">${escapeHtml(result.message || 'Update sent')}</div>`;
    } catch (error) {
        wifiStatus.innerHTML = `<span style="color: #ef4444;">Error: ${escapeHtml(error.message)}</span>`;
    }
});

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
    // Don't trigger shortcuts when typing in inputs
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

    switch(e.key) {
        case 'ArrowLeft':
            sendControl('prev');
            break;
        case 'ArrowRight':
            sendControl('next');
            break;
        case ' ':
            e.preventDefault();
            const anyPlaying = displays.some(d => !d.paused);
            sendControl(anyPlaying ? 'pause' : 'resume');
            break;
        case 'r':
            sendControl('refresh');
            break;
        case 'Escape':
            closeEditModal();
            closeSettingsModal();
            break;
    }
});

// Initial load
loadPages();
loadSystemInfo();
