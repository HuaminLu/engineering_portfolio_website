"""Flexible frame source for hand_mimic scripts.

Accepts a live camera index, a video file, or a still image.

Special case: index 10 is the IPU6 internal laptop camera exposed via a
v4l2loopback bridge (the ipu6-camera.service was disabled so the camera LED
stays off when not in use). When source=10 is requested, FrameSource starts
the bridge process automatically and kills it on release() so the LED turns off
as soon as mimic.py exits — and Cheese / Meet can use the camera normally.
"""

from __future__ import annotations

import subprocess
import time

import cv2

_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")

# GStreamer pipeline that feeds the IPU6 cam into the loopback device.
_IPU6_INDEX  = 10
_IPU6_DEVICE = "/dev/video10"
_IPU6_GST    = (
    "gst-launch-1.0 -e icamerasrc "
    "! videoconvert "
    "! video/x-raw,format=YUY2 "
    "! identity drop-allocation=1 "
    f"! v4l2sink device={_IPU6_DEVICE} sync=false"
)
_IPU6_WARMUP = 4.0  # seconds to wait for the bridge to produce frames


def parse_source(source: str):
    """Return an int camera index for all-digit strings, else the path string."""
    return int(source) if str(source).isdigit() else source


class FrameSource:
    """Uniform read() -> (ok, frame) over camera / video file / image.

    When source == 10 (the IPU6 internal cam loopback), starts the
    GStreamer bridge process on open and kills it on release().
    """

    def __init__(self, source: str, width: int | None = None,
                 height: int | None = None, loop: bool = True) -> None:
        self.source = source
        self._loop  = loop
        self._bridge: subprocess.Popen | None = None

        self.is_image = isinstance(source, str) and source.lower().endswith(_IMAGE_EXTS)
        if self.is_image:
            self._img = cv2.imread(source)
            if self._img is None:
                raise FileNotFoundError(f"cannot read image: {source}")
            self.is_video_file = False
            self._cap = None
            return

        target = parse_source(source)
        self.is_video_file = isinstance(target, str)

        # IPU6 internal cam: start on-demand bridge
        if not self.is_video_file and target == _IPU6_INDEX:
            self._bridge = subprocess.Popen(
                _IPU6_GST.split(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(_IPU6_WARMUP)  # wait for pipeline to produce frames

        self._cap = cv2.VideoCapture(target)
        if not self.is_video_file:
            self._cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if width:
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        if height:
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    def opened(self) -> bool:
        return True if self.is_image else bool(self._cap and self._cap.isOpened())

    def read(self):
        if self.is_image:
            return True, self._img.copy()
        ok, frame = self._cap.read()
        if not ok and self._loop and self.is_video_file:
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._cap.read()
        return ok, frame

    def describe(self) -> str:
        if self.is_image:
            return f"image:{self.source}"
        bridge = "+ipu6bridge" if self._bridge else ""
        return f"{'video' if self.is_video_file else 'camera'}:{self.source}{bridge}"

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
        if self._bridge is not None:
            self._bridge.terminate()
            try:
                self._bridge.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._bridge.kill()
            self._bridge = None
