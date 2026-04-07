const socket = io();
const cameraData = {};
const fpsCounters = {};
const resizeObservers = {};
const resizeSaveTimers = {};
let focusedCameraId = null;
let autoFocusEnabled = true;
let lastAutoFocusSwitchAt = 0;
const videoSizeStorageKey = 'cameraVideoSizes';
const defaultVideoAspectRatio = 16 / 9;
const resizeSaveDebounceMs = 120;
const autoFocusSettings = {
    scoreThreshold: 0.10,
    switchCooldownMs: 3000
};

const defaultParams = {
    pan_speed: 40,
    tilt_speed: 30,
    deadzone: 0.1,
    confidence_threshold: 0.80,
    tracking_enabled: false,
    draw_skeleton: true,
    draw_stats: true
};

const servoDefaults = {
    pan: 90,
    tilt: 90
};

const servoPulseMin = 900;
const servoPulseMax = 2100;
const servoAngleMin = 0;
const servoAngleMax = 180;

function degreesToPulse(degrees) {
    const clamped = Math.max(servoAngleMin, Math.min(servoAngleMax, degrees));
    return Math.round(servoPulseMin + ((clamped - servoAngleMin) / (servoAngleMax - servoAngleMin)) * (servoPulseMax - servoPulseMin));
}

function pulseToDegrees(pulse) {
    const clamped = Math.max(servoPulseMin, Math.min(servoPulseMax, pulse));
    return Math.round(((clamped - servoPulseMin) / (servoPulseMax - servoPulseMin)) * (servoAngleMax - servoAngleMin) + servoAngleMin);
}

socket.on('connect', () => {
    console.log('Connected to server');
    socket.emit('get_cameras');
});

socket.on('camera_list', (cameras) => {
    cameras.forEach(camera => {
        const cameraId = camera.camera_id;
        if (!cameraData[cameraId]) {
            const savedVideoSize = getSavedVideoSize(cameraId);
            cameraData[cameraId] = {
                connected: camera.connected,
                params: { ...defaultParams, ...(camera.params || {}) },
                videoSize: savedVideoSize,
                aspectRatio: getSavedVideoAspectRatio(cameraId),
                recording: camera.recording || false,
                servo: { ...servoDefaults },
                viewScore: Number.isFinite(camera.view_score) ? camera.view_score : 0
            };
            setupCameraListeners(cameraId);
        } else {
            cameraData[cameraId].connected = camera.connected;
            cameraData[cameraId].params = { ...defaultParams, ...(camera.params || cameraData[cameraId].params || {}) };
            cameraData[cameraId].recording = camera.recording || false;
            cameraData[cameraId].viewScore = Number.isFinite(camera.view_score) ? camera.view_score : (cameraData[cameraId].viewScore || 0);
            if (!cameraData[cameraId].servo) {
                cameraData[cameraId].servo = { ...servoDefaults };
            }
            if (!cameraData[cameraId].videoSize) {
                cameraData[cameraId].videoSize = getSavedVideoSize(cameraId);
            }
            if (!cameraData[cameraId].aspectRatio) {
                cameraData[cameraId].aspectRatio = getSavedVideoAspectRatio(cameraId);
            }
        }
    });
    renderCameras();
});

socket.on('camera_disconnected', (data) => {
    const cameraId = data.camera_id;
    if (cameraData[cameraId]) {
        cameraData[cameraId].connected = false;
        const card = document.getElementById('card_' + cameraId);
        if (card) card.classList.add('disconnected');
        const status = document.getElementById('status_' + cameraId);
        if (status) {
            status.textContent = 'Offline';
            status.className = 'camera-status offline';
        }
    }
    renderCameras();
});

function setupCameraListeners(cameraId) {
    socket.on('frame_' + cameraId, (data) => {
        const img = document.getElementById('video_' + cameraId);
        if (img) {
            img.src = 'data:image/jpeg;base64,' + data.image;
        }

        if (data.params) {
            cameraData[cameraId].params = data.params;
        }

        if (Number.isFinite(data.view_score)) {
            cameraData[cameraId].viewScore = data.view_score;
        }

        if (!fpsCounters[cameraId]) {
            fpsCounters[cameraId] = { count: 0, lastUpdate: Date.now() };
        }
        fpsCounters[cameraId].count++;
        const now = Date.now();
        if (now - fpsCounters[cameraId].lastUpdate >= 1000) {
            const fpsEl = document.getElementById('fps_' + cameraId);
            if (fpsEl) {
                fpsEl.textContent = fpsCounters[cameraId].count;
            }
            fpsCounters[cameraId].count = 0;
            fpsCounters[cameraId].lastUpdate = now;
        }

        cameraData[cameraId].connected = true;
        const card = document.getElementById('card_' + cameraId);
        if (card) card.classList.remove('disconnected');
        const status = document.getElementById('status_' + cameraId);
        if (status) {
            status.textContent = 'Online';
            status.className = 'camera-status online';
        }

        if (autoFocusEnabled) {
            evaluateAutoFocus();
        }
    });
}

socket.on('params_updated', (data) => {
    const { camera_id, params } = data;
    if (cameraData[camera_id]) {
        cameraData[camera_id].params = params;
        updateCameraUI(camera_id, params);
    }
});

socket.on('recording_started', (data) => {
    const cameraId = data.camera_id;
    if (cameraData[cameraId]) {
        cameraData[cameraId].recording = true;
        renderCameras();
    }
});

socket.on('recording_stopped', (data) => {
    const cameraId = data.camera_id;
    if (cameraData[cameraId]) {
        cameraData[cameraId].recording = false;
        renderCameras();
    }
    if (data.download_url) {
        const link = document.createElement('a');
        link.href = data.download_url;
        link.download = data.filename || '';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }
});

socket.on('recording_error', (data) => {
    const cameraId = data.camera_id;
    if (cameraData[cameraId]) {
        cameraData[cameraId].recording = false;
        renderCameras();
    }
    console.error('Recording error', data.error || 'Unknown error');
});

socket.on('servo_moved', (data) => {
    const cameraId = data.camera_id;
    if (!cameraData[cameraId]) {
        return;
    }

    const panDegrees = pulseToDegrees(data.pan);
    const tiltDegrees = pulseToDegrees(data.tilt);

    cameraData[cameraId].servo.pan = panDegrees;
    cameraData[cameraId].servo.tilt = tiltDegrees;
    updateServoPreview(cameraId, 'pan', panDegrees);
    updateServoPreview(cameraId, 'tilt', tiltDegrees);
});

socket.on('servo_move_rejected', (data) => {
    if (data?.reason) {
        console.warn(`Servo move rejected for ${data.camera_id}: ${data.reason}`);
    }
});

function renderCameras() {
    const grid = document.getElementById('cameraGrid');
    const cameraIds = Object.keys(cameraData);
    updateAutoFocusButton();
    syncAutoFocusControlUI();

    if (cameraIds.length === 0) {
        grid.innerHTML = '<div class="no-cameras">Waiting for cameras to connect...</div>';
        return;
    }

    if (focusedCameraId && !cameraData[focusedCameraId]) {
        focusedCameraId = null;
    }

    grid.classList.toggle('focus-mode', Boolean(focusedCameraId));

    grid.innerHTML = cameraIds.map(cameraId => `
        <div id="card_${cameraId}" class="camera-card ${cameraData[cameraId].connected ? '' : 'disconnected'} ${focusedCameraId && focusedCameraId !== cameraId ? 'hidden' : ''} ${focusedCameraId === cameraId ? 'focused' : ''}">
            <div class="camera-header">
                <span class="camera-title">${cameraId}</span>
                <span class="camera-score">Score: ${formatViewScore(cameraData[cameraId].viewScore)}</span>
                <span id="status_${cameraId}" class="camera-status ${cameraData[cameraId].connected ? 'online' : 'offline'}">
                    ${cameraData[cameraId].connected ? 'Online' : 'Offline'}
                </span>
            </div>

            <div class="video-container">
                <div id="videoWrap_${cameraId}" class="video-resize" style="${getVideoSizeStyle(cameraId)}">
                    <img id="video_${cameraId}" alt="${cameraId} feed" onload="handleVideoMetadataLoad('${cameraId}', this)">
                </div>
                <div class="fps-counter">FPS: <span id="fps_${cameraId}">0</span></div>
            </div>

            <div class="controls">
                <div class="control-group">
                    <div class="toggle-container">
                        <label class="toggle">
                            <input type="checkbox" id="tracking_${cameraId}" 
                                   onchange="updateParam('${cameraId}', 'tracking_enabled', this.checked)"
                                   ${cameraData[cameraId].params.tracking_enabled ? 'checked' : ''}>
                            <span class="toggle-slider"></span>
                        </label>
                        <span>Tracking Enabled</span>
                    </div>
                </div>

                <div class="control-group">
                    <div class="toggle-container">
                        <label class="toggle">
                            <input type="checkbox" id="drawSkeleton_${cameraId}" 
                                   onchange="updateParam('${cameraId}', 'draw_skeleton', this.checked)"
                                   ${cameraData[cameraId].params.draw_skeleton ? 'checked' : ''}>
                            <span class="toggle-slider"></span>
                        </label>
                        <span>Draw Skeleton</span>
                    </div>
                    <div class="toggle-container">
                        <label class="toggle">
                            <input type="checkbox" id="drawStats_${cameraId}" 
                                   onchange="updateParam('${cameraId}', 'draw_stats', this.checked)"
                                   ${cameraData[cameraId].params.draw_stats ? 'checked' : ''}>
                            <span class="toggle-slider"></span>
                        </label>
                        <span>Draw Stats</span>
                    </div>
                </div>

                <div class="control-group">
                    <label>Manual Pan Degrees: <span id="manualPanValue_${cameraId}">${cameraData[cameraId].servo.pan}°</span></label>
                    <input type="range" id="manualPan_${cameraId}" min="0" max="180" step="1"
                           value="${cameraData[cameraId].servo.pan}"
                           ${cameraData[cameraId].params.tracking_enabled ? 'disabled' : ''}
                           oninput="updateServoPreview('${cameraId}', 'pan', parseInt(this.value, 10))">
                </div>

                <div class="control-group">
                    <label>Manual Tilt Degrees: <span id="manualTiltValue_${cameraId}">${cameraData[cameraId].servo.tilt}°</span></label>
                    <input type="range" id="manualTilt_${cameraId}" min="0" max="180" step="1"
                           value="${cameraData[cameraId].servo.tilt}"
                           ${cameraData[cameraId].params.tracking_enabled ? 'disabled' : ''}
                           oninput="updateServoPreview('${cameraId}', 'tilt', parseInt(this.value, 10))">
                </div>

                <div class="control-group">
                    <div class="button-row">
                        <button class="btn-primary" onclick="moveServo('${cameraId}')" ${cameraData[cameraId].params.tracking_enabled ? 'disabled' : ''}>Move Servo</button>
                        <button id="centerServo_${cameraId}" class="btn-secondary" onclick="centerServo('${cameraId}')" ${cameraData[cameraId].params.tracking_enabled ? 'disabled' : ''}>Center</button>
                    </div>
                    <div id="manualControlHint_${cameraId}" class="helper-text" style="display: ${cameraData[cameraId].params.tracking_enabled ? 'block' : 'none'};">Disable tracking to use manual servo control.</div>
                </div>

                <div class="speed-controls">
                    <div class="control-group">
                        <label>Pan Speed: <span id="panSpeedValue_${cameraId}">${cameraData[cameraId].params.pan_speed}</span></label>
                        <input type="range" id="panSpeed_${cameraId}" min="5" max="100" 
                               value="${cameraData[cameraId].params.pan_speed}"
                               oninput="updateParam('${cameraId}', 'pan_speed', parseFloat(this.value))">
                    </div>

                    <div class="control-group">
                        <label>Tilt Speed: <span id="tiltSpeedValue_${cameraId}">${cameraData[cameraId].params.tilt_speed}</span></label>
                        <input type="range" id="tiltSpeed_${cameraId}" min="5" max="100" 
                               value="${cameraData[cameraId].params.tilt_speed}"
                               oninput="updateParam('${cameraId}', 'tilt_speed', parseFloat(this.value))">
                    </div>
                </div>

                <div class="control-group">
                    <label>Deadzone: <span id="deadzoneValue_${cameraId}">${cameraData[cameraId].params.deadzone.toFixed(2)}</span></label>
                    <input type="range" id="deadzone_${cameraId}" min="0" max="0.5" step="0.01"
                           value="${cameraData[cameraId].params.deadzone}"
                           oninput="updateParam('${cameraId}', 'deadzone', parseFloat(this.value))">
                </div>

                <div class="control-group">
                    <label>Confidence: <span id="confidenceValue_${cameraId}">${cameraData[cameraId].params.confidence_threshold.toFixed(2)}</span></label>
                    <input type="range" id="confidence_${cameraId}" min="0.1" max="1.0" step="0.05"
                           value="${cameraData[cameraId].params.confidence_threshold}"
                           oninput="updateParam('${cameraId}', 'confidence_threshold', parseFloat(this.value))">
                </div>

                <div class="button-row">
                    <button class="btn-secondary" onclick="resetDefaults('${cameraId}')">Reset</button>
                    <button class="btn-secondary" onclick="toggleRecording('${cameraId}')" ${cameraData[cameraId].connected ? '' : 'disabled'}>
                        ${cameraData[cameraId].recording ? 'Stop Recording' : 'Start Recording'}
                    </button>
                    <button class="btn-tertiary" onclick="toggleFocus('${cameraId}')">
                        ${focusedCameraId === cameraId ? 'Unfocus' : 'Focus'}
                    </button>
                    <button class="btn-danger" onclick="removeCamera('${cameraId}')" ${cameraData[cameraId].connected ? 'disabled' : ''}>
                        Remove
                    </button>
                </div>
            </div>
        </div>
    `).join('');

    setupResizeObservers();
    applySizeClamps();
}

function getVideoSizeStyle(cameraId) {
    const size = cameraData[cameraId]?.videoSize;
    const styles = [];
    const aspectRatio = getCameraAspectRatio(cameraId);
    styles.push(`--video-aspect-ratio:${aspectRatio.toFixed(6)};`);
    if (focusedCameraId === cameraId) {
        styles.push('width:100%;');
        return styles.join('');
    }
    if (size && size.width > 0) {
        styles.push(`width:${size.width}px;`);
    }
    return styles.join('');
}

function handleVideoMetadataLoad(cameraId, img) {
    if (!cameraData[cameraId] || !img || img.naturalWidth <= 0 || img.naturalHeight <= 0) {
        return;
    }

    const discoveredRatio = img.naturalWidth / img.naturalHeight;
    if (!isValidAspectRatio(discoveredRatio)) {
        return;
    }

    const previousRatio = getCameraAspectRatio(cameraId);
    if (Math.abs(previousRatio - discoveredRatio) < 0.01) {
        return;
    }

    cameraData[cameraId].aspectRatio = discoveredRatio;
    const wrap = document.getElementById('videoWrap_' + cameraId);
    if (wrap) {
        wrap.style.setProperty('--video-aspect-ratio', discoveredRatio.toFixed(6));
    }

    if (cameraData[cameraId].videoSize?.width > 0) {
        cameraData[cameraId].videoSize.height = Math.round(cameraData[cameraId].videoSize.width / discoveredRatio);
        queueSaveVideoSize(cameraId, cameraData[cameraId].videoSize);
    }
}

function parseSavedVideoSizeMap() {
    const stored = localStorage.getItem(videoSizeStorageKey);
    if (!stored) {
        return null;
    }
    const map = JSON.parse(stored);
    if (!map || typeof map !== 'object') {
        return null;
    }
    return map;
}

function sanitizeVideoSize(rawSize) {
    if (!rawSize || !Number.isFinite(rawSize.width) || !Number.isFinite(rawSize.height)) {
        return null;
    }

    const width = Math.round(rawSize.width);
    const height = Math.round(rawSize.height);
    if (width <= 0 || height <= 0) {
        return null;
    }

    return { width, height };
}

function isValidAspectRatio(aspectRatio) {
    return Number.isFinite(aspectRatio) && aspectRatio > 0.2 && aspectRatio < 5;
}

function getCameraAspectRatio(cameraId) {
    const saved = cameraData[cameraId]?.aspectRatio;
    if (isValidAspectRatio(saved)) {
        return saved;
    }
    return defaultVideoAspectRatio;
}

function getSavedVideoAspectRatio(cameraId) {
    try {
        const map = parseSavedVideoSizeMap();
        const size = map?.[cameraId];
        if (!size) {
            return defaultVideoAspectRatio;
        }

        if (isValidAspectRatio(size.aspectRatio)) {
            return size.aspectRatio;
        }

        const normalizedSize = sanitizeVideoSize(size);
        if (!normalizedSize) {
            return defaultVideoAspectRatio;
        }

        const inferredRatio = normalizedSize.width / normalizedSize.height;
        return isValidAspectRatio(inferredRatio) ? inferredRatio : defaultVideoAspectRatio;
    } catch (err) {
        console.warn('Failed to load saved video aspect ratio', err);
        return defaultVideoAspectRatio;
    }
}

function getSavedVideoSize(cameraId) {
    try {
        const map = parseSavedVideoSizeMap();
        const size = map?.[cameraId];
        return sanitizeVideoSize(size);
    } catch (err) {
        console.warn('Failed to load saved video sizes', err);
        return null;
    }
}

function saveVideoSize(cameraId, size) {
    try {
        const normalizedSize = sanitizeVideoSize(size);
        if (!normalizedSize) {
            return;
        }

        const map = parseSavedVideoSizeMap() || {};
        map[cameraId] = {
            width: normalizedSize.width,
            height: normalizedSize.height,
            aspectRatio: getCameraAspectRatio(cameraId)
        };
        localStorage.setItem(videoSizeStorageKey, JSON.stringify(map));
    } catch (err) {
        console.warn('Failed to save video size', err);
    }
}

function queueSaveVideoSize(cameraId, size) {
    if (resizeSaveTimers[cameraId]) {
        clearTimeout(resizeSaveTimers[cameraId]);
    }

    resizeSaveTimers[cameraId] = setTimeout(() => {
        saveVideoSize(cameraId, size);
        delete resizeSaveTimers[cameraId];
    }, resizeSaveDebounceMs);
}

function applySizeClamps() {
    Object.keys(cameraData).forEach(cameraId => {
        const wrapper = document.getElementById('videoWrap_' + cameraId);
        if (!wrapper) return;

        const parentWidth = wrapper.parentElement?.clientWidth || wrapper.clientWidth;
        if (!parentWidth) return;

        const style = window.getComputedStyle(wrapper);
        const minWidth = Number.parseFloat(style.minWidth) || 280;
        const maxAllowedWidth = Math.max(minWidth, Math.round(parentWidth));
        const currentWidth = wrapper.getBoundingClientRect().width;

        const clampedWidth = Math.max(minWidth, Math.min(Math.round(currentWidth), maxAllowedWidth));
        const aspectRatio = getCameraAspectRatio(cameraId);
        if (Math.abs(currentWidth - clampedWidth) > 1) {
            wrapper.style.width = `${clampedWidth}px`;
        }

        if (clampedWidth > 0) {
            cameraData[cameraId].videoSize = {
                width: clampedWidth,
                height: Math.round(clampedWidth / aspectRatio)
            };
            queueSaveVideoSize(cameraId, cameraData[cameraId].videoSize);
        }
    });
}

function setupResizeObservers() {
    Object.keys(cameraData).forEach(cameraId => {
        const wrapper = document.getElementById('videoWrap_' + cameraId);
        if (!wrapper || resizeObservers[cameraId]) return;

        const observer = new ResizeObserver(entries => {
            for (const entry of entries) {
                if (!entry.contentRect || !wrapper.offsetParent) {
                    continue;
                }

                const parentWidth = wrapper.parentElement?.clientWidth || entry.contentRect.width;
                const minWidth = Number.parseFloat(window.getComputedStyle(wrapper).minWidth) || 280;
                const maxAllowedWidth = Math.max(minWidth, Math.round(parentWidth));
                const nextWidth = Math.max(minWidth, Math.min(Math.round(entry.contentRect.width), maxAllowedWidth));
                const aspectRatio = getCameraAspectRatio(cameraId);
                const nextHeight = Math.round(nextWidth / aspectRatio);

                if (Math.abs(entry.contentRect.width - nextWidth) > 1) {
                    wrapper.style.width = `${nextWidth}px`;
                }

                if (nextWidth > 0 && nextHeight > 0) {
                    cameraData[cameraId].videoSize = {
                        width: nextWidth,
                        height: nextHeight
                    };
                    queueSaveVideoSize(cameraId, cameraData[cameraId].videoSize);
                }
            }
        });

        observer.observe(wrapper);
        resizeObservers[cameraId] = observer;
    });
}

function toggleFocus(cameraId) {
    autoFocusEnabled = false;
    focusedCameraId = focusedCameraId === cameraId ? null : cameraId;
    updateAutoFocusButton();
    renderCameras();
}

function removeCamera(cameraId) {
    if (!cameraData[cameraId] || cameraData[cameraId].connected) {
        return;
    }
    if (resizeObservers[cameraId]) {
        resizeObservers[cameraId].disconnect();
        delete resizeObservers[cameraId];
    }
    if (resizeSaveTimers[cameraId]) {
        clearTimeout(resizeSaveTimers[cameraId]);
        delete resizeSaveTimers[cameraId];
    }
    delete cameraData[cameraId];
    delete fpsCounters[cameraId];
    if (focusedCameraId === cameraId) {
        focusedCameraId = null;
    }
    renderCameras();
    if (autoFocusEnabled) {
        evaluateAutoFocus(true);
    }
}

function updateCameraUI(cameraId, params) {
    const tracking = document.getElementById('tracking_' + cameraId);
    const drawSkeleton = document.getElementById('drawSkeleton_' + cameraId);
    const drawStats = document.getElementById('drawStats_' + cameraId);
    const manualPan = document.getElementById('manualPan_' + cameraId);
    const manualTilt = document.getElementById('manualTilt_' + cameraId);
    const centerButton = document.getElementById('centerServo_' + cameraId);
    const manualHint = document.getElementById('manualControlHint_' + cameraId);
    const panSpeed = document.getElementById('panSpeed_' + cameraId);
    const tiltSpeed = document.getElementById('tiltSpeed_' + cameraId);
    const deadzone = document.getElementById('deadzone_' + cameraId);
    const confidence = document.getElementById('confidence_' + cameraId);

    if (tracking) tracking.checked = params.tracking_enabled;
    if (drawSkeleton) drawSkeleton.checked = Boolean(params.draw_skeleton);
    if (drawStats) drawStats.checked = Boolean(params.draw_stats);
    if (manualPan) manualPan.disabled = params.tracking_enabled;
    if (manualTilt) manualTilt.disabled = params.tracking_enabled;
    if (centerButton) centerButton.disabled = params.tracking_enabled;
    if (manualHint) manualHint.style.display = params.tracking_enabled ? 'block' : 'none';
    if (panSpeed) {
        panSpeed.value = params.pan_speed;
        document.getElementById('panSpeedValue_' + cameraId).textContent = params.pan_speed;
    }
    if (tiltSpeed) {
        tiltSpeed.value = params.tilt_speed;
        document.getElementById('tiltSpeedValue_' + cameraId).textContent = params.tilt_speed;
    }
    if (deadzone) {
        deadzone.value = params.deadzone;
        document.getElementById('deadzoneValue_' + cameraId).textContent = params.deadzone.toFixed(2);
    }
    if (confidence) {
        confidence.value = params.confidence_threshold;
        document.getElementById('confidenceValue_' + cameraId).textContent = params.confidence_threshold.toFixed(2);
    }
}

function updateParam(cameraId, param, value) {
    cameraData[cameraId].params[param] = value;

    if (param === 'pan_speed') {
        document.getElementById('panSpeedValue_' + cameraId).textContent = value;
    } else if (param === 'tilt_speed') {
        document.getElementById('tiltSpeedValue_' + cameraId).textContent = value;
    } else if (param === 'deadzone') {
        document.getElementById('deadzoneValue_' + cameraId).textContent = value.toFixed(2);
    } else if (param === 'confidence_threshold') {
        document.getElementById('confidenceValue_' + cameraId).textContent = value.toFixed(2);
    }

    socket.emit('update_params', {
        camera_id: cameraId,
        params: cameraData[cameraId].params
    });

    if (param === 'tracking_enabled') {
        updateCameraUI(cameraId, cameraData[cameraId].params);
    }
}

function formatViewScore(score) {
    if (!Number.isFinite(score)) {
        return '0.0%';
    }
    return `${(score * 100).toFixed(1)}%`;
}

function updateAutoFocusButton() {
    const button = document.getElementById('autoFocusButton');
    if (!button) {
        return;
    }

    button.textContent = `Auto-Focus Best Score: ${autoFocusEnabled ? 'On' : 'Off'}`;
    button.classList.toggle('active', autoFocusEnabled);
}

function syncAutoFocusControlUI() {
    const thresholdInput = document.getElementById('autoFocusThreshold');
    const thresholdValue = document.getElementById('autoFocusThresholdValue');
    const cooldownInput = document.getElementById('autoFocusCooldown');
    const cooldownValue = document.getElementById('autoFocusCooldownValue');

    if (thresholdInput) {
        thresholdInput.value = autoFocusSettings.scoreThreshold.toFixed(2);
    }
    if (thresholdValue) {
        thresholdValue.textContent = autoFocusSettings.scoreThreshold.toFixed(2);
    }
    if (cooldownInput) {
        cooldownInput.value = (autoFocusSettings.switchCooldownMs / 1000).toFixed(1);
    }
    if (cooldownValue) {
        cooldownValue.textContent = `${(autoFocusSettings.switchCooldownMs / 1000).toFixed(1)}s`;
    }
}

function updateAutoFocusThreshold(rawValue) {
    const parsed = parseFloat(rawValue);
    if (!Number.isFinite(parsed)) {
        return;
    }
    autoFocusSettings.scoreThreshold = Math.max(0, Math.min(1, parsed));
    syncAutoFocusControlUI();
    if (autoFocusEnabled) {
        evaluateAutoFocus(true);
    }
}

function updateAutoFocusCooldown(rawValue) {
    const parsedSeconds = parseFloat(rawValue);
    if (!Number.isFinite(parsedSeconds)) {
        return;
    }
    const clampedSeconds = Math.max(0, Math.min(10, parsedSeconds));
    autoFocusSettings.switchCooldownMs = Math.round(clampedSeconds * 1000);
    syncAutoFocusControlUI();
}

function toggleAutoFocus() {
    autoFocusEnabled = !autoFocusEnabled;
    if (autoFocusEnabled) {
        lastAutoFocusSwitchAt = 0;
        evaluateAutoFocus(true);
    }
    updateAutoFocusButton();
    renderCameras();
}

function evaluateAutoFocus(forceSwitch = false) {
    if (!autoFocusEnabled) {
        return false;
    }

    const cameraIds = Object.keys(cameraData).filter(cameraId => cameraData[cameraId]?.connected);
    if (cameraIds.length === 0) {
        return false;
    }

    let bestCameraId = null;
    let bestScore = -Infinity;

    cameraIds.forEach(cameraId => {
        const score = Number.isFinite(cameraData[cameraId].viewScore) ? cameraData[cameraId].viewScore : 0;
        if (score > bestScore) {
            bestScore = score;
            bestCameraId = cameraId;
        }
    });

    if (!bestCameraId) {
        return false;
    }

    const currentCamera = focusedCameraId ? cameraData[focusedCameraId] : null;
    const currentScore = currentCamera && currentCamera.connected
        ? (Number.isFinite(currentCamera.viewScore) ? currentCamera.viewScore : 0)
        : -Infinity;
    const now = Date.now();
    const cooldownSatisfied = forceSwitch || (now - lastAutoFocusSwitchAt) >= autoFocusSettings.switchCooldownMs;

    if (!focusedCameraId || !currentCamera || !currentCamera.connected) {
        if (focusedCameraId !== bestCameraId) {
            focusedCameraId = bestCameraId;
            lastAutoFocusSwitchAt = now;
            renderCameras();
        }
        return true;
    }

    if (
        bestCameraId !== focusedCameraId &&
        bestScore - currentScore >= autoFocusSettings.scoreThreshold &&
        cooldownSatisfied
    ) {
        focusedCameraId = bestCameraId;
        lastAutoFocusSwitchAt = now;
        renderCameras();
        return true;
    }

    return false;
}

function updateServoPreview(cameraId, axis, value) {
    if (!cameraData[cameraId]) {
        return;
    }

    cameraData[cameraId].servo[axis] = value;

    const valueElement = document.getElementById(`manual${axis === 'pan' ? 'Pan' : 'Tilt'}Value_${cameraId}`);
    if (valueElement) {
        valueElement.textContent = `${value}°`;
    }
}

function moveServo(cameraId) {
    if (!cameraData[cameraId] || cameraData[cameraId].params.tracking_enabled) {
        if (cameraData[cameraId]) {
            updateParam(cameraId, 'tracking_enabled', false);
        }
    }

    if (!cameraData[cameraId] || cameraData[cameraId].params.tracking_enabled) {
        return;
    }

    socket.emit('move_servo', {
        camera_id: cameraId,
        pan: degreesToPulse(cameraData[cameraId].servo.pan),
        tilt: degreesToPulse(cameraData[cameraId].servo.tilt)
    });
}

function centerServo(cameraId) {
    if (cameraData[cameraId] && cameraData[cameraId].params.tracking_enabled) {
        return;
    }

    if (cameraData[cameraId]) {
        cameraData[cameraId].servo = { ...servoDefaults };
    }
    socket.emit('center_servo', { camera_id: cameraId });
}

function resetDefaults(cameraId) {
    cameraData[cameraId].params = { ...defaultParams };
    updateCameraUI(cameraId, defaultParams);
    socket.emit('update_params', {
        camera_id: cameraId,
        params: defaultParams
    });
}

function centerAllServos() {
    Object.keys(cameraData).forEach(cameraId => {
        if (!cameraData[cameraId].params.tracking_enabled) {
            socket.emit('center_servo', { camera_id: cameraId });
        }
    });
}

function toggleRecording(cameraId) {
    if (!cameraData[cameraId]) {
        return;
    }
    if (cameraData[cameraId].recording) {
        socket.emit('stop_recording', { camera_id: cameraId });
    } else {
        socket.emit('start_recording', { camera_id: cameraId });
    }
}

function enableAllTracking() {
    Object.keys(cameraData).forEach(cameraId => {
        updateParam(cameraId, 'tracking_enabled', true);
    });
}

function disableAllTracking() {
    Object.keys(cameraData).forEach(cameraId => {
        updateParam(cameraId, 'tracking_enabled', false);
    });
}

function setAllDrawSkeleton(enabled) {
    Object.keys(cameraData).forEach(cameraId => {
        updateParam(cameraId, 'draw_skeleton', enabled);
    });
}

function setAllDrawStats(enabled) {
    Object.keys(cameraData).forEach(cameraId => {
        updateParam(cameraId, 'draw_stats', enabled);
    });
}

syncAutoFocusControlUI();