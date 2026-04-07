import cv2
from ultralytics import YOLO
from enum import IntEnum, auto

KEYPOINT_CONFIDENCE_THRESHOLD = 0.5

# COCO keypoint names (17)
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
    #Keypoint.LEFT_EYE: ,
    #Keypoint.RIGHT_EYE: ,
    #Keypoint.LEFT_EAR: ,
    # Keypoint.RIGHT_EAR: ,
    # Keypoint.LEFT_SHOULDER: ,
    # Keypoint.RIGHT_SHOULDER: ,
    # Keypoint.LEFT_ELBOW: ,
    # Keypoint.RIGHT_ELBOW: ,
    Keypoint.LEFT_WRIST: 0.9,
    Keypoint.RIGHT_WRIST: 0.9,
    # Keypoint.LEFT_HIP: ,
    # Keypoint.RIGHT_HIP: ,
    # Keypoint.LEFT_KNEE: ,
    # Keypoint.RIGHT_KNEE: ,
    # Keypoint.LEFT_ANKLE: ,
    # Keypoint.RIGHT_ANKLE: 
}

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

def main():
    # --- Camera setup ---
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: Could not open camera")
        return

    # Optional: request a higher capture size from the camera (may be ignored by some cams)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    # --- Window: make it resizable and set an initial size ---
    window_name = "YOLO11 Pose Detection"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 1280, 720)

    # Fullscreen mode
    #cv2.setWindowProperty(window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    print("Loading YOLO11 Pose model...")
    model = YOLO("yolo11n-pose.pt")

    print("Starting pose detection. Press 'q' to quit.")
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Error: Failed to capture frame")
                break

            # Inference
            results = model(frame, verbose=False)

            # Draw results
            for result in results:
                boxes = result.boxes
                kps_all = getattr(result.keypoints, "xy", None)  # tensor-like (N, 17, 2)
                kps_conf_all = getattr(result.keypoints, "conf", None)  # tensor-like (N, 17)

                if boxes is None or kps_all is None:
                    continue

                # Convert to CPU numpy if needed
                if hasattr(kps_all, "cpu"):
                    kps_all_np = kps_all.cpu().numpy()
                else:
                    kps_all_np = kps_all

                # Convert keypoint confidences to numpy if needed
                if kps_conf_all is not None:
                    if hasattr(kps_conf_all, "cpu"):
                        kps_conf_all_np = kps_conf_all.cpu().numpy()
                    else:
                        kps_conf_all_np = kps_conf_all
                else:
                    kps_conf_all_np = None


                people_count = 0
                total_score = 0
                for i, box in enumerate(boxes):
                    class_id = int(box.cls[0])
                    # Only 'person' (COCO class 0)
                    if class_id != 0:
                        continue

                    people_count += 1

                    # --- Bounding box + label ---
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    conf = float(box.conf[0])
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(
                        frame, f"Fighter {conf:.2f}", (x1, y1 - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2
                    )

                    # --- Keypoints + labels + skeleton ---
                    if i >= len(kps_all_np):
                        continue
                    kps_xy = kps_all_np[i]  # (17, 2)
                    kps_conf = kps_conf_all_np[i] if kps_conf_all_np is not None else None  # (17,)

                    # Draw keypoints (red) and their names (yellow)
                    for j, (kx, ky) in enumerate(kps_xy):
                        x, y = int(kx), int(ky)
                        score = float(kps_conf[j]) if kps_conf is not None else 1.0
                        if j < len(Keypoint):
                            if score >= KEYPOINT_CONFIDENCE_THRESHOLD:
                                cv2.circle(frame, (x, y), 4, (0, 0, 255), -1)
                                cv2.putText(
                                    frame, Keypoint(j).name, (x + 5, y - 5),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1
                                )
                                cv2.putText(
                                    frame, f"{score:.2f}", (x + 5, y + 15),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1
                                )

                                total_score += KEYPOINT_SCORES.get(Keypoint(j), 0)

                    # Draw skeleton connections (blue)
                    for a, b in SKELETON:
                        if a < len(kps_xy) and b < len(kps_xy) and (kps_conf is None or (kps_conf[a] >= KEYPOINT_CONFIDENCE_THRESHOLD and kps_conf[b] >= KEYPOINT_CONFIDENCE_THRESHOLD)):
                            xA, yA = map(int, kps_xy[a])
                            xB, yB = map(int, kps_xy[b])
                            cv2.line(frame, (xA, yA), (xB, yB), (255, 0, 0), 2)

                # Display fighters count
                cv2.putText(
                    frame, f"Fighters detected: {people_count}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2
                )

                # Display total keypoint score
                cv2.putText(
                    frame, f"Total Keypoint Score: {total_score:.2f}", (10, 70),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2
                )

            # Show frame (window is resizable)
            cv2.imshow(window_name, frame)

            # Quit on 'q'
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print("Pose detection stopped.")

if __name__ == "__main__":
    main()
