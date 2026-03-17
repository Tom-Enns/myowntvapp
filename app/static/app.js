let selectedDeviceId = null;
let currentStreams = [];
let backendPriority = [];

// ----------------------------------------------------
// Status Log
// ----------------------------------------------------
function statusLog(msg, level = 'info') {
    const log = document.getElementById('status-log');
    log.classList.remove('hidden');
    const line = document.createElement('div');
    line.className = 'log-line';
    const now = new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    line.innerHTML = `<span class="log-time">${now}</span><span class="log-${level}">${msg}</span>`;
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
}

function clearStatusLog() {
    const log = document.getElementById('status-log');
    log.innerHTML = '';
    log.classList.add('hidden');
}

document.addEventListener('DOMContentLoaded', () => {
    discoverDevices();
    loadBackends();

    const catBtns = document.querySelectorAll('.cat-btn');
    catBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            catBtns.forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            const cat = e.target.dataset.cat;
            loadCategory(cat);
        });
    });

    document.getElementById('discover-btn').addEventListener('click', () => {
        const panel = document.getElementById('devices-panel');
        panel.classList.toggle('hidden');
    });

    // Backend settings toggle
    document.getElementById('backends-btn').addEventListener('click', () => {
        const panel = document.getElementById('backends-panel');
        panel.classList.toggle('hidden');
    });

    loadCategory('nba');
});

// ----------------------------------------------------
// Backend Management
// ----------------------------------------------------
async function loadBackends() {
    try {
        const res = await fetch('/api/backends');
        const data = await res.json();
        backendPriority = data.priority || [];
        renderBackendsPanel(data.backends);
    } catch (e) {
        console.error('Failed to load backends:', e);
    }
}

function renderBackendsPanel(backends) {
    const list = document.getElementById('backend-list');
    list.innerHTML = '';

    backends.forEach((b, idx) => {
        const div = document.createElement('div');
        div.className = 'backend-item';
        div.draggable = true;
        div.dataset.id = b.id;

        div.innerHTML = `
            <div class="backend-info">
                <span class="backend-rank">#${idx + 1}</span>
                <span class="backend-name">${b.name}</span>
            </div>
            <div class="backend-actions">
                ${idx > 0 ? `<button class="small-btn" onclick="moveBackend('${b.id}', -1)">Up</button>` : ''}
                ${idx < backends.length - 1 ? `<button class="small-btn" onclick="moveBackend('${b.id}', 1)">Down</button>` : ''}
                <span class="health-dot" id="health-${b.id}" title="Checking..."></span>
            </div>
        `;
        list.appendChild(div);

        // Check health in background
        checkBackendHealth(b.id);
    });
}

async function checkBackendHealth(backendId) {
    const dot = document.getElementById(`health-${backendId}`);
    if (!dot) return;
    try {
        const res = await fetch(`/api/backends/${backendId}/health`);
        const data = await res.json();
        dot.classList.add(data.healthy ? 'healthy' : 'unhealthy');
        dot.title = data.healthy ? 'Online' : 'Offline';
    } catch {
        dot.classList.add('unhealthy');
        dot.title = 'Error';
    }
}

async function moveBackend(backendId, direction) {
    const idx = backendPriority.indexOf(backendId);
    if (idx < 0) return;
    const newIdx = idx + direction;
    if (newIdx < 0 || newIdx >= backendPriority.length) return;

    // Swap
    [backendPriority[idx], backendPriority[newIdx]] = [backendPriority[newIdx], backendPriority[idx]];

    try {
        const res = await fetch('/api/backends/priority', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ priority: backendPriority })
        });
        const data = await res.json();
        backendPriority = data.priority;
        loadBackends();
    } catch (e) {
        console.error('Failed to update priority:', e);
    }
}

// ----------------------------------------------------
// Category Loading
// ----------------------------------------------------
async function loadCategory(category) {
    const grid = document.getElementById('streams-grid');
    const title = document.getElementById('category-title');
    const spinner = document.getElementById('loading-spinner');
    const errorDiv = document.getElementById('streams-error');

    grid.innerHTML = '';
    errorDiv.classList.add('hidden');
    spinner.classList.remove('hidden');
    clearStatusLog();

    const catNames = { nba: 'NBA Games', mlb: 'MLB Games', nhl: 'NHL Games', nfl: 'NFL Games', ncaaf: 'NCAAF Games', ncaab: 'NCAAB Games', soccer: 'Soccer', ppv: 'PPV Events', tv: 'TV Channels' };
    const catLabel = catNames[category] || category.toUpperCase();
    title.textContent = `Live ${catLabel}`;

    statusLog(`Fetching ${catLabel} from server...`);
    const startTime = Date.now();

    try {
        const res = await fetch(`/api/sports/${category}`);
        const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);

        if (!res.ok) {
            statusLog(`Server returned ${res.status} after ${elapsed}s`, 'err');
            throw new Error(`Server error ${res.status}`);
        }

        const data = await res.json();

        if (data.error) {
            statusLog(`Error: ${data.error}`, 'err');
            throw new Error(data.error);
        }

        // Show warnings from providers that failed (even if another provider succeeded)
        if (data.warnings && data.warnings.length > 0) {
            data.warnings.forEach(w => statusLog(`Warning: ${w}`, 'warn'));
        }

        if (!data.events || data.events.length === 0) {
            const hasWarnings = data.warnings && data.warnings.length > 0;
            if (hasWarnings) {
                // Providers failed — show the actual reason, not a vague message
                statusLog(`No events loaded (${elapsed}s)`, 'err');
                grid.innerHTML = `<div class="error-banner">
                    <div class="error-title">Could not load events</div>
                    <div class="error-details">${data.warnings.map(w => `<div>${escapeHtml(w)}</div>`).join('')}</div>
                    <div class="error-hint">This usually means a provider is blocking your connection. Try a VPN or check if the site is accessible from your browser.</div>
                </div>`;
            } else {
                statusLog(`No events found (${elapsed}s).`, 'warn');
                grid.innerHTML = '<div style="color:var(--text-muted); padding:20px;">No events found for this category right now.</div>';
            }
            return;
        }

        const withLogos = data.events.filter(e => e.home_logo || e.away_logo).length;
        statusLog(`Loaded ${data.events.length} events in ${elapsed}s` + (withLogos > 0 ? ` (${withLogos} with logos)` : '') + (data.provider ? ` via ${data.provider}` : ''), 'ok');

        currentStreams = data.events;
        renderGrid(data.events);
    } catch (e) {
        errorDiv.textContent = e.message;
        errorDiv.classList.remove('hidden');
    } finally {
        spinner.classList.add('hidden');
    }
}

function renderGrid(events) {
    const grid = document.getElementById('streams-grid');

    events.forEach(ev => {
        const card = document.createElement('div');
        card.className = 'stream-card';
        card.onclick = () => handleStreamClick(ev);

        let logosHtml = '';
        if (ev.home_team && ev.away_team) {
            const hLogo = ev.home_logo ? `<img src="${ev.home_logo}" class="team-logo">` : `<div style="color:#aaa;font-size:10px;text-align:center">${ev.home_team}</div>`;
            const aLogo = ev.away_logo ? `<img src="${ev.away_logo}" class="team-logo">` : `<div style="color:#aaa;font-size:10px;text-align:center">${ev.away_team}</div>`;

            logosHtml = `
                <div class="team-logos">
                    <div class="team-logo-container">${aLogo}</div>
                    <span class="vs-text">VS</span>
                    <div class="team-logo-container">${hLogo}</div>
                </div>
            `;
        }

        card.innerHTML = `
            <div class="card-bg-blur" style="background-image: url('${ev.home_logo || ev.away_logo || ''}')"></div>
            <div class="card-content">
                ${logosHtml}
                <div class="card-title">${ev.title.replace('\n', '<br>')}</div>
            </div>
        `;
        grid.appendChild(card);
    });
}

// ----------------------------------------------------
// Playback & Casting Flow
// ----------------------------------------------------
async function handleStreamClick(eventData) {
    if (selectedDeviceId) {
        await castStreamSequence(eventData);
    } else {
        await playLocalSequence(eventData);
    }
}

function showCastStatus(text, showSpinner=true) {
    const el = document.getElementById('global-cast-status');
    const txt = document.getElementById('cast-status-text');
    const spin = el.querySelector('.spinner');

    el.classList.remove('hidden');
    txt.textContent = text;
    spin.style.display = showSpinner ? 'block' : 'none';
}

function hideCastStatus() {
    document.getElementById('global-cast-status').classList.add('hidden');
}

function stopCasting() {
    selectedDeviceId = null;
    document.querySelectorAll('.device-item').forEach(el => el.classList.remove('active'));
    document.getElementById('stop-cast-btn').classList.add('hidden');
    showCastStatus('Switched to local playback', false);
    setTimeout(hideCastStatus, 2000);
}

/**
 * Resolve a stream using the backend-agnostic /api/resolve endpoint.
 */
async function resolveStream(eventData) {
    const body = {
        event_id: eventData.event_id,
        title: eventData.title,
        category: eventData.category,
        home_team: eventData.home_team || null,
        away_team: eventData.away_team || null,
        home_logo: eventData.home_logo || null,
        away_logo: eventData.away_logo || null,
    };

    const res = await fetch('/api/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
    return await res.json();
}

/**
 * Resolve ALL streams from ALL backends in parallel.
 * Returns multiple streams sorted by quality for the stream picker.
 */
async function resolveAllStreams(eventData) {
    const body = {
        event_id: eventData.event_id,
        title: eventData.title,
        category: eventData.category,
        home_team: eventData.home_team || null,
        away_team: eventData.away_team || null,
        home_logo: eventData.home_logo || null,
        away_logo: eventData.away_logo || null,
    };

    const res = await fetch('/api/resolve-all', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
    return await res.json();
}

async function castStreamSequence(eventData) {
    clearStatusLog();
    showCastStatus(`Extracting stream for Apple TV...`);
    statusLog(`Opening ${eventData.title.split('\n')[0]}...`);
    statusLog('Resolving stream from backends...');
    try {
        const t0 = Date.now();
        const ext = await resolveStream(eventData);
        const extractTime = ((Date.now() - t0) / 1000).toFixed(1);

        if (ext.error) {
            logBackendErrors(ext, extractTime);
            throw new Error(ext.error);
        }

        let qualityInfo = '';
        if (ext.qualities && ext.qualities.length > 0) {
            const best = ext.qualities[0];
            qualityInfo = best.resolution ? ` [${best.resolution}]` : '';
        }
        statusLog(`Stream found via ${ext.backend_name || 'unknown'} in ${extractTime}s${qualityInfo}`, 'ok');

        showCastStatus(`Casting to Apple TV...`);
        statusLog('Preparing remuxed stream (ffmpeg)...');
        statusLog('Sending stream URL to Apple TV via AirPlay...');

        const t1 = Date.now();
        const castRes = await fetch('/api/cast', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device_id: selectedDeviceId, session_id: ext.session_id })
        });
        const castData = await castRes.json();
        const castTime = ((Date.now() - t1) / 1000).toFixed(1);

        if (castData.error) {
            statusLog(`Cast failed after ${castTime}s: ${castData.error}`, 'err');
            throw new Error(castData.error);
        }

        statusLog(`Cast command accepted in ${castTime}s — stream should appear on TV`, 'ok');
        showCastStatus(`Playing on Apple TV!`, false);
        setTimeout(hideCastStatus, 3000);

    } catch (e) {
        showCastStatus(`Failed: ${e.message}`, false);
        // Don't auto-hide — user needs to read the error
    }
}

async function playLocalSequence(eventData) {
    clearStatusLog();
    showCastStatus(`Resolving streams...`);
    statusLog(`Opening ${eventData.title.split('\n')[0]}...`);
    statusLog('Resolving streams from all backends...');
    try {
        const t0 = Date.now();
        const result = await resolveAllStreams(eventData);
        const elapsed = ((Date.now() - t0) / 1000).toFixed(1);

        if (result.error) {
            logBackendErrors(result, elapsed);
            throw new Error(result.error);
        }

        const streams = result.streams || [];
        if (streams.length === 0) {
            throw new Error('No streams found from any backend');
        }

        // Log backend statuses
        if (result.backend_statuses) {
            result.backend_statuses.forEach(s => {
                if (s.success) {
                    statusLog(`${s.backend}: found stream (${s.latency_ms}ms)`, 'ok');
                } else {
                    statusLog(`${s.backend}: ${s.error} (${s.latency_ms}ms)`, 'warn');
                }
            });
        }

        // Auto-play the best stream (first in list — sorted by quality)
        const best = streams[0];
        let qualityInfo = '';
        if (best.qualities && best.qualities.length > 0) {
            const q = best.qualities[0];
            qualityInfo = q.resolution ? ` [${q.resolution}]` : '';
        }
        statusLog(`Playing best of ${streams.length} stream(s) via ${best.backend_name}${qualityInfo}`, 'ok');
        hideCastStatus();
        openLocalPlayer(best.proxy_url, eventData.title, best.qualities, best.backend_name, streams);
    } catch (e) {
        showErrorOverlay(e.message);
    }
}

// ----------------------------------------------------
// Error Display Helpers
// ----------------------------------------------------
function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function logBackendErrors(response, elapsed) {
    statusLog(`Stream resolution failed after ${elapsed}s`, 'err');
    if (response.backend_errors) {
        response.backend_errors.forEach(be => {
            statusLog(`  ${be.backend}: ${be.error} (${be.latency_ms}ms)`, 'err');
        });
    }
}

function showErrorOverlay(message) {
    hideCastStatus();
    // Show a persistent error in the player area instead of a disappearing toast
    const container = document.getElementById('local-player-container');
    container.classList.remove('hidden');
    document.getElementById('player-title').textContent = 'Stream Error';
    document.getElementById('player-badges').innerHTML = '';

    const videoWrapper = container.querySelector('.video-wrapper');
    const video = document.getElementById('video-player');
    video.style.display = 'none';

    // Create or update error display inside video wrapper
    let errorEl = document.getElementById('player-error-display');
    if (!errorEl) {
        errorEl = document.createElement('div');
        errorEl.id = 'player-error-display';
        errorEl.className = 'player-error-display';
        videoWrapper.appendChild(errorEl);
    }
    errorEl.innerHTML = `
        <div class="error-icon">!</div>
        <div class="error-title">Stream Unavailable</div>
        <div class="error-message">${escapeHtml(message)}</div>
        <div class="error-hint">Check the status log below for details from each backend.</div>
    `;
    errorEl.style.display = 'flex';
}

// Override closeLocalPlayer to clean up error state
const _origCloseLocalPlayer = typeof closeLocalPlayer === 'function' ? closeLocalPlayer : null;

// ----------------------------------------------------
// Local Player
// ----------------------------------------------------
let hlsInstance = null;
let currentProxyUrl = null;

let allAvailableStreams = [];

function openLocalPlayer(m3u8Url, title, qualities, backendName, streams) {
    document.getElementById('local-player-container').classList.remove('hidden');
    document.getElementById('player-title').textContent = title.split('\n')[0];
    currentProxyUrl = m3u8Url;
    allAvailableStreams = streams || [];

    // Ensure video is visible (may have been hidden by error overlay)
    const video = document.getElementById('video-player');
    video.style.display = '';
    const errorEl = document.getElementById('player-error-display');
    if (errorEl) errorEl.style.display = 'none';

    // Show quality and backend badges
    const badgeContainer = document.getElementById('player-badges');
    badgeContainer.innerHTML = '';
    if (backendName) {
        badgeContainer.innerHTML += `<span class="badge-pill backend-badge">${backendName}</span>`;
    }
    if (qualities && qualities.length > 0) {
        const best = qualities[0];
        if (best.resolution) {
            badgeContainer.innerHTML += `<span class="badge-pill quality-badge">${best.resolution}</span>`;
        }
        if (best.bandwidth) {
            const mbps = (best.bandwidth / 1_000_000).toFixed(1);
            badgeContainer.innerHTML += `<span class="badge-pill bitrate-badge">${mbps} Mbps</span>`;
        }
    }

    // Render stream picker if multiple streams available
    renderStreamPicker(allAvailableStreams, m3u8Url);

    startPlayback(video, m3u8Url);
}

function startPlayback(video, m3u8Url) {
    const useNative = video.canPlayType('application/vnd.apple.mpegurl');

    if (useNative) {
        if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }
        video.src = m3u8Url;
        video.play();
    } else if (Hls.isSupported()) {
        if (hlsInstance) hlsInstance.destroy();
        hlsInstance = new Hls();
        hlsInstance.loadSource(m3u8Url);
        hlsInstance.attachMedia(video);
        hlsInstance.on(Hls.Events.MANIFEST_PARSED, () => video.play());
    }
}

function renderStreamPicker(streams, activeUrl) {
    const picker = document.getElementById('stream-picker');
    if (!picker) return;

    if (!streams || streams.length <= 1) {
        picker.classList.add('hidden');
        return;
    }

    picker.classList.remove('hidden');
    picker.innerHTML = '<div class="picker-label">Available Streams</div>';

    streams.forEach((s, idx) => {
        const btn = document.createElement('button');
        btn.className = 'stream-option' + (s.proxy_url === activeUrl ? ' active' : '');

        let label = s.source_label || `Stream ${idx + 1}`;
        let quality = '';
        if (s.qualities && s.qualities.length > 0) {
            const best = s.qualities[0];
            quality = best.resolution || '';
        }

        btn.innerHTML = `
            <span class="stream-option-label">${label}</span>
            <span class="stream-option-meta">${s.backend_name}${quality ? ' · ' + quality : ''}</span>
        `;

        btn.onclick = () => switchStream(s, streams);
        picker.appendChild(btn);
    });
}

function switchStream(stream, allStreams) {
    const video = document.getElementById('video-player');
    currentProxyUrl = stream.proxy_url;

    // Update badges
    const badgeContainer = document.getElementById('player-badges');
    badgeContainer.innerHTML = '';
    if (stream.backend_name) {
        badgeContainer.innerHTML += `<span class="badge-pill backend-badge">${stream.backend_name}</span>`;
    }
    if (stream.qualities && stream.qualities.length > 0) {
        const best = stream.qualities[0];
        if (best.resolution) {
            badgeContainer.innerHTML += `<span class="badge-pill quality-badge">${best.resolution}</span>`;
        }
        if (best.bandwidth) {
            const mbps = (best.bandwidth / 1_000_000).toFixed(1);
            badgeContainer.innerHTML += `<span class="badge-pill bitrate-badge">${mbps} Mbps</span>`;
        }
    }

    renderStreamPicker(allStreams, stream.proxy_url);
    startPlayback(video, stream.proxy_url);
    statusLog(`Switched to stream from ${stream.backend_name}`, 'ok');
}

function closeLocalPlayer() {
    document.getElementById('local-player-container').classList.add('hidden');
    const video = document.getElementById('video-player');
    video.pause();
    video.removeAttribute('src');
    video.style.display = '';
    if (hlsInstance) {
        hlsInstance.destroy();
        hlsInstance = null;
    }
    // Clean up error overlay if present
    const errorEl = document.getElementById('player-error-display');
    if (errorEl) errorEl.style.display = 'none';
}

function copyStreamUrl() {
    if (currentProxyUrl) {
        navigator.clipboard.writeText(currentProxyUrl);
        alert('Stream URL copied to clipboard');
    }
}

// ----------------------------------------------------
// Device Discovery & Pairing
// ----------------------------------------------------
async function discoverDevices() {
    const list = document.getElementById('device-list');
    const status = document.getElementById('device-status');
    const badge = document.getElementById('device-count');

    status.classList.remove('hidden', 'error');
    status.className = 'status info';
    status.innerHTML = '<div class="spinner"></div> Searching networks...';
    statusLog('Scanning network for AirPlay devices...');

    try {
        const res = await fetch('/api/devices');
        const data = await res.json();

        list.innerHTML = '';
        if (data.devices.length === 0) {
            status.textContent = 'No Apple TVs found. Ensure they are awake and on the same network.';
            statusLog('No AirPlay devices found on network', 'warn');
        } else {
            status.classList.add('hidden');
            badge.textContent = data.devices.length;
            statusLog(`Found ${data.devices.length} AirPlay device(s): ${data.devices.map(d => d.name).join(', ')}`, 'ok');

            createDeviceItem(list, { name: 'Watch Here Locally', identifier: null, address: 'Computer Browser' });

            data.devices.forEach(device => {
                createDeviceItem(list, device);
            });
        }
    } catch (e) {
        status.className = 'status error';
        status.textContent = 'Error: ' + e.message;
        statusLog(`Device scan failed: ${e.message}`, 'err');
    }
}

function createDeviceItem(container, device) {
    const div = document.createElement('div');
    div.className = 'device-item';
    if (device.identifier === selectedDeviceId) div.classList.add('active');

    div.innerHTML = `
        <div>
            <div class="device-name">${device.name}</div>
            <div class="device-address">${device.address}</div>
        </div>
        ${device.identifier ? `<button class="secondary-btn" onclick="startPairing('${device.identifier}', event)">Pair</button>` : ''}
    `;

    div.onclick = (e) => {
        if (e.target.tagName === 'BUTTON') return;
        selectedDeviceId = device.identifier;
        document.querySelectorAll('.device-item').forEach(el => el.classList.remove('active'));
        div.classList.add('active');
        document.getElementById('devices-panel').classList.add('hidden');

        const stopBtn = document.getElementById('stop-cast-btn');
        if (device.identifier) {
            stopBtn.classList.remove('hidden');
        } else {
            stopBtn.classList.add('hidden');
        }

        showCastStatus(device.identifier ? `Target locked: ${device.name}` : `Target locked: Local Browser`, false);
        setTimeout(hideCastStatus, 2000);
    };

    container.appendChild(div);
}

// ----------------------------------------------------
// Pairing Modal
// ----------------------------------------------------
let pairingDeviceId = null;

async function startPairing(deviceId, e) {
    if (e) e.stopPropagation();
    try {
        const res = await fetch('/api/pair/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device_id: deviceId })
        });
        const data = await res.json();

        if (data.error) {
            alert('Pairing error: ' + data.error);
            return;
        }

        pairingDeviceId = deviceId;
        document.getElementById('pair-dialog').classList.remove('hidden');
        document.getElementById('pair-pin').value = '';
        document.getElementById('pair-status').classList.add('hidden');
        document.getElementById('devices-panel').classList.add('hidden');
    } catch (e) {
        alert(e.message);
    }
}

async function finishPairing() {
    const pin = document.getElementById('pair-pin').value;
    const status = document.getElementById('pair-status');
    status.classList.remove('hidden');
    status.className = 'status info';
    status.textContent = 'Verifying PIN...';

    try {
        const res = await fetch('/api/pair/finish', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device_id: pairingDeviceId, pin: parseInt(pin) })
        });
        const data = await res.json();

        if (data.error) {
            status.className = 'status error';
            status.textContent = data.error;
        } else if (data.status === 'more_pairing') {
            status.className = 'status info';
            status.textContent = data.message;
            document.getElementById('pair-pin').value = '';
        } else {
            status.className = 'status success';
            status.textContent = 'Pairing successful!';
            setTimeout(closePairDialog, 1500);
        }
    } catch (e) {
        status.className = 'status error';
        status.textContent = e.message;
    }
}

function closePairDialog() {
    document.getElementById('pair-dialog').classList.add('hidden');
    pairingDeviceId = null;
}
