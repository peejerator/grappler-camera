import cv2
from ultralytics import YOLO

# COCO keypoint names (17)
KEYPOINT_NAMES = [
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle"
]

# COCO skeleton connections (pairs of indices)
SKELETON = [
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),  # arms
    (5, 11), (6, 12), (11, 12),               # torso
    (11, 13), (13, 15), (12, 14), (14, 16),   # legs
    (0, 1), (0, 2), (1, 3), (2, 4),           # head
    (0, 5), (0, 6)                            # neck to shoulders
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

                if boxes is None or kps_all is None:
                    continue

                # Convert to CPU numpy if needed
                if hasattr(kps_all, "cpu"):
                    kps_all_np = kps_all.cpu().numpy()
                else:
                    kps_all_np = kps_all


                people_count = 0
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
                        frame, f"Person {conf:.2f}", (x1, y1 - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2
                    )

                    # --- Keypoints + labels + skeleton ---
                    if i >= len(kps_all_np):
                        continue
                    kps_xy = kps_all_np[i]  # (17, 2)

                    # Draw keypoints (red) and their names (yellow)
                    for j, (kx, ky) in enumerate(kps_xy):
                        x, y = int(kx), int(ky)
                        cv2.circle(frame, (x, y), 4, (0, 0, 255), -1)
                        if j < len(KEYPOINT_NAMES):
                            cv2.putText(
                                frame, KEYPOINT_NAMES[j], (x + 5, y - 5),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1
                            )

                    # Draw skeleton connections (blue)
                    for a, b in SKELETON:
                        if a < len(kps_xy) and b < len(kps_xy):
                            xA, yA = map(int, kps_xy[a])
                            xB, yB = map(int, kps_xy[b])
                            cv2.line(frame, (xA, yA), (xB, yB), (255, 0, 0), 2)

                # Display people count
                cv2.putText(
                    frame, f"People detected: {people_count}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2
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
