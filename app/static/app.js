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

        if (!data.events || data.events.length === 0) {
            statusLog(`No events found (${elapsed}s). The source may be down — try refreshing.`, 'warn');
            grid.innerHTML = '<div style="color:var(--text-muted); padding:20px;">No events found. Try refreshing the page.</div>';
            return;
        }

        const withLogos = data.events.filter(e => e.home_logo || e.away_logo).length;
        statusLog(`Loaded ${data.events.length} events in ${elapsed}s` + (withLogos > 0 ? ` (${withLogos} with logos)` : ''), 'ok');

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
 * Falls back to legacy /api/extract if the event has a direct URL (old format).
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
            statusLog(`Resolution failed after ${extractTime}s: ${ext.error}`, 'err');
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
        setTimeout(hideCastStatus, 4000);
    }
}

async function playLocalSequence(eventData) {
    clearStatusLog();
    showCastStatus(`Resolving stream...`);
    statusLog(`Opening ${eventData.title.split('\n')[0]}...`);
    statusLog('Resolving stream from backends...');
    try {
        const t0 = Date.now();
        const ext = await resolveStream(eventData);
        const elapsed = ((Date.now() - t0) / 1000).toFixed(1);

        if (ext.error) {
            statusLog(`Resolution failed after ${elapsed}s: ${ext.error}`, 'err');
            throw new Error(ext.error);
        }

        let qualityInfo = '';
        if (ext.qualities && ext.qualities.length > 0) {
            const best = ext.qualities[0];
            qualityInfo = best.resolution ? ` [${best.resolution}]` : '';
        }
        statusLog(`Stream found via ${ext.backend_name || 'unknown'} in ${elapsed}s${qualityInfo} — starting playback`, 'ok');
        hideCastStatus();
        openLocalPlayer(ext.proxy_url, eventData.title, ext.qualities, ext.backend_name);
    } catch (e) {
        showCastStatus(`Failed: ${e.message}`, false);
        setTimeout(hideCastStatus, 4000);
    }
}

// ----------------------------------------------------
// Local Player
// ----------------------------------------------------
let hlsInstance = null;
let currentProxyUrl = null;

function openLocalPlayer(m3u8Url, title, qualities, backendName) {
    document.getElementById('local-player-container').classList.remove('hidden');
    document.getElementById('player-title').textContent = title.split('\n')[0];
    currentProxyUrl = m3u8Url;

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

    const video = document.getElementById('video-player');
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

function closeLocalPlayer() {
    document.getElementById('local-player-container').classList.add('hidden');
    const video = document.getElementById('video-player');
    video.pause();
    video.removeAttribute('src');
    if (hlsInstance) {
        hlsInstance.destroy();
        hlsInstance = null;
    }
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
