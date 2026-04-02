# 🎥 Grappler Camera System

A distributed, AI-powered multi-camera tracking system designed for combat sports (MMA, BJJ, wrestling). Each camera node runs YOLO11n-pose on a Raspberry Pi 5 to detect and follow grapplers in real time using pan/tilt servos, while a central server streams all feeds to a web dashboard with live controls.

Built as a **Senior Design project** at FAMU-FSU College of Engineering (2025–2026).

---

## System Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     Web Dashboard                        │
│          (Flask + Socket.IO + vanilla JS)                 │
│   Live feeds · Per-camera controls · Recording · Focus   │
└──────────────────┬───────────────────────────────────────┘
                   │ WebSocket (Socket.IO)
┌──────────────────┴───────────────────────────────────────┐
│                   Central Server                         │
│    Flask-SocketIO · mDNS advertisement (Zeroconf)        │
│    Frame relay · Param sync · Heartbeat monitor          │
│    Recording pipeline (ffmpeg subprocess)                │
└────┬─────────────────┬─────────────────┬─────────────────┘
     │                 │                 │
  Camera 1          Camera 2          Camera N
  (Pi 5)            (Pi 5)            (Pi 5)
  YOLO11n-pose      YOLO11n-pose      YOLO11n-pose
  PCA9685 servo     PCA9685 servo     PCA9685 servo
  mDNS discovery    mDNS discovery    mDNS discovery
```

## Features

### Worker Nodes (Raspberry Pi 5)
- **YOLO11n-pose inference** — Real-time human pose estimation at 480×360, processing every 3rd frame for performance
- **Servo tracking** — PCA9685-driven pan/tilt via I2C with configurable speed, deadzone, and min/max pulse limits
- **Chest-based targeting** — Tracks the midpoint between detected shoulder keypoints for stable center-of-mass following
- **Multi-person averaging** — When multiple people are detected, the camera targets the centroid of all subjects
- **COCO skeleton overlay** — Draws keypoints, skeleton connections, bounding boxes, and confidence scores on each frame
- **mDNS auto-discovery** — Workers discover the server automatically via Zeroconf (`_grappler._tcp.local.`) with manual IP fallback
- **Reconnection** — Automatic reconnect with infinite retry on connection loss

### Central Server
- **Frame relay** — Receives base64 JPEG frames from workers and broadcasts to all web clients via Socket.IO
- **Parameter synchronization** — Real-time two-way sync of tracking params (pan speed, tilt speed, deadzone, confidence threshold, tracking toggle)
- **Heartbeat monitoring** — Marks cameras as offline after 10 seconds of no frames; auto-stops recording on disconnect
- **Recording pipeline** — Server-side recording via ffmpeg subprocess with automatic FPS detection from frame timestamps, buffered startup, and downloadable MP4 output
- **mDNS advertisement** — Broadcasts service on the local network for zero-config worker discovery

### Web Dashboard
- **Live multi-camera grid** — Real-time JPEG feeds with per-camera FPS counter and online/offline status
- **Per-camera controls** — Sliders for pan speed, tilt speed, deadzone, and confidence threshold; toggle for tracking enable/disable
- **Global controls** — Center all servos, enable/disable all tracking in one click
- **Recording** — Start/stop recording per camera with automatic MP4 download on stop
- **Focus mode** — Expand a single camera to full-width view
- **Resizable feeds** — Drag-to-resize video panels with size persistence via localStorage
- **Remove offline cameras** — Clean up disconnected camera cards from the UI

## Tech Stack

| Component | Technology |
|---|---|
| Inference | YOLO11n-pose (Ultralytics) on Raspberry Pi 5 |
| Servo control | PCA9685 16-channel PWM driver over I2C (smbus) |
| Networking | Flask-SocketIO (server) + python-socketio (workers) |
| Discovery | Zeroconf / mDNS (`_grappler._tcp.local.`) |
| Recording | ffmpeg via subprocess (MJPEG → H.264 MP4) |
| Frontend | Vanilla JS + Socket.IO client + CSS grid |
| Camera | OpenCV VideoCapture (USB/CSI) |

## Project Structure

```
├── server/
│   ├── server.py              # Central Flask-SocketIO server
│   ├── templates/
│   │   └── index.html         # Dashboard HTML
│   └── static/
│       ├── css/style.css      # Dashboard styling
│       └── js/app.js          # Dashboard logic + Socket.IO client
├── worker/
│   ├── worker.py              # Camera node — YOLO + servo + networking
│   └── PCA9685.py             # I2C servo driver for PCA9685
└── vision/
    └── pose-detection.py      # Standalone YOLO pose detection (dev/testing)
```

## Getting Started

### Prerequisites

**Server** (any machine on the local network):
- Python 3.9+
- ffmpeg (for recording)

**Workers** (Raspberry Pi 5):
- Python 3.9+
- USB or CSI camera
- PCA9685 servo driver board connected via I2C
- Pan/tilt servo assembly

### Server Setup

```bash
pip install flask flask-socketio zeroconf
python server/server.py --port 10000 --mdns
```

### Worker Setup (on each Pi)

```bash
pip install ultralytics opencv-python python-socketio zeroconf smbus
python worker/worker.py
```

The worker will auto-discover the server via mDNS. To specify the server manually:

```bash
python worker/worker.py --server_ip http://192.168.1.100:10000
```

### Access the Dashboard

Open `http://<server-ip>:10000` in a browser on any device on the same network.

## Configuration

Worker tracking parameters can be adjusted in real time from the web dashboard:

| Parameter | Default | Range | Description |
|---|---|---|---|
| Pan Speed | 40 | 5–100 | Horizontal servo responsiveness |
| Tilt Speed | 30 | 5–100 | Vertical servo responsiveness |
| Deadzone | 0.10 | 0–0.50 | Fraction of frame center to ignore (prevents jitter) |
| Confidence | 0.80 | 0.10–1.00 | Minimum YOLO detection confidence to track |
| Tracking | Off | On/Off | Enable/disable servo tracking per camera |

## License

This project was developed for academic purposes as a senior design capstone. Feel free to reference or fork for learning.
