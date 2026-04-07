const socket = io();
const cameraData = {};
const fpsCounters = {};
const resizeObservers = {};
let focusedCameraId = null;
const videoSizeStorageKey = 'cameraVideoSizes';

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
            cameraData[cameraId] = {
                connected: camera.connected,
                params: { ...defaultParams, ...(camera.params || {}) },
                videoSize: getSavedVideoSize(cameraId),
                recording: camera.recording || false,
                servo: { ...servoDefaults }
            };
            setupCameraListeners(cameraId);
        } else {
            cameraData[cameraId].connected = camera.connected;
            cameraData[cameraId].params = { ...defaultParams, ...(camera.params || cameraData[cameraId].params || {}) };
            cameraData[cameraId].recording = camera.recording || false;
            if (!cameraData[cameraId].servo) {
                cameraData[cameraId].servo = { ...servoDefaults };
            }
            if (!cameraData[cameraId].videoSize) {
                cameraData[cameraId].videoSize = getSavedVideoSize(cameraId);
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
                <span id="status_${cameraId}" class="camera-status ${cameraData[cameraId].connected ? 'online' : 'offline'}">
                    ${cameraData[cameraId].connected ? 'Online' : 'Offline'}
                </span>
            </div>

            <div class="video-container">
                <div id="videoWrap_${cameraId}" class="video-resize" style="${getVideoSizeStyle(cameraId)}">
                    <img id="video_${cameraId}" alt="${cameraId} feed">
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
    if (!size || size.width <= 0 || size.height <= 0) {
        return '';
    }
    return `width:${size.width}px;height:${size.height}px;`;
}

function getSavedVideoSize(cameraId) {
    try {
        const stored = localStorage.getItem(videoSizeStorageKey);
        if (!stored) return null;
        const map = JSON.parse(stored);
        const size = map?.[cameraId];
        if (!size || !Number.isFinite(size.width) || !Number.isFinite(size.height)) {
            return null;
        }
        return { width: size.width, height: size.height };
    } catch (err) {
        console.warn('Failed to load saved video sizes', err);
        return null;
    }
}

function saveVideoSize(cameraId, size) {
    try {
        const stored = localStorage.getItem(videoSizeStorageKey);
        const map = stored ? JSON.parse(stored) : {};
        map[cameraId] = { width: size.width, height: size.height };
        localStorage.setItem(videoSizeStorageKey, JSON.stringify(map));
    } catch (err) {
        console.warn('Failed to save video size', err);
    }
}

function applySizeClamps() {
    Object.keys(cameraData).forEach(cameraId => {
        const wrapper = document.getElementById('videoWrap_' + cameraId);
        if (!wrapper) return;
        const parentWidth = wrapper.parentElement?.clientWidth || wrapper.clientWidth;
        const currentWidth = wrapper.getBoundingClientRect().width;
        if (parentWidth && currentWidth > parentWidth) {
            wrapper.style.width = `${Math.round(parentWidth)}px`;
            cameraData[cameraId].videoSize = {
                width: Math.round(parentWidth),
                height: Math.round(wrapper.getBoundingClientRect().height)
            };
            saveVideoSize(cameraId, cameraData[cameraId].videoSize);
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
                const nextWidth = Math.min(entry.contentRect.width, parentWidth);
                const nextHeight = entry.contentRect.height;

                if (nextWidth !== entry.contentRect.width) {
                    wrapper.style.width = `${Math.round(nextWidth)}px`;
                }

                if (nextWidth > 0 && nextHeight > 0) {
                    cameraData[cameraId].videoSize = {
                        width: Math.round(nextWidth),
                        height: Math.round(nextHeight)
                    };
                    saveVideoSize(cameraId, cameraData[cameraId].videoSize);
                }
            }
        });

        observer.observe(wrapper);
        resizeObservers[cameraId] = observer;
    });
}

function toggleFocus(cameraId) {
    focusedCameraId = focusedCameraId === cameraId ? null : cameraId;
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
    delete cameraData[cameraId];
    delete fpsCounters[cameraId];
    if (focusedCameraId === cameraId) {
        focusedCameraId = null;
    }
    renderCameras();
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