import cv2
from ultralytics import YOLO
import numpy as np
from PCA9685 import PCA9685
import socketio
import base64
from time import sleep

# Configuration
CAMERA_ID = "camera1"
SERVER_IP = "http://172.20.10.11:10000"

# Servo stuff
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

#TESTING: center servos at the start
#pwm.setServoPulse(PAN_CHANNEL, pan_pulse)
#pwm.setServoPulse(TILT_CHANNEL, tilt_pulse)

# Tracking parameters
params = {
    'pan_speed': 40,
    'tilt_speed': 30,
    'deadzone': 0.1,
    'confidence_threshold': 0.80,
    'tracking_enabled': False
}

# Keypoint indices (COCO format)
NOSE = 0
LEFT_SHOULDER = 5
RIGHT_SHOULDER = 6

SKELETON = [
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16),
    (0, 1), (0, 2), (1, 3), (2, 4),
    (0, 5), (0, 6)
]

# Socket.IO client
sio = socketio.Client(reconnection=True, reconnection_attempts=0, reconnection_delay=1)

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

def get_chest_position(keypoints):
    left_shoulder = keypoints[LEFT_SHOULDER]
    right_shoulder = keypoints[RIGHT_SHOULDER]
    
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

def tracking_loop():
    global pan_pulse, tilt_pulse
    
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: Could not open camera")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
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
            
            if frame_count % 2 == 0:
                results = model(frame, imgsz=320, verbose=False)
                last_results = results
            
            if last_results is None:
                emit_frame(frame)
                continue
                
            results = last_results

            for result in results:
                boxes = result.boxes
                kps_all = getattr(result.keypoints, "xy", None)

                if boxes is None or kps_all is None:
                    continue

                if hasattr(kps_all, "cpu"):
                    kps_all_np = kps_all.cpu().numpy()
                else:
                    kps_all_np = kps_all

                people_count = 0
                person_centers_x = []
                person_chests_y = []
                
                for i, box in enumerate(boxes):
                    class_id = int(box.cls[0])
                    if class_id != 0:
                        continue

                    conf = float(box.conf[0])

                    if conf < params['confidence_threshold']:
                        continue

                    people_count += 1

                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    
                    # store center x of bounding box for panning
                    person_centers_x.append((x1 + x2) / 2)
                    
                    # get chest position for tilting
                    if i < len(kps_all_np):
                        kps_xy = kps_all_np[i]
                        chest_pos = get_chest_position(kps_xy)
                        
                        if chest_pos is not None:
                            person_chests_y.append(chest_pos[1])
                            # draw chest circle for each person
                            cv2.circle(frame, (int(chest_pos[0]), int(chest_pos[1])), 8, (255, 0, 255), -1)
                        else:
                            # if chest is not in frame default to the center of the bounding box
                            # TODO: make this smarter so that it tries to find the chest or make
                            #       it dynamic/configurable
                            person_chests_y.append((y1 + y2) / 2)
                    else:
                        # Fallback to bounding box center
                        person_chests_y.append((y1 + y2) / 2)
                    
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
                    target_y = sum(person_chests_y) / len(person_chests_y)
                    
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
                
                status = "ON" if params['tracking_enabled'] else "OFF"
                cv2.putText(
                    frame, f"{CAMERA_ID} | People: {people_count} | Tracking: {status}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2
                )
                cv2.putText(
                    frame, f"Pan: {pan_angle:.0f} | Tilt: {tilt_angle:.0f}", (10, 60),
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
        _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        frame_base64 = base64.b64encode(buffer).decode('utf-8')
        sio.emit('camera_frame', {
            'camera_id': CAMERA_ID,
            'image': frame_base64,
            'params': params
        })

if __name__ == "__main__":
    import threading
    
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
            #TODO: Handle exceptions better
            print(f"Connection failed: {e}, retrying in 5s...")
            sleep(5)
