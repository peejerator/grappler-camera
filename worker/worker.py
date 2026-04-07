import cv2
from ultralytics import YOLO
import numpy as np
from PCA9685 import PCA9685
import socketio
import base64
from time import sleep
from zeroconf import ServiceBrowser, Zeroconf
import socket
import argparse
from enum import IntEnum, auto

# config
CAMERA_ID = socket.gethostname()
SERVER_IP = None  # discovered via mDNS
SERVER_DISCOVERED = False

# servo stuff
pwm = PCA9685(0x40, debug=False)
pwm.setPWMFreq(50)

TILT_CHANNEL = 0
PAN_CHANNEL = 1

PAN_MIN = 900
PAN_MAX = 2100
PAN_CENTER = 1500

TILT_MIN = 900
TILT_MAX = 2100
TILT_CENTER = 1500

pan_pulse = PAN_CENTER
tilt_pulse = TILT_CENTER

# center servos at the start
pwm.setServoPulse(PAN_CHANNEL, pan_pulse)
pwm.setServoPulse(TILT_CHANNEL, tilt_pulse)

# Tracking parameters
params = {
    'pan_speed': 40,
    'tilt_speed': 30,
    'deadzone': 0.1,
    'confidence_threshold': 0.80,
    'tracking_enabled': False
}

# Keypoint indices (COCO format)
# NOSE = 0
# LEFT_SHOULDER = 5
# RIGHT_SHOULDER = 6

class Keypoint(IntEnum):
    NOSE = 0
    LEFT_EYE = auto()
    RIGHT_EYE = auto()
    LEFT_EAR = auto()
    RIGHT_EAR = auto()
    LEFT_SHOULDER = auto()
    RIGHT_SHOULDER = auto()
    LEFT_ELBOW = auto()
    RIGHT_ELBOW = auto()
    LEFT_WRIST = auto()
    RIGHT_WRIST = auto()
    LEFT_HIP = auto()
    RIGHT_HIP = auto()
    LEFT_KNEE = auto()
    RIGHT_KNEE = auto()
    LEFT_ANKLE = auto()
    RIGHT_ANKLE = auto()

# Keypoint scores
KEYPOINT_SCORES = {
    Keypoint.NOSE: 0.8,
    Keypoint.LEFT_EYE: 0,
    Keypoint.RIGHT_EYE: 0,
    Keypoint.LEFT_EAR: 0,
    Keypoint.RIGHT_EAR: 0,
    Keypoint.LEFT_SHOULDER: 0,
    Keypoint.RIGHT_SHOULDER: 0,
    Keypoint.LEFT_ELBOW: 0,
    Keypoint.RIGHT_ELBOW: 0,
    Keypoint.LEFT_WRIST: 0.9,
    Keypoint.RIGHT_WRIST: 0.9,
    Keypoint.LEFT_HIP: 0,
    Keypoint.RIGHT_HIP: 0,
    Keypoint.LEFT_KNEE: 0,
    Keypoint.RIGHT_KNEE: 0,
    Keypoint.LEFT_ANKLE: 0,
    Keypoint.RIGHT_ANKLE: 0
}

KEYPOINT_CONFIDENCE_FLOOR = 0.30

SKELETON = [
    (Keypoint.LEFT_SHOULDER, Keypoint.RIGHT_SHOULDER),
    (Keypoint.LEFT_SHOULDER, Keypoint.LEFT_ELBOW),
    (Keypoint.RIGHT_SHOULDER, Keypoint.RIGHT_ELBOW),
    (Keypoint.LEFT_ELBOW, Keypoint.LEFT_WRIST),
    (Keypoint.RIGHT_ELBOW, Keypoint.RIGHT_WRIST),
    (Keypoint.LEFT_SHOULDER, Keypoint.LEFT_HIP),
    (Keypoint.RIGHT_SHOULDER, Keypoint.RIGHT_HIP),
    (Keypoint.LEFT_HIP, Keypoint.LEFT_KNEE),
    (Keypoint.RIGHT_HIP, Keypoint.RIGHT_KNEE),
    (Keypoint.LEFT_KNEE, Keypoint.LEFT_ANKLE),
    (Keypoint.RIGHT_KNEE, Keypoint.RIGHT_ANKLE),
    (Keypoint.NOSE, Keypoint.LEFT_EYE),
    (Keypoint.NOSE, Keypoint.RIGHT_EYE),
    (Keypoint.LEFT_EYE, Keypoint.LEFT_EAR),
    (Keypoint.RIGHT_EYE, Keypoint.RIGHT_EAR)
]

# Socket.IO client
sio = socketio.Client(reconnection=True, reconnection_attempts=0, reconnection_delay=1)

def discover_server(wait_time):
    """Discover server via mDNS and return its address"""
    global SERVER_IP, SERVER_DISCOVERED
    
    discovered_server = {'ip': None}
    
    class ServerListener:
        def add_service(self, zeroconf, service_type, name):
            try:
                info = zeroconf.get_service_info(service_type, name)
                if info and info.properties.get(b'camera_server') == b'true':
                    ip = socket.inet_ntoa(info.addresses[0])
                    discovered_server['ip'] = f"http://{ip}:{info.port}"
                    print(f"Server discovered: {discovered_server['ip']}")
            except Exception as e:
                print(f"Error discovering server: {e}")
        
        def remove_service(self, zeroconf, service_type, name):
            pass
        
        def update_service(self, zeroconf, service_type, name):
            pass
    
    try:
        zeroconf = Zeroconf()
        ServiceBrowser(zeroconf, "_grappler._tcp.local.", ServerListener())
        
        # wait up to 10 seconds for server discovery
        for i in range(wait_time * 2):
            if discovered_server['ip']:
                SERVER_IP = discovered_server['ip']
                SERVER_DISCOVERED = True
                return
            sleep(0.5)
        
        zeroconf.close()
        
        if not SERVER_IP:
            print("Server not discovered via mDNS")
    except Exception as e:
        print(f"mDNS discovery failed: {e}")

@sio.event
def connect():
    print(f"Connected to central server as {CAMERA_ID}")
    sio.emit('register_camera', {'camera_id': CAMERA_ID, 'params': params})

@sio.event
def disconnect():
    print("Disconnected from central server")

@sio.on(f'update_params_{CAMERA_ID}')
def on_update_params(data):
    global params
    if 'pan_speed' in data:
        params['pan_speed'] = float(data['pan_speed'])
    if 'tilt_speed' in data:
        params['tilt_speed'] = float(data['tilt_speed'])
    if 'deadzone' in data:
        params['deadzone'] = float(data['deadzone'])
    if 'confidence_threshold' in data:
        params['confidence_threshold'] = float(data['confidence_threshold'])
    if 'tracking_enabled' in data:
        params['tracking_enabled'] = bool(data['tracking_enabled'])
    print(f"Params updated: {params}")

@sio.on(f'center_servo_{CAMERA_ID}')
def on_center_servo():
    global pan_pulse, tilt_pulse
    pan_pulse = PAN_CENTER
    tilt_pulse = TILT_CENTER
    pwm.setServoPulse(PAN_CHANNEL, PAN_CENTER)
    pwm.setServoPulse(TILT_CHANNEL, TILT_CENTER)
    print("Servos centered")

@sio.on(f'move_servo_{CAMERA_ID}')
def on_move_servo(data):
    global pan_pulse, tilt_pulse
    if params.get('tracking_enabled'):
        print("Ignoring manual servo command while tracking is enabled")
        return

    params['tracking_enabled'] = False
    pan_pulse = data['pan']
    tilt_pulse = data['tilt']
    pwm.setServoPulse(PAN_CHANNEL, pan_pulse)
    pwm.setServoPulse(TILT_CHANNEL, tilt_pulse)
    print(f"Servos moved to: pan={pan_pulse}, tilt={tilt_pulse}")

def get_chest_position(keypoints):
    left_shoulder = keypoints[Keypoint.LEFT_SHOULDER]
    right_shoulder = keypoints[Keypoint.RIGHT_SHOULDER]
    
    # Check if keypoints are valid (not at origin or very low confidence)
    def is_valid(kp):
        return kp[0] > 0 and kp[1] > 0
    
    if is_valid(left_shoulder) and is_valid(right_shoulder):
        # Use midpoint between shoulders
        chest_x = (left_shoulder[0] + right_shoulder[0]) / 2
        chest_y = (left_shoulder[1] + right_shoulder[1]) / 2
        return (chest_x, chest_y)
    else:
        return None
    
def get_optimal_tracking_position(keypoints: np.ndarray) -> tuple[float, float] | None:
    '''
    Determine optimal tracking position based on keypoints.
    If hips and shoulders are visible, take the midpoint between them as the center of the torso.
    Else if hips are visible, use their midpoint.
    Else if shoulders are visible, use their midpoint.
    Else if wrists are visible, use them.
    Else if face keypoints are visible, use the nose or midpoint between eyes.
    Else if any other keypoint is visible, use it.

    Returns (x, y) coordinates of optimal tracking point or None if no valid keypoints are found.
    '''

    # Check for shoulders and hips
    left_shoulder = keypoints[Keypoint.LEFT_SHOULDER]
    right_shoulder = keypoints[Keypoint.RIGHT_SHOULDER]
    left_hip = keypoints[Keypoint.LEFT_HIP]
    right_hip = keypoints[Keypoint.RIGHT_HIP]

    def is_valid(kp):
        return kp[0] > 0 and kp[1] > 0
    
    def average_kp(*args):
        count = 0
        x, y = 0, 0
        for kp in args:
            x += kp[0]
            y += kp[1]
            count += 1
        if count == 0:
            return None
        return (x / count, y / count)
    
    # If hips and shoulders are valid, use their midpoint as torso center
    if is_valid(left_shoulder) and is_valid(right_shoulder) and is_valid(left_hip) and is_valid(right_hip):
        return average_kp(left_shoulder, right_shoulder, left_hip, right_hip)
    
    # If only hips are valid, use their midpoint
    if is_valid(left_hip) and is_valid(right_hip):
        return average_kp(left_hip, right_hip)
    
    # If only shoulders are valid, use their midpoint
    if is_valid(left_shoulder) and is_valid(right_shoulder):
        return average_kp(left_shoulder, right_shoulder)
    
    # If wrists are valid, use them
    left_wrist = keypoints[Keypoint.LEFT_WRIST]
    right_wrist = keypoints[Keypoint.RIGHT_WRIST]
    if is_valid(left_wrist) and is_valid(right_wrist):
        return average_kp(left_wrist, right_wrist)
    
    # If face keypoints are valid, use nose or midpoint between eyes
    nose = keypoints[Keypoint.NOSE]
    left_eye = keypoints[Keypoint.LEFT_EYE]
    right_eye = keypoints[Keypoint.RIGHT_EYE]
    if is_valid(nose):
        return nose
    
    if is_valid(left_eye) and is_valid(right_eye):
        return average_kp(left_eye, right_eye)
    
    # If any other keypoint is valid, use it
    for kp in keypoints:
        if is_valid(kp):
            return kp


def calculate_person_view_score(keypoint_confidences: np.ndarray | list[float] | None) -> float:
    """Compute weighted pose visibility score for one person in range [0.0, 1.0]."""
    if keypoint_confidences is None:
        return 0.0

    #weighted_conf_sum = 0.0
    weight_sum = 0.0

    for keypoint, weight in KEYPOINT_SCORES.items():
        idx = int(keypoint)
        if idx >= len(keypoint_confidences):
            continue

        confidence = float(keypoint_confidences[idx])
        if confidence < KEYPOINT_CONFIDENCE_FLOOR:
            continue

        #weighted_conf_sum += confidence * weight
        weight_sum += weight

    if weight_sum == 0:
        return 0.0
    #return weighted_conf_sum / weight_sum
    return weight_sum / sum(KEYPOINT_SCORES.values())
    

def tracking_loop():
    global pan_pulse, tilt_pulse
    
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: Could not open camera")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 480)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
    cap.set(cv2.CAP_PROP_FPS, 30)
    
    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_center_x = frame_width / 2
    frame_center_y = frame_height / 2

    print("Loading YOLO11 Pose model...")
    model = YOLO("yolo11n-pose.pt")
    model.overrides['half'] = False
    
    print("Starting pose detection.")
    
    frame_count = 0
    last_results = None
    
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Error: Failed to capture frame")
                break

            frame_count += 1
            
            # Process every 3rd frame for performance
            if frame_count % 3 == 0:
                results = model(frame, imgsz=256, verbose=False)
                last_results = results
            
            if last_results is None:
                emit_frame(frame)
                continue
                
            results = last_results

            for result in results:
                boxes = result.boxes
                kps_all = getattr(result.keypoints, "xy", None)
                kps_conf_all = getattr(result.keypoints, "conf", None)

                if boxes is None or kps_all is None:
                    continue

                if hasattr(kps_all, "cpu"):
                    kps_all_np = kps_all.cpu().numpy()
                else:
                    kps_all_np = kps_all

                if kps_conf_all is not None:
                    if hasattr(kps_conf_all, "cpu"):
                        kps_conf_all_np = kps_conf_all.cpu().numpy()
                    else:
                        kps_conf_all_np = kps_conf_all
                else:
                    kps_conf_all_np = None

                people_count = 0
                person_centers_x = []
                person_centers_y = []
                person_view_scores = []
                
                for i, box in enumerate(boxes):
                    class_id = int(box.cls[0])
                    if class_id != 0:
                        continue

                    conf = float(box.conf[0])

                    if conf < params['confidence_threshold']:
                        continue

                    people_count += 1

                    if kps_conf_all_np is not None and i < len(kps_conf_all_np):
                        person_view_scores.append(calculate_person_view_score(kps_conf_all_np[i]))
                    else:
                        person_view_scores.append(0.0)

                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    
                    # store center x of bounding box for panning
                    person_centers_x.append((x1 + x2) / 2)
                    
                    # get chest position for tilting
                    if i < len(kps_all_np):
                        kps_xy = kps_all_np[i]
                        #chest_pos = get_chest_position(kps_xy)
                        opt_pos = get_optimal_tracking_position(kps_xy)
                        
                        if opt_pos is not None:
                            person_centers_y.append(opt_pos[1])
                            # draw chest circle for each person
                            cv2.circle(frame, (int(opt_pos[0]), int(opt_pos[1])), 8, (255, 0, 255), -1)
                        else:
                            # if chest is not in frame default to the center of the bounding box
                            # TODO: make this smarter so that it tries to find the chest or make
                            #       it dynamic/configurable
                            person_centers_y.append((y1 + y2) / 2)
                    else:
                        # Fallback to bounding box center
                        person_centers_y.append((y1 + y2) / 2)
                    
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(
                        frame, f"Person {conf:.2f}", (x1, y1 - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2
                    )

                    if i >= len(kps_all_np):
                        continue
                    kps_xy = kps_all_np[i]

                    for j, (kx, ky) in enumerate(kps_xy):
                        x, y = int(kx), int(ky)
                        cv2.circle(frame, (x, y), 4, (0, 0, 255), -1)

                    for a, b in SKELETON:
                        if a < len(kps_xy) and b < len(kps_xy):
                            xA, yA = map(int, kps_xy[a])
                            xB, yB = map(int, kps_xy[b])
                            cv2.line(frame, (xA, yA), (xB, yB), (255, 0, 0), 2)

                # servo tracking
                if len(person_centers_x) > 0 and params['tracking_enabled']:
                    # target x: midpoint of all detected people
                    target_x = sum(person_centers_x) / len(person_centers_x)
                    
                    # target y: average chest position
                    target_y = sum(person_centers_y) / len(person_centers_y)
                    
                    # draw target point
                    cv2.circle(frame, (int(target_x), int(target_y)), 12, (0, 255, 255), 2)
                    
                    # get current offset from target point [-1, 1]
                    offset_x = (target_x - frame_center_x) / frame_center_x
                    offset_y = (target_y - frame_center_y) / frame_center_y
                    
                    # panning
                    if abs(offset_x) > params['deadzone']:
                        pan_pulse += offset_x * params['pan_speed']
                        pan_pulse = max(PAN_MIN, min(PAN_MAX, pan_pulse))
                        pwm.setServoPulse(PAN_CHANNEL, pan_pulse)
                    
                    # tilting
                    if abs(offset_y) > params['deadzone']:
                        tilt_pulse -= offset_y * params['tilt_speed']
                        tilt_pulse = max(TILT_MIN, min(TILT_MAX, tilt_pulse))
                        pwm.setServoPulse(TILT_CHANNEL, tilt_pulse)

                pan_angle = (pan_pulse - PAN_MIN) / (PAN_MAX - PAN_MIN) * 180
                tilt_angle = (tilt_pulse - TILT_MIN) / (TILT_MAX - TILT_MIN) * 180
                camera_view_score = sum(person_view_scores) / len(person_view_scores) if person_view_scores else 0.0
                
                status = "ON" if params['tracking_enabled'] else "OFF"
                cv2.putText(
                    frame, f"{CAMERA_ID} | People: {people_count} | Tracking: {status}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2
                )
                cv2.putText(
                    frame, f"Pan: {pan_angle:.0f} | Tilt: {tilt_angle:.0f}", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2
                )
                cv2.putText(
                    frame, f"View Score: {camera_view_score * 100:.1f}%", (10, 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2
                )
                
                # crosshair at center of frame
                cv2.line(frame, (int(frame_center_x) - 20, int(frame_center_y)), 
                         (int(frame_center_x) + 20, int(frame_center_y)), (255, 255, 0), 1)
                cv2.line(frame, (int(frame_center_x), int(frame_center_y) - 20), 
                         (int(frame_center_x), int(frame_center_y) + 20), (255, 255, 0), 1)

            emit_frame(frame)

    finally:
        cap.release()
        pwm.setServoPulse(PAN_CHANNEL, PAN_CENTER)
        pwm.setServoPulse(TILT_CHANNEL, TILT_CENTER)
        print("Pose detection stopped.")

def emit_frame(frame):
    if sio.connected:
        _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
        frame_base64 = base64.b64encode(buffer).decode('utf-8')
        sio.emit('camera_frame', {
            'camera_id': CAMERA_ID,
            'image': frame_base64,
            'params': params
        })

if __name__ == "__main__":
    import threading
    
    argparser = argparse.ArgumentParser(description="Grappler Camera Worker")
    argparser.add_argument('--server_ip', type=str, default=None, help='Server IP address')
    argparser.add_argument('--mdns_wait', type=int, default=10, help='mDNS discovery wait time in seconds (default: 10). Only used if --server_ip is not provided.')
    args = argparser.parse_args()

    if args.server_ip:
        SERVER_IP = args.server_ip
    else:
        # discover server via mDNS
        print("Discovering server via mDNS...")
        discover_server(args.mdns_wait)
    
    if not SERVER_IP:
        print("Failed to discover server. Exiting.")
        exit(1)
    
    # start tracking in background
    tracking_thread = threading.Thread(target=tracking_loop, daemon=True)
    tracking_thread.start()
    
    # connect to server
    while True:
        try:
            print(f"Connecting to {SERVER_IP}...")
            sio.connect(SERVER_IP)
            sio.wait()
        except Exception as e:
            #TODO: Handle exception printing better
            print(f"Connection failed: {e}, retrying in 5s...")
            sleep(5)
