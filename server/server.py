from flask import Flask, render_template, request, send_from_directory
from flask_socketio import SocketIO, emit
from time import sleep, time
from datetime import datetime
import threading
import logging
from zeroconf import ServiceInfo, Zeroconf
import socket
import os
import base64
import subprocess
import argparse

app = Flask(__name__)
app.config['SECRET_KEY'] = 'multi-camera-secret'

# silence werkzeug request logs for socketio
class NoSocketIOFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        return "/socket.io/" not in message

werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.addFilter(NoSocketIOFilter())

socketio = SocketIO(
    app, 
    cors_allowed_origins="*", 
    async_mode='threading',
    logger=False,               # silence socketio logs
    engineio_logger=False       # silence engineio logs
)

# track connected cameras: {socket_id: camera_id}
socket_to_camera = {}
# track camera status: {camera_id: {'connected': bool, 'last_seen': timestamp}}
cameras = {}

DEFAULT_CAMERA_PARAMS = {
    'pan_speed': 40,
    'tilt_speed': 30,
    'deadzone': 0.1,
    'confidence_threshold': 0.80,
    'tracking_enabled': False,
    'draw_skeleton': True,
    'draw_stats': True
}

# recording state
recordings_dir = os.path.join(os.path.dirname(__file__), "recordings")
os.makedirs(recordings_dir, exist_ok=True)
recording_sessions = {}
recording_requests = {}
recording_lock = threading.Lock()
recording_fps_default = int(os.environ.get("RECORDING_FPS", "30"))
recording_fps_min = int(os.environ.get("RECORDING_FPS_MIN", "5"))
recording_fps_max = int(os.environ.get("RECORDING_FPS_MAX", "60"))
recording_sample_frames = int(os.environ.get("RECORDING_SAMPLE_FRAMES", "12"))
recording_sample_seconds = float(os.environ.get("RECORDING_SAMPLE_SECONDS", "1.0"))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/recordings/<path:filename>')
def download_recording(filename):
    return send_from_directory(recordings_dir, filename, as_attachment=True)

@socketio.on('register_camera')
def handle_register(data):
    camera_id = data['camera_id']
    sid = request.sid
    
    socket_to_camera[sid] = camera_id
    incoming_params = data.get('params') or {}
    merged_params = {**DEFAULT_CAMERA_PARAMS, **incoming_params}

    cameras[camera_id] = {
        'connected': True,
        'last_seen': time(),
        'params': merged_params,
        'recording': False,
        'view_score': float(data.get('view_score', 0.0) or 0.0)
    }
    print(f"Camera registered: {camera_id} (sid: {sid})")
    broadcast_camera_list()

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    if sid in socket_to_camera:
        camera_id = socket_to_camera[sid]
        del socket_to_camera[sid]
        
        if camera_id in cameras:
            cameras[camera_id]['connected'] = False
            print(f"Camera disconnected: {camera_id}")
            stop_recording_internal(camera_id)
        
        # Notify all web clients
        socketio.emit('camera_disconnected', {'camera_id': camera_id})
        broadcast_camera_list()

@socketio.on('camera_frame')
def handle_camera_frame(data):
    camera_id = data['camera_id']
    view_score = float(data.get('view_score', 0.0) or 0.0)
    
    # Update last seen
    if camera_id in cameras:
        cameras[camera_id]['last_seen'] = time()
        cameras[camera_id]['connected'] = True
        cameras[camera_id]['view_score'] = view_score
    
    # Relay frame to all web clients
    socketio.emit('frame_' + camera_id, {
        'image': data['image'],
        'params': data['params'],
        'view_score': view_score
    })

    if is_recording_active(camera_id):
        image_bytes = decode_image_bytes(data.get('image'))
        if image_bytes:
            write_recording_frame(camera_id, image_bytes)

@socketio.on('update_params')
def handle_update_params(data):
    camera_id = data['camera_id']
    params = data['params']
    merged_params = {**DEFAULT_CAMERA_PARAMS, **params}
    
    # Update stored params
    if camera_id in cameras:
        cameras[camera_id]['params'] = merged_params
    
    # Send to specific camera
    socketio.emit(f'update_params_{camera_id}', merged_params)
    
    # Broadcast to all web clients
    socketio.emit('params_updated', {'camera_id': camera_id, 'params': merged_params})

@socketio.on('center_servo')
def handle_center_servo(data):
    camera_id = data['camera_id']
    socketio.emit(f'center_servo_{camera_id}')
    socketio.emit('servo_centered', {'camera_id': camera_id})

@socketio.on('move_servo')
def handle_move_servo(data):
    camera_id = data['camera_id']
    pan = data['pan']
    tilt = data['tilt']

    camera_info = cameras.get(camera_id)
    if camera_info and camera_info.get('params', {}).get('tracking_enabled'):
        socketio.emit('servo_move_rejected', {
            'camera_id': camera_id,
            'reason': 'Tracking is enabled'
        })
        return

    if camera_info:
        camera_info['params']['tracking_enabled'] = False
        socketio.emit(f'update_params_{camera_id}', camera_info['params'])
        socketio.emit('params_updated', {'camera_id': camera_id, 'params': camera_info['params']})

    socketio.emit(f'move_servo_{camera_id}', {'pan': pan, 'tilt': tilt})
    socketio.emit('servo_moved', {'camera_id': camera_id, 'pan': pan, 'tilt': tilt})

@socketio.on('get_cameras')
def handle_get_cameras():
    camera_list = [
        {
            'camera_id': cid,
            'connected': info['connected'],
            'params': info['params'],
            'recording': info.get('recording', False),
            'view_score': float(info.get('view_score', 0.0) or 0.0)
        }
        for cid, info in cameras.items()
        if info['connected']  # Only send connected cameras on initial load
    ]
    emit('camera_list', camera_list)

def broadcast_camera_list():
    camera_list = [
        {
            'camera_id': cid,
            'connected': info['connected'],
            'params': info['params'],
            'recording': info.get('recording', False),
            'view_score': float(info.get('view_score', 0.0) or 0.0)
        }
        for cid, info in cameras.items()
    ]
    socketio.emit('camera_list', camera_list)

@socketio.on('start_recording')
def handle_start_recording(data):
    camera_id = data['camera_id']
    if camera_id not in cameras:
        emit('recording_error', {'camera_id': camera_id, 'error': 'Camera not found'})
        return

    with recording_lock:
        if camera_id in recording_sessions or camera_id in recording_requests:
            emit('recording_error', {'camera_id': camera_id, 'error': 'Already recording'})
            return
        recording_requests[camera_id] = {
            'frames': [],
            'timestamps': []
        }
        cameras[camera_id]['recording'] = True

    socketio.emit('recording_started', {'camera_id': camera_id})
    broadcast_camera_list()

@socketio.on('stop_recording')
def handle_stop_recording(data):
    camera_id = data['camera_id']
    path, filename = stop_recording_internal(camera_id)
    download_url = f"/recordings/{filename}" if filename else None
    socketio.emit('recording_stopped', {
        'camera_id': camera_id,
        'path': path,
        'filename': filename,
        'download_url': download_url
    })
    broadcast_camera_list()

def is_recording_active(camera_id):
    with recording_lock:
        return camera_id in recording_requests or camera_id in recording_sessions

def decode_image_bytes(image_b64):
    if not image_b64:
        return None
    try:
        return base64.b64decode(image_b64)
    except Exception:
        return None

def start_recording_session(camera_id, fps):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{camera_id}_{timestamp}.mp4"
    path = os.path.join(recordings_dir, filename)

    cmd = [
        "ffmpeg",
        "-y",
        "-f", "mjpeg",
        "-framerate", str(fps),
        "-i", "pipe:0",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        path
    ]

    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except FileNotFoundError:
        return None

    recording_sessions[camera_id] = {
        'process': process,
        'path': path,
        'filename': filename
    }
    return path

def write_recording_frame(camera_id, image_bytes):
    with recording_lock:
        if camera_id in recording_requests and camera_id not in recording_sessions:
            request_state = recording_requests[camera_id]
            request_state['frames'].append(image_bytes)
            request_state['timestamps'].append(time())

            timestamps = request_state['timestamps']
            if len(timestamps) < recording_sample_frames:
                return

            duration = timestamps[-1] - timestamps[0]
            if duration < recording_sample_seconds:
                return

            measured_fps = len(timestamps) / max(duration, 0.001)
            measured_fps = max(recording_fps_min, min(recording_fps_max, measured_fps))

            path = start_recording_session(camera_id, measured_fps)
            if not path:
                recording_requests.pop(camera_id, None)
                if camera_id in cameras:
                    cameras[camera_id]['recording'] = False
                socketio.emit('recording_error', {
                    'camera_id': camera_id,
                    'error': 'ffmpeg not available on server'
                })
                broadcast_camera_list()
                return

            session = recording_sessions.get(camera_id)
            if not session:
                recording_requests.pop(camera_id, None)
                return

            process = session['process']
            buffered_frames = request_state['frames']
            recording_requests.pop(camera_id, None)

            try:
                for frame_bytes in buffered_frames:
                    process.stdin.write(frame_bytes)
                process.stdin.flush()
            except Exception:
                stop_recording_internal(camera_id)
            return

        session = recording_sessions.get(camera_id)
        if not session:
            return

        process = session['process']

    try:
        process.stdin.write(image_bytes)
        process.stdin.flush()
    except Exception:
        stop_recording_internal(camera_id)

def stop_recording_internal(camera_id):
    path = None
    filename = None
    with recording_lock:
        recording_requests.pop(camera_id, None)
        session = recording_sessions.pop(camera_id, None)
        if session:
            process = session['process']
            path = session['path']
            filename = session.get('filename')
            try:
                if process.stdin:
                    process.stdin.close()
            except Exception:
                pass
            try:
                process.wait(timeout=5)
            except Exception:
                process.terminate()

        if camera_id in cameras:
            cameras[camera_id]['recording'] = False

    return path, filename

# Heartbeat checker - mark cameras as offline if no frames received
def heartbeat_checker():
    while True:
        # Check every 5 seconds
        sleep(5)
        now = time()
        for camera_id, info in cameras.items():
            # If no frame received in last 10 seconds, mark as disconnected
            if info['connected'] and (now - info['last_seen']) > 10:
                info['connected'] = False
                print(f"Camera timeout: {camera_id}")
                stop_recording_internal(camera_id)
                socketio.emit('camera_disconnected', {'camera_id': camera_id})
                broadcast_camera_list()


def advertise_service():
    try:
        # Get the actual local IP by connecting to an external address
        # This doesn't actually connect, just determines which interface would be used
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        
        hostname = socket.gethostname()
        
        service_info = ServiceInfo(
            "_grappler._tcp.local.",
            "GrapplerCameraServer._grappler._tcp.local.",
            addresses=[socket.inet_aton(local_ip)],
            port=10000,
            properties={'version': '1.0', 'camera_server': 'true'},
            server=f"{hostname}.local."
        )
        
        zeroconf = Zeroconf()
        zeroconf.register_service(service_info)
        print(f"mDNS service advertised: {local_ip}:10000")
        
        try:
            while True:
                sleep(1)
        except KeyboardInterrupt:
            zeroconf.unregister_service(service_info)
            zeroconf.close()
    except Exception as e:
        print(f"mDNS advertisement failed: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Grappler Camera Server")
    parser.add_argument('--port', type=int, default=10000, help='Port to run the server on')
    parser.add_argument('--mdns', action='store_true', help='Enable mDNS advertisement')
    args = parser.parse_args()

    # Start mDNS advertisement if enabled
    if args.mdns:
        advertise_thread = threading.Thread(target=advertise_service, daemon=True)
        advertise_thread.start()
    
    # Start heartbeat checker
    heartbeat_thread = threading.Thread(target=heartbeat_checker, daemon=True)
    heartbeat_thread.start()
    
    socketio.run(app, host='0.0.0.0', port=args.port, allow_unsafe_werkzeug=True)
