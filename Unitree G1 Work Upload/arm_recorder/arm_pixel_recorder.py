#!/usr/bin/env python3
"""arm_pixel_recorder.py — record arm samples targeted by camera pixel.

Instead of being told a direction (arm_train_recorder.py), you click a point on
the live camera frame to set the target, then physically move the arm to point
the gripper there.  The saved CSV feeds data/train_er2.py, whose MLP maps

    (pixel_y, pixel_x, start_arm_joints) -> end_arm_joints

Pixel coordinates follow the ER 2 convention: 0-1000 normalized, [y, x],
origin top-left.  Switching to real ER 2 output later is therefore zero-change.

Workflow
========
1. Select arm (left / right).
2. Look at the camera panel — the live frame refreshes every 2 s.
3. Click the target object in the frame — a green crosshair marks the pixel.
4. Position the arm at home (or any start pose you choose).
5. Press Record — the start joints are snapped, a 2-second window opens.
6. Move the arm so the gripper points at the crosshair target.
7. End of the window: quality score flashes.  Press 1-5 to override, or wait.
8. Sample is saved.  Repeat from step 2.

Camera sources (pick one; env vars override defaults)
    --frame FILE     use a saved JPEG (no robot needed — good for testing)
    --rtsp camera    grab from ARNIE's /camera RTSP mount (needs ffmpeg)
    --rtsp webcam    grab from /webcam RTSP mount
    --webcam         grab from local V4L2 device

Usage
=====
    python3 arm_pixel_recorder.py --arm right --iface enxa0cec8b8657b
    python3 arm_pixel_recorder.py --arm right --frame path/to/frame.jpg
    python3 arm_pixel_recorder.py --arm right --rtsp camera --iface enxa0cec8b8657b
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

# --------------------------------------------------------------------------- #
#  LiDAR depth reader (optional — gracefully disabled if rclpy not available)
# --------------------------------------------------------------------------- #
_LIDAR_TOPIC  = "/g1/lio/points"
_CAMINFO_TOPIC = "/g1/camera/camera_info"
_LIDAR_DOMAIN  = 42
_LIDAR_PIXEL_RADIUS = 20  # px: nearest projected point within this radius counts


class LidarDepthReader:
    """Background rclpy subscriber that projects LiDAR into camera frame.

    Runs a minimal rclpy spin in a daemon thread so it does not block Qt.
    Call ``depth_at_pixel(py, px)`` after a click to get the nearest depth_m.

    Gracefully disabled (``ok=False``) when rclpy or sensor_msgs are missing.
    """

    def __init__(self, domain: int = _LIDAR_DOMAIN):
        self.ok = False
        self._points: np.ndarray | None = None   # (N, 3) in lidar_link frame
        self._K: np.ndarray | None = None        # 3x3 camera intrinsics
        self._lock = threading.Lock()
        self._node = None

        # Inject ROS2 Humble Python paths if rclpy isn't already importable.
        # This lets --lidar work without `source /opt/ros/humble/setup.bash`.
        try:
            import rclpy  # type: ignore
        except ImportError:
            for _p in (
                "/opt/ros/humble/local/lib/python3.10/dist-packages",
                "/opt/ros/humble/lib/python3.10/site-packages",
            ):
                if _p not in sys.path:
                    sys.path.insert(0, _p)
        try:
            import rclpy  # type: ignore
            from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, HistoryPolicy  # type: ignore
            from sensor_msgs.msg import PointCloud2, CameraInfo  # type: ignore
        except ImportError as exc:
            print(f"[lidar] rclpy/sensor_msgs unavailable: {exc}", file=sys.stderr)
            return

        try:
            rclpy.init(args=None, domain_id=domain)
            self._rclpy = rclpy
        except Exception as exc:
            print(f"[lidar] rclpy.init failed: {exc}", file=sys.stderr)
            return

        try:
            node = rclpy.create_node("arm_pixel_lidar")
            qos = QoSProfile(
                reliability=ReliabilityPolicy.BEST_EFFORT,
                durability=DurabilityPolicy.VOLATILE,
                history=HistoryPolicy.KEEP_LAST,
                depth=10,
            )
            node.create_subscription(
                PointCloud2, _LIDAR_TOPIC, self._on_cloud, qos
            )
            node.create_subscription(
                CameraInfo, _CAMINFO_TOPIC, self._on_caminfo, qos
            )
            self._node = node
        except Exception as exc:
            print(f"[lidar] node creation failed: {exc}", file=sys.stderr)
            return

        def _spin():
            try:
                rclpy.spin(self._node)
            except Exception:
                pass

        t = threading.Thread(target=_spin, daemon=True)
        t.start()
        self.ok = True
        print(f"[lidar] subscribed to {_LIDAR_TOPIC} on domain {domain}")

    def _on_cloud(self, msg) -> None:
        if len(msg.data) == 0:
            return
        arr = np.frombuffer(msg.data, dtype=np.float32).reshape(-1, msg.point_step // 4)
        pts = arr[:, :3].copy()
        with self._lock:
            self._points = pts

    def _on_caminfo(self, msg) -> None:
        K = np.asarray(msg.k, dtype=float).reshape(3, 3)
        with self._lock:
            self._K = K

    def depth_at_pixel(self, py_er2: float, px_er2: float,
                       img_w: int = 424, img_h: int = 240) -> float | None:
        """Return metric depth (m) at an ER2 pixel, or None if unavailable.

        ``py_er2`` / ``px_er2`` are in 0-1000 ER2 coords.  We convert to pixel
        coords, project the latest LiDAR cloud into the image plane (using the K
        matrix from /g1/camera/camera_info), and return the depth of the closest
        projected point within ``_LIDAR_PIXEL_RADIUS`` pixels.

        The LiDAR-to-camera extrinsic is approximated as identity (both sensors
        are on the robot's head, <5 cm apart).  For training purposes this is
        accurate enough; a proper TF lookup would improve precision.
        """
        with self._lock:
            pts = self._points
            K   = self._K

        if pts is None or K is None or pts.shape[0] == 0:
            return None

        # ER2 coords → pixel coords
        u_target = px_er2 / 1000.0 * img_w
        v_target = py_er2 / 1000.0 * img_h

        # Keep only points in front of the camera (positive Z)
        fwd = pts[:, 2]
        mask = fwd > 0.05
        if not np.any(mask):
            return None
        pts_f = pts[mask]

        # Project: [u, v, 1] = K @ [X/Z, Y/Z, 1]
        X, Y, Z = pts_f[:, 0], pts_f[:, 1], pts_f[:, 2]
        u_proj = K[0, 0] * (X / Z) + K[0, 2]
        v_proj = K[1, 1] * (Y / Z) + K[1, 2]

        # Find nearest projected point to the clicked pixel
        dist2 = (u_proj - u_target) ** 2 + (v_proj - v_target) ** 2
        idx = int(np.argmin(dist2))
        if np.sqrt(dist2[idx]) > _LIDAR_PIXEL_RADIUS:
            return None
        return float(Z[idx])

# --------------------------------------------------------------------------- #
#  Joint layout — identical to arm_train_recorder.py
# --------------------------------------------------------------------------- #

LEFT = [
    (15, "L Shoulder Pitch"),
    (16, "L Shoulder Roll"),
    (17, "L Shoulder Yaw"),
    (18, "L Elbow"),
    (19, "L Wrist Roll"),
    (20, "L Wrist Pitch"),
    (21, "L Wrist Yaw"),
]
RIGHT = [
    (22, "R Shoulder Pitch"),
    (23, "R Shoulder Roll"),
    (24, "R Shoulder Yaw"),
    (25, "R Elbow"),
    (26, "R Wrist Roll"),
    (27, "R Wrist Pitch"),
    (28, "R Wrist Yaw"),
]

# Quality scoring — same tunables as arm_train_recorder.py
QUAL_DISP_FULL = 0.40
QUAL_STAB_ZERO = 0.08
QUAL_STAB_SAMPLES = 16
W_DISP, W_STAB = 0.6, 0.4
RATING_SECS = 1.5

# Camera / RTSP config (mirrors er2_probe.py env var names)
RTSP_BASE = os.environ.get("RTSP_BASE", "rtsp://127.0.0.1:8554")
RTSP_MOUNTS = {"camera": "/camera", "webcam": "/webcam"}
CAMERA_DEVICE = os.environ.get("CAMERA_DEVICE", "/dev/video0")
CAMERA_REFRESH_SECS = 2.0

YELLOW = "#f4c430"
GREEN  = "#5fd35f"
RED    = "#e05a5a"
DIM    = "#7a7a7a"

# --------------------------------------------------------------------------- #
#  Qt import
# --------------------------------------------------------------------------- #
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402


# --------------------------------------------------------------------------- #
#  Text-to-speech
# --------------------------------------------------------------------------- #
def speak(text: str) -> None:
    for cmd in (["spd-say", "-t", "female1", text], ["espeak-ng", text], ["espeak", text]):
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except FileNotFoundError:
            continue


# --------------------------------------------------------------------------- #
#  DDS LowState reader — identical to arm_train_recorder.py
# --------------------------------------------------------------------------- #
class LowStateReader:
    def __init__(self, iface: str, domain: int = 0):
        self.joint_cur: dict[int, float] = {}
        self.msg_count = 0
        self.ok = False
        self._sub = None
        try:
            from unitree_sdk2py.core.channel import (  # type: ignore
                ChannelFactoryInitialize, ChannelSubscriber,
            )
        except Exception as exc:
            print(f"[recorder] unitree_sdk2py unavailable: {exc}", file=sys.stderr)
            return
        try:
            ChannelFactoryInitialize(domain, iface)
        except Exception as exc:
            print(f"[recorder] DDS init failed ({exc})", file=sys.stderr)
            return
        all_idx = [i for i, _ in LEFT + RIGHT]
        def _cb(msg):
            self.msg_count += 1
            for j in all_idx:
                try:
                    self.joint_cur[j] = float(msg.motor_state[j].q)
                except Exception:
                    pass
        for dotted in (
            "unitree_sdk2py.idl.unitree_hg.msg.dds_.LowState_",
            "unitree_sdk2py.idl.unitree_go.msg.dds_.LowState_",
        ):
            try:
                modp, cls = dotted.rsplit(".", 1)
                mod = __import__(modp, fromlist=[cls])
                sub = ChannelSubscriber("rt/lowstate", getattr(mod, cls))
                sub.Init(_cb, 10)
                self._sub = sub
                self.ok = True
                print(f"[recorder] subscribed to rt/lowstate via {cls}")
                return
            except Exception:
                continue
        print("[recorder] could not subscribe to rt/lowstate", file=sys.stderr)

    def snapshot(self, indices: list[int]) -> list[float]:
        return [self.joint_cur.get(i, 0.0) for i in indices]


# --------------------------------------------------------------------------- #
#  Waist locker — identical to arm_train_recorder.py
# --------------------------------------------------------------------------- #
ARM_INDICES  = [i for i, _ in LEFT + RIGHT]
WAIST_YAW_IDX = 12

class WaistLocker:
    def __init__(self):
        self.ok = False
        self._pub = self._cmd = self._crc = None
        try:
            from unitree_sdk2py.core.channel import ChannelPublisher  # type: ignore
            from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_  # type: ignore
            from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowCmd_  # type: ignore
            from unitree_sdk2py.utils.crc import CRC  # type: ignore
            self._pub = ChannelPublisher("rt/arm_sdk", LowCmd_)
            self._pub.Init()
            self._cmd = unitree_hg_msg_dds__LowCmd_()
            self._crc = CRC()
            self._cmd.motor_cmd[29].q = 1.0
            w = self._cmd.motor_cmd[WAIST_YAW_IDX]
            w.q, w.dq, w.tau, w.kp, w.kd = 0.0, 0.0, 0.0, 60.0, 1.5
            for i in ARM_INDICES:
                mc = self._cmd.motor_cmd[i]
                mc.q, mc.dq, mc.tau, mc.kp, mc.kd = 0.0, 0.0, 0.0, 0.0, 0.0
            self.ok = True
            print("[recorder] waist-locker ready")
        except Exception as exc:
            print(f"[recorder] waist-locker init failed: {exc}", file=sys.stderr)

    def publish(self):
        if not self.ok:
            return
        self._cmd.crc = self._crc.Crc(self._cmd)
        self._pub.Write(self._cmd)


# --------------------------------------------------------------------------- #
#  Camera capture
# --------------------------------------------------------------------------- #
def _grab_ffmpeg_rtsp(mount: str) -> bytes | None:
    url = RTSP_BASE + mount
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        proc = subprocess.run(
            ["ffmpeg", "-rtsp_transport", "tcp", "-i", url,
             "-frames:v", "1", "-f", "image2", tmp_path, "-y"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        )
        if proc.returncode == 0:
            data = Path(tmp_path).read_bytes()
            return data if data else None
    except Exception:
        pass
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return None


def _grab_ffmpeg_v4l2() -> bytes | None:
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        proc = subprocess.run(
            ["ffmpeg", "-f", "v4l2", "-i", CAMERA_DEVICE,
             "-frames:v", "1", "-f", "image2", tmp_path, "-y"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        )
        if proc.returncode == 0:
            data = Path(tmp_path).read_bytes()
            return data if data else None
    except Exception:
        pass
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return None


# --------------------------------------------------------------------------- #
#  Clickable camera label
# --------------------------------------------------------------------------- #
class CameraLabel(QtWidgets.QLabel):
    """QLabel that emits a (pixel_y, pixel_x) signal in 0-1000 ER 2 coords on click."""

    pixel_clicked = QtCore.Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setMinimumSize(424, 240)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )
        self._raw_pixmap: QtGui.QPixmap | None = None
        self._target: tuple[float, float] | None = None  # (y, x) in 0-1000
        self._show_placeholder()

    def _show_placeholder(self):
        pm = QtGui.QPixmap(424, 240)
        pm.fill(QtGui.QColor("#1a1a1a"))
        p = QtGui.QPainter(pm)
        p.setPen(QtGui.QColor(DIM))
        p.setFont(QtGui.QFont("monospace", 14))
        p.drawText(pm.rect(), QtCore.Qt.AlignCenter, "No camera frame\nClick Refresh")
        p.end()
        self._raw_pixmap = pm
        self._redraw()

    def set_jpeg(self, data: bytes):
        pm = QtGui.QPixmap()
        pm.loadFromData(data)
        if pm.isNull():
            return
        self._raw_pixmap = pm
        self._redraw()

    def set_static(self, path: str):
        pm = QtGui.QPixmap(path)
        if pm.isNull():
            return
        self._raw_pixmap = pm
        self._redraw()

    def set_target(self, y: float, x: float):
        self._target = (y, x)
        self._redraw()

    def clear_target(self):
        self._target = None
        self._redraw()

    def _redraw(self):
        if self._raw_pixmap is None:
            return
        pm = self._raw_pixmap.scaled(
            self.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
        )
        if self._target is not None:
            ty, tx = self._target
            px = int(tx / 1000.0 * pm.width())
            py = int(ty / 1000.0 * pm.height())
            painter = QtGui.QPainter(pm)
            pen = QtGui.QPen(QtGui.QColor(64, 255, 64), 2)
            painter.setPen(pen)
            r = 12
            painter.drawLine(px - r, py, px + r, py)
            painter.drawLine(px, py - r, px, py + r)
            painter.drawEllipse(px - 5, py - 5, 10, 10)
            painter.setPen(QtGui.QPen(QtGui.QColor(64, 255, 64), 1))
            painter.setFont(QtGui.QFont("monospace", 10))
            painter.drawText(px + 14, py - 8,
                             f"[{ty:.0f}, {tx:.0f}]")
            painter.end()
        self.setPixmap(pm)

    def mousePressEvent(self, ev: QtGui.QMouseEvent):
        if self._raw_pixmap is None or ev.button() != QtCore.Qt.LeftButton:
            return super().mousePressEvent(ev)

        # Map click within the label to click within the scaled pixmap.
        pm_scaled = self._raw_pixmap.scaled(
            self.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
        )
        # Offset of the pixmap inside the label (centred).
        off_x = (self.width()  - pm_scaled.width())  // 2
        off_y = (self.height() - pm_scaled.height()) // 2
        cx = ev.position().x() - off_x
        cy = ev.position().y() - off_y
        if cx < 0 or cy < 0 or cx > pm_scaled.width() or cy > pm_scaled.height():
            return
        px = cx / pm_scaled.width()  * 1000.0
        py = cy / pm_scaled.height() * 1000.0
        self.pixel_clicked.emit(py, px)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._redraw()


# --------------------------------------------------------------------------- #
#  Top-down LiDAR view widget
# --------------------------------------------------------------------------- #
_LIDAR_VIEW_RANGE = 4.0   # metres shown each side of origin (8m × 8m total)


class LidarView(QtWidgets.QWidget):
    """Top-down (X-Y plane) scatter of the latest LiDAR point cloud.

    Robot is at the centre.  X axis points forward (right on screen),
    Y axis points left (up on screen).  The colour encodes depth (Z):
    blue = low, green = mid, red = high — matching the dashboard colormap.

    When a target depth is set via ``set_target_depth``, a labelled ring is
    drawn at that radius from the origin on the forward (X) axis to give a
    visual depth reference.
    """

    def __init__(self, lidar: "LidarDepthReader | None", parent=None):
        super().__init__(parent)
        self._lidar = lidar
        self._target_depth: float | None = None
        self.setMinimumSize(300, 200)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )
        self.setToolTip("Top-down LiDAR view  (X forward, Y left)")

    def set_target_depth(self, depth_m: float | None):
        self._target_depth = depth_m
        self.update()

    # Keep the widget square as the window resizes.
    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return w

    def paintEvent(self, _ev):
        W, H = self.width(), self.height()
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)

        # Background
        p.fillRect(0, 0, W, H, QtGui.QColor("#0d0d0d"))

        # Grid lines
        grid_pen = QtGui.QPen(QtGui.QColor("#2a2a2a"), 1)
        p.setPen(grid_pen)
        scale = min(W, H) / 2.0 / _LIDAR_VIEW_RANGE
        cx, cy = W // 2, H // 2
        for d in (1, 2, 3):
            r = int(d * scale)
            p.drawEllipse(cx - r, cy - r, 2 * r, 2 * r)
        p.drawLine(0, cy, W, cy)   # X axis
        p.drawLine(cx, 0, cx, H)   # Y axis

        # Axis labels
        p.setPen(QtGui.QColor(DIM))
        p.setFont(QtGui.QFont("monospace", 9))
        p.drawText(W - 24, cy - 4, "fwd")
        p.drawText(cx + 4, 12, "left")

        # No data
        has_lidar = self._lidar is not None and self._lidar.ok
        if not has_lidar:
            p.setPen(QtGui.QColor(DIM))
            p.setFont(QtGui.QFont("monospace", 11))
            p.drawText(self.rect(), QtCore.Qt.AlignCenter,
                       "No LiDAR\n(pass --lidar to enable)")
            p.end()
            return

        import threading as _t
        with self._lidar._lock:
            pts = self._lidar._points

        if pts is None or pts.shape[0] == 0:
            p.setPen(QtGui.QColor(DIM))
            p.setFont(QtGui.QFont("monospace", 11))
            p.drawText(self.rect(), QtCore.Qt.AlignCenter,
                       "Waiting for /g1/lio/points…")
            p.end()
            return

        # Subsample for speed (max 4000 points drawn)
        if pts.shape[0] > 4000:
            step = pts.shape[0] // 4000
            pts = pts[::step]

        X, Y, Z = pts[:, 0], pts[:, 1], pts[:, 2]
        z_min, z_max = float(Z.min()), float(Z.max())
        z_range = max(z_max - z_min, 0.01)
        t_arr = (Z - z_min) / z_range

        # Draw as 2×2 pixel rectangles
        for i in range(len(X)):
            # Top-down: robot at centre, X→right, Y→up
            sx = int(cx + Y[i] * scale)   # Y-left becomes screen-right
            sy = int(cy - X[i] * scale)   # X-forward becomes screen-up
            if sx < 0 or sx >= W or sy < 0 or sy >= H:
                continue
            t = float(t_arr[i])
            r_c = int(max(0, min(255, (2 * t - 1) * 255)))
            g_c = int(max(0, min(255, (1 - abs(2 * t - 1)) * 255)))
            b_c = int(max(0, min(255, (1 - 2 * t) * 255)))
            p.fillRect(sx, sy, 2, 2, QtGui.QColor(r_c, g_c, b_c))

        # Robot origin dot
        p.fillRect(cx - 3, cy - 3, 6, 6, QtGui.QColor(255, 220, 0))

        # Target depth ring
        if self._target_depth is not None and self._target_depth > 0:
            dr = int(self._target_depth * scale)
            ring_pen = QtGui.QPen(QtGui.QColor(64, 255, 64), 1, QtCore.Qt.DashLine)
            p.setPen(ring_pen)
            p.drawEllipse(cx - dr, cy - dr, 2 * dr, 2 * dr)
            p.setPen(QtGui.QColor(64, 255, 64))
            p.setFont(QtGui.QFont("monospace", 9))
            p.drawText(cx + dr + 3, cy - 3, f"{self._target_depth:.2f}m")

        p.end()


# --------------------------------------------------------------------------- #
#  Main window
# --------------------------------------------------------------------------- #
class PixelRecorder(QtWidgets.QMainWindow):
    def __init__(
        self,
        reader: LowStateReader,
        locker: WaistLocker,
        arm: str,
        source_mode: str,      # "frame" | "rtsp" | "webcam"
        source_arg: str | None,  # path for "frame", mount for "rtsp", None for "webcam"
        lidar: LidarDepthReader | None = None,
    ):
        super().__init__()
        self.reader = reader
        self.locker = locker
        self.arm = arm
        self.source_mode = source_mode
        self.source_arg = source_arg
        self.lidar = lidar

        self.setWindowTitle("ER2 Pixel Recorder")
        self.resize(1600, 900)

        # Recording state
        self.recording = False
        self.phase = "idle"
        self.phase_started = 0.0
        self.sample_idx = 0
        self.total_samples = 30
        self.window_secs = 2.0
        self.target_pixel: tuple[float, float] | None = None  # (y, x) 0-1000
        self.target_depth_m: float | None = None
        self.start_pose: list[float] | None = None
        self.csv_path: Path | None = None
        self.session_id: str = ""
        self._window_buf: list[list[float]] = []
        self._pending: dict | None = None
        self._session_qualities: list[float] = []
        self.n_saved = 0

        self._build_ui()
        self._apply_theme()
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        # Camera refresh (background thread → signal back to UI)
        self._cam_lock = threading.Lock()
        self._cam_bytes: bytes | None = None
        self._cam_dirty = False
        if source_mode == "frame" and source_arg:
            self.camera_view.set_static(source_arg)
        else:
            self._start_camera_thread()

        # Timers
        self._mon_timer = QtCore.QTimer(self)
        self._mon_timer.timeout.connect(self._refresh_monitor)
        self._mon_timer.start(50)

        self._sm_timer = QtCore.QTimer(self)
        self._sm_timer.timeout.connect(self._tick)
        self._sm_timer.start(30)

        self._cam_timer = QtCore.QTimer(self)
        self._cam_timer.timeout.connect(self._flush_camera)
        self._cam_timer.start(100)

        # LiDAR view refresh at ~5 Hz (matches /g1/lio/points publish rate)
        self._lidar_timer = QtCore.QTimer(self)
        self._lidar_timer.timeout.connect(self.lidar_view.update)
        self._lidar_timer.start(200)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # Layout (top-down):
        #
        #   ┌──────────────────┬──────────────┬──────────────────┐
        #   │                  │  LiDAR (sq)  │  Action / READY  │
        #   │    Camera        │              │  Progress bar    │
        #   │  (clickable)     ├──────────────┤  Controls        │
        #   │                  │  pixel label │  Stats           │
        #   │                  │  + buttons   │  Log (small)     │
        #   ├──────────────────┴──────────────┴──────────────────┤
        #   │          Arm Joints (compact strip)                 │
        #   └─────────────────────────────────────────────────────┘
        #
        # Column stretch:  camera=5  lidar=3  controls=2

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(8)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(10)

        # ------------------------------------------------------------------ #
        # Column 1 — Camera
        # ------------------------------------------------------------------ #
        cam_box = QtWidgets.QGroupBox("Camera — click to set target pixel")
        cam_lay = QtWidgets.QVBoxLayout(cam_box)
        self.camera_view = CameraLabel()
        self.camera_view.pixel_clicked.connect(self._on_pixel_clicked)
        cam_lay.addWidget(self.camera_view, 1)

        cam_ctrl = QtWidgets.QHBoxLayout()
        self.pixel_lbl = QtWidgets.QLabel("Target: not set")
        self.pixel_lbl.setStyleSheet("font-family: monospace; font-size: 12px;")
        cam_ctrl.addWidget(self.pixel_lbl, 1)
        refresh_btn = QtWidgets.QPushButton("↺")
        refresh_btn.setFixedWidth(36)
        refresh_btn.setToolTip("Refresh camera frame")
        refresh_btn.clicked.connect(self._manual_refresh)
        cam_ctrl.addWidget(refresh_btn)
        clear_btn = QtWidgets.QPushButton("✕ Clear")
        clear_btn.clicked.connect(self._clear_target)
        cam_ctrl.addWidget(clear_btn)
        cam_lay.addLayout(cam_ctrl)

        top.addWidget(cam_box, 5)

        # ------------------------------------------------------------------ #
        # Column 2 — LiDAR (square)
        # ------------------------------------------------------------------ #
        lidar_col = QtWidgets.QVBoxLayout()
        lidar_col.setSpacing(6)

        lidar_box = QtWidgets.QGroupBox("LiDAR — top-down (X fwd, Y left)")
        lidar_box_lay = QtWidgets.QVBoxLayout(lidar_box)
        lidar_box_lay.setContentsMargins(4, 4, 4, 4)
        self.lidar_view = LidarView(self.lidar)
        # Square policy: let heightForWidth() drive the height.
        self.lidar_view.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )
        lidar_box_lay.addWidget(self.lidar_view)
        lidar_col.addWidget(lidar_box, 1)
        lidar_col.addStretch(0)

        top.addLayout(lidar_col, 3)

        # ------------------------------------------------------------------ #
        # Column 3 — Controls + Log
        # ------------------------------------------------------------------ #
        right = QtWidgets.QVBoxLayout()
        right.setSpacing(8)

        self.action_label = QtWidgets.QLabel("READY")
        f = QtGui.QFont(); f.setPointSize(34); f.setBold(True)
        self.action_label.setFont(f)
        self.action_label.setAlignment(QtCore.Qt.AlignCenter)
        self.action_label.setMinimumHeight(80)
        right.addWidget(self.action_label)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10)
        right.addWidget(self.progress)

        # Controls row
        ctrl = QtWidgets.QHBoxLayout()
        ctrl.addWidget(QtWidgets.QLabel("Arm:"))
        self.arm_combo = QtWidgets.QComboBox()
        self.arm_combo.addItems(["left", "right"])
        self.arm_combo.setCurrentText(self.arm)
        self.arm_combo.currentTextChanged.connect(self._on_arm_changed)
        ctrl.addWidget(self.arm_combo)
        ctrl.addWidget(QtWidgets.QLabel("Samples:"))
        self.samples_spin = QtWidgets.QSpinBox()
        self.samples_spin.setRange(1, 500)
        self.samples_spin.setValue(30)
        ctrl.addWidget(self.samples_spin)
        ctrl.addWidget(QtWidgets.QLabel("Sec:"))
        self.secs_spin = QtWidgets.QDoubleSpinBox()
        self.secs_spin.setRange(0.5, 10.0)
        self.secs_spin.setSingleStep(0.5)
        self.secs_spin.setValue(2.0)
        ctrl.addWidget(self.secs_spin)
        right.addLayout(ctrl)

        self.damp_btn = QtWidgets.QPushButton("⏸ Damp OFF")
        self.damp_btn.setObjectName("dampBtn")
        self.damp_btn.setCheckable(True)
        self.damp_btn.setChecked(True)
        self.damp_btn.clicked.connect(self._toggle_damp)
        right.addWidget(self.damp_btn)

        self.rec_btn = QtWidgets.QPushButton("● Record Sample")
        self.rec_btn.setObjectName("recBtn")
        self.rec_btn.clicked.connect(self._record_sample)
        right.addWidget(self.rec_btn)

        self.stop_btn = QtWidgets.QPushButton("■  Stop Session")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_session)
        right.addWidget(self.stop_btn)

        stats_box = QtWidgets.QGroupBox("Session")
        sg = QtWidgets.QHBoxLayout(stats_box)
        self._n_saved_lbl = QtWidgets.QLabel("Saved: 0")
        self._n_saved_lbl.setStyleSheet("font-family: monospace;")
        sg.addWidget(self._n_saved_lbl)
        sg.addStretch(1)
        self._file_lbl = QtWidgets.QLabel("No file yet")
        self._file_lbl.setStyleSheet(f"font-family: monospace; color: {DIM}; font-size: 10px;")
        sg.addWidget(self._file_lbl)
        right.addWidget(stats_box)

        log_box = QtWidgets.QGroupBox("Log")
        lv = QtWidgets.QVBoxLayout(log_box)
        lv.setContentsMargins(4, 4, 4, 4)
        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("font-family: monospace; font-size: 10px;")
        self.log_view.setMaximumBlockCount(200)
        lv.addWidget(self.log_view)
        right.addWidget(log_box, 1)

        top.addLayout(right, 2)

        outer.addLayout(top, 8)

        # ------------------------------------------------------------------ #
        # Bottom strip — Arm Joints (full name + value, two columns)
        # ------------------------------------------------------------------ #
        mon_box = QtWidgets.QGroupBox("Arm Joints")
        mon = QtWidgets.QHBoxLayout(mon_box)
        self._joint_labels: dict[int, QtWidgets.QLabel] = {}
        for title, joints in (("Left", LEFT), ("Right", RIGHT)):
            col = QtWidgets.QVBoxLayout()
            hdr = QtWidgets.QLabel(title)
            hdr.setFont(QtGui.QFont("", 12, QtGui.QFont.Bold))
            col.addWidget(hdr)
            for idx, name in joints:
                row = QtWidgets.QHBoxLayout()
                num = QtWidgets.QLabel(f"{idx}")
                num.setStyleSheet(f"color: {YELLOW}; font-weight: bold;")
                num.setFixedWidth(24)
                lbl = QtWidgets.QLabel(name)
                lbl.setMinimumWidth(130)
                val = QtWidgets.QLabel("+0.000")
                val.setStyleSheet("font-family: monospace;")
                val.setAlignment(QtCore.Qt.AlignRight)
                self._joint_labels[idx] = val
                row.addWidget(num)
                row.addWidget(lbl)
                row.addStretch(1)
                row.addWidget(val)
                col.addLayout(row)
            col.addStretch(1)
            mon.addLayout(col)

        outer.addWidget(mon_box, 2)

        self.status = self.statusBar()
        self._update_status()

    def _apply_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #1e1e1e; color: #e6e6e6; }
            QGroupBox {
                border: 1px solid #3a3a3a; border-radius: 6px;
                margin-top: 10px; padding-top: 8px; font-weight: bold;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QPushButton {
                background: #2d2d2d; border: 1px solid #444; border-radius: 5px;
                padding: 6px 14px; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background: #383838; }
            QPushButton:disabled { color: #666; }
            QPushButton#recBtn  { background: #2f5d2f; }
            QPushButton#recBtn:hover { background: #3a7a3a; }
            QPushButton#stopBtn { background: #5d2f2f; }
            QPushButton#stopBtn:hover { background: #7a3a3a; }
            QPushButton#dampBtn { background: #2f4a5d; }
            QPushButton#dampBtn:hover { background: #3a5f7a; }
            QPushButton#dampBtn:checked { background: #2f4a5d; }
            QPushButton#dampBtn:!checked { background: #5d4a2f; }
            QProgressBar { background: #2a2a2a; border: none; border-radius: 7px; }
            QProgressBar::chunk { background: #f4c430; border-radius: 7px; }
            QComboBox, QSpinBox, QDoubleSpinBox {
                background: #2a2a2a; border: 1px solid #444; border-radius: 4px; padding: 3px 6px;
            }
            QPlainTextEdit { background: #141414; border: 1px solid #333; }
        """)

    # ------------------------------------------------------------------ camera
    def _start_camera_thread(self):
        def _run():
            while True:
                data = self._capture_once()
                if data:
                    with self._cam_lock:
                        self._cam_bytes = data
                        self._cam_dirty = True
                time.sleep(CAMERA_REFRESH_SECS)
        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def _capture_once(self) -> bytes | None:
        if self.source_mode == "rtsp":
            mount = RTSP_MOUNTS.get(self.source_arg or "camera", "/camera")
            return _grab_ffmpeg_rtsp(mount)
        if self.source_mode == "webcam":
            return _grab_ffmpeg_v4l2()
        return None

    def _flush_camera(self):
        with self._cam_lock:
            if not self._cam_dirty or self._cam_bytes is None:
                return
            data = self._cam_bytes
            self._cam_dirty = False
        self.camera_view.set_jpeg(data)

    def _manual_refresh(self):
        data = self._capture_once()
        if data:
            self.camera_view.set_jpeg(data)
            self.log("Camera frame refreshed.")
        else:
            self.log("[warn] camera grab failed — check ffmpeg and RTSP source.")

    # ------------------------------------------------------------------ pixel
    def _on_pixel_clicked(self, py: float, px: float):
        self.target_pixel = (py, px)
        self.camera_view.set_target(py, px)

        # Try to fetch depth from LiDAR at this pixel.
        depth_str = ""
        self.target_depth_m = None
        if self.lidar is not None and self.lidar.ok:
            d = self.lidar.depth_at_pixel(py, px)
            self.target_depth_m = d
            depth_str = f"  depth={d:.2f}m" if d is not None else "  depth=n/a"

        self.pixel_lbl.setText(f"Target: [y={py:.0f}, x={px:.0f}]{depth_str}  (ER2 0-1000)")
        self.pixel_lbl.setStyleSheet(f"font-family: monospace; font-size: 13px; color: {GREEN};")
        self.log(f"Target pixel set → [y={py:.0f}, x={px:.0f}]{depth_str}")
        self.lidar_view.set_target_depth(self.target_depth_m)

    def _clear_target(self):
        self.target_pixel = None
        self.target_depth_m = None
        self.camera_view.clear_target()
        self.lidar_view.set_target_depth(None)
        self.pixel_lbl.setText("Target: not set")
        self.pixel_lbl.setStyleSheet("font-family: monospace; font-size: 13px;")

    # ------------------------------------------------------------------ recording
    def _arm_indices(self) -> list[int]:
        return [i for i, _ in (LEFT if self.arm == "left" else RIGHT)]

    def _csv_path(self) -> Path:
        return Path("data") / "arms_pixel" / self.arm / "pixel_samples.csv"

    def _record_sample(self):
        if self.recording:
            return
        if self.target_pixel is None:
            QtWidgets.QMessageBox.warning(
                self, "No target",
                "Click a point on the camera image first to set the target pixel."
            )
            return
        if not self.reader.ok:
            QtWidgets.QMessageBox.warning(
                self, "No feedback",
                "Not receiving rt/lowstate — cannot record joint angles.\n"
                "Check robot connection and --iface."
            )
            return

        if self.session_id == "":
            # First sample of session — open CSV.
            self.total_samples = self.samples_spin.value()
            self.window_secs   = self.secs_spin.value()
            self.session_id    = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._session_qualities = []
            self.n_saved = 0
            self.csv_path = self._csv_path()
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.csv_path.exists():
                idx = self._arm_indices()
                header = (
                    ["pixel_y", "pixel_x", "depth_m", "session_id", "quality"]
                    + [f"start_{i}" for i in idx]
                    + [f"end_{i}" for i in idx]
                )
                with self.csv_path.open("w", newline="") as fp:
                    csv.writer(fp).writerow(header)
            with self.csv_path.open("a", newline="") as fp:
                csv.writer(fp).writerow(
                    [f"# session {self.session_id} | arm={self.arm} | started"]
                )
            self._file_lbl.setText(str(self.csv_path))
            self.arm_combo.setEnabled(False)
            self.samples_spin.setEnabled(False)
            self.secs_spin.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.log(f"=== session {self.session_id} started ===")
            self.log(f"file: {self.csv_path}")

        # Snapshot start pose and enter the movement window.
        self.recording = True
        self.start_pose = self.reader.snapshot(self._arm_indices())
        self._window_buf = []
        self._pending = None
        self._enter_phase("window")
        self.rec_btn.setEnabled(False)

        py, px = self.target_pixel
        self.log(f"[sample {self.sample_idx + 1}] target=[y={py:.0f}, x={px:.0f}]  "
                 f"start_joints={[f'{v:.3f}' for v in self.start_pose]}")
        speak("go")

    def _stop_session(self):
        if not self.recording and self.session_id == "":
            return
        self.recording = False
        self.phase = "idle"
        self._pending = None

        n = len(self._session_qualities)
        if n > 0:
            reply = QtWidgets.QMessageBox.question(
                self, "Save session?",
                f"Save {n} sample{'s' if n != 1 else ''} from this session?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes,
            )
            if reply == QtWidgets.QMessageBox.Yes:
                self._write_session_footer()
                self.log(f"=== stopped — {n} samples saved ===")
            else:
                self._discard_session()
                self.log(f"=== stopped — session discarded ===")
        else:
            self.log("=== stopped — no samples recorded ===")

        self.session_id = ""
        self.progress.setValue(0)
        self.action_label.setText("STOPPED")
        self.action_label.setStyleSheet(f"color: {RED};")
        self.arm_combo.setEnabled(True)
        self.samples_spin.setEnabled(True)
        self.secs_spin.setEnabled(True)
        self.rec_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def _enter_phase(self, phase: str):
        self.phase = phase
        self.phase_started = time.monotonic()
        if phase == "window":
            self.progress.setValue(0)
            self._window_buf = []
            self.action_label.setStyleSheet("color: #ffffff;")
            self.action_label.setText("MOVE ARM →\nGRIPPER ON TARGET")
        elif phase == "rating":
            self.progress.setValue(1000)
            self._show_quality(self._pending["quality"])
            self.setFocus()

    def _toggle_damp(self, checked: bool):
        if checked:
            self.damp_btn.setText("⏸ Damp OFF")
            self.log("[damp] arm damp enabled — arm is freely backdrivable")
        else:
            self.damp_btn.setText("▶ Damp ON")
            self.log("[damp] arm damp DISABLED — arm held by robot controller")

    def _tick(self):
        if self.damp_btn.isChecked():
            self.locker.publish()
        if not self.recording or self.phase == "idle":
            return

        elapsed = time.monotonic() - self.phase_started

        if self.phase == "window":
            self._window_buf.append(self.reader.snapshot(self._arm_indices()))
            frac = min(1.0, elapsed / self.window_secs)
            self.progress.setValue(int(frac * 1000))
            if elapsed >= self.window_secs:
                self._finish_sample()

        elif self.phase == "rating":
            if elapsed >= RATING_SECS:
                self._commit_sample(self._pending["quality"], manual=False)

    def _finish_sample(self):
        end_pose = self.reader.snapshot(self._arm_indices())
        quality, disp, std = self._compute_quality(self.start_pose, end_pose, self._window_buf)
        self._pending = {
            "pixel":   self.target_pixel,
            "depth_m": self.target_depth_m,
            "start":   list(self.start_pose),
            "end":     list(end_pose),
            "quality": quality,
        }
        self.log(f"    quality={quality:.2f}  (moved {disp:.3f} rad, jitter {std:.3f})")
        self._enter_phase("rating")

    def _compute_quality(self, start_pose, end_pose, window_buf):
        start = np.asarray(start_pose, dtype=float)
        end   = np.asarray(end_pose,   dtype=float)
        disp  = float(np.linalg.norm(end - start))
        disp_score = min(1.0, disp / QUAL_DISP_FULL)
        std_mean = 0.0
        if window_buf:
            n = min(len(window_buf), QUAL_STAB_SAMPLES)
            tail = np.asarray(window_buf[-n:], dtype=float)
            if tail.shape[0] >= 2:
                std_mean = float(tail.std(axis=0).mean())
        stab_score = max(0.0, 1.0 - std_mean / QUAL_STAB_ZERO)
        quality = W_DISP * disp_score + W_STAB * stab_score
        return round(quality, 2), disp, std_mean

    def _show_quality(self, q: float):
        filled = int(round(q * 5))
        stars = "★" * filled + "☆" * (5 - filled)
        col = GREEN if q >= 0.6 else (YELLOW if q >= 0.3 else RED)
        self.action_label.setStyleSheet(f"color: {col};")
        self.action_label.setText(f"{stars}   {q:.2f}")

    def _commit_sample(self, quality: float, manual: bool):
        if self._pending is None:
            return
        p = self._pending
        self._pending = None
        py, px = p["pixel"]
        depth = p.get("depth_m")
        depth_str = f"{depth:.4f}" if depth is not None else ""
        row = (
            [f"{py:.2f}", f"{px:.2f}", depth_str, self.session_id, f"{quality:.2f}"]
            + [f"{v:.6f}" for v in p["start"]]
            + [f"{v:.6f}" for v in p["end"]]
        )
        with self.csv_path.open("a", newline="") as fp:
            csv.writer(fp).writerow(row)
        self._session_qualities.append(quality)
        self.n_saved += 1
        self.sample_idx += 1
        self._n_saved_lbl.setText(f"Saved: {self.n_saved}")
        tag = "manual" if manual else "auto"
        self.log(f"  ✓ saved sample {self.sample_idx}  quality={quality:.2f} ({tag})")

        self.recording = False
        self.phase = "idle"
        self.progress.setValue(0)
        self.action_label.setStyleSheet(f"color: {GREEN};")
        self.action_label.setText("DONE ✓\nClick new target or Record again")
        self.rec_btn.setEnabled(True)
        speak("saved")

    def _write_session_footer(self):
        if not self.csv_path or not self._session_qualities:
            return
        n = len(self._session_qualities)
        avg = sum(self._session_qualities) / n
        with self.csv_path.open("a", newline="") as fp:
            csv.writer(fp).writerow(
                [f"# session {self.session_id} | {n} samples | avg_quality={avg:.2f} | ended"]
            )

    def _discard_session(self):
        if not self.csv_path or not self.csv_path.exists():
            return
        sid = self.session_id
        kept = []
        try:
            with self.csv_path.open("r", newline="") as fp:
                for line in fp:
                    if sid in line:
                        continue
                    kept.append(line)
            with self.csv_path.open("w", newline="") as fp:
                fp.writelines(kept)
        except Exception as exc:
            self.log(f"[warn] could not discard session: {exc}")

    def keyPressEvent(self, ev):
        if self.recording and self.phase == "rating" and self._pending is not None:
            override = {
                QtCore.Qt.Key_1: 0.2, QtCore.Qt.Key_2: 0.4,
                QtCore.Qt.Key_3: 0.6, QtCore.Qt.Key_4: 0.8,
                QtCore.Qt.Key_5: 1.0,
            }.get(ev.key())
            if override is not None:
                self._commit_sample(override, manual=True)
                return
        super().keyPressEvent(ev)

    # ------------------------------------------------------------------ monitor
    def _refresh_monitor(self):
        for idx, lbl in self._joint_labels.items():
            v = self.reader.joint_cur.get(idx)
            lbl.setText(f"{v:+.3f}" if v is not None else "  --  ")
        if not self.recording:
            self._update_status()

    def _on_arm_changed(self, txt: str):
        if self.recording:
            return
        self.arm = txt
        self._update_status()

    def _update_status(self):
        conn = (f"lowstate: {self.reader.msg_count} msgs"
                if self.reader.ok else "lowstate: NOT CONNECTED")
        lidar_s = ""
        if self.lidar is not None:
            lidar_s = "   |   lidar: OK" if self.lidar.ok else "   |   lidar: NOT CONNECTED"
        self.status.showMessage(f"arm: {self.arm}   |   {conn}{lidar_s}")

    def log(self, msg: str):
        self.log_view.appendPlainText(msg)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def closeEvent(self, ev):
        self.recording = False
        super().closeEvent(ev)


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="ER2 pixel-targeted arm recorder")
    ap.add_argument("--iface", default="enxa0cec8b8657b")
    ap.add_argument("--domain", type=int, default=0)
    ap.add_argument("--arm", choices=["left", "right"], default="right")
    ap.add_argument(
        "--lidar", action="store_true",
        help=(
            "Subscribe to /g1/lio/points on ROS2 domain 42 and record LiDAR "
            "depth alongside each pixel sample.  Requires rclpy + sensor_msgs."
        ),
    )
    ap.add_argument(
        "--lidar-domain", type=int, default=_LIDAR_DOMAIN,
        help=f"ROS2 domain ID for the LiDAR point cloud (default {_LIDAR_DOMAIN})",
    )

    src = ap.add_mutually_exclusive_group()
    src.add_argument("--frame", metavar="FILE",
                     help="use a saved JPEG (no robot needed)")
    src.add_argument("--rtsp", choices=sorted(RTSP_MOUNTS), metavar="MOUNT",
                     help="grab from ARNIE RTSP (camera | webcam)")
    src.add_argument("--webcam", action="store_true",
                     help=f"grab from local V4L2 device ({CAMERA_DEVICE})")

    args = ap.parse_args()

    if args.frame:
        source_mode, source_arg = "frame", args.frame
    elif args.rtsp:
        source_mode, source_arg = "rtsp", args.rtsp
    elif args.webcam:
        source_mode, source_arg = "webcam", None
    else:
        source_mode, source_arg = "rtsp", "camera"  # default: try ARNIE /camera

    lidar = None
    if args.lidar:
        lidar = LidarDepthReader(domain=args.lidar_domain)
        if not lidar.ok:
            print("[lidar] depth reader failed to start — continuing without depth",
                  file=sys.stderr)
            lidar = None

    app = QtWidgets.QApplication(sys.argv)

    # Skip DDS init when using a static frame — no robot connection needed.
    skip_dds = source_mode == "frame"
    reader = LowStateReader.__new__(LowStateReader) if skip_dds else LowStateReader(args.iface, args.domain)
    if skip_dds:
        reader.joint_cur = {}
        reader.msg_count = 0
        reader.ok = False
        reader._sub = None
    locker = WaistLocker.__new__(WaistLocker) if skip_dds else WaistLocker()
    if skip_dds:
        locker.ok = False

    win = PixelRecorder(reader, locker, args.arm, source_mode, source_arg, lidar=lidar)
    win.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
