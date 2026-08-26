"""摄像头采集：枚举可用设备、打开指定摄像头并默认最大分辨率、独立线程抓帧。"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import cv2


@dataclass
class CameraInfo:
    index: int
    width: int
    height: int


def _max_resolution(cap: cv2.VideoCapture) -> tuple[int, int]:
    """把分辨率设为超大值，让驱动按最大支持分辨率钳制后读回实际值。"""
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 10000)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 10000)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if w <= 0 or h <= 0:
        return 1280, 720
    return w, h


def enumerate_cameras(max_index: int = 8) -> list[CameraInfo]:
    """枚举可用摄像头，返回各自索引与最大分辨率（供选择弹窗使用）。"""
    devices: list[CameraInfo] = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(i)
        if cap.isOpened():
            w, h = _max_resolution(cap)
            devices.append(CameraInfo(i, w, h))
        cap.release()
    return devices


class Camera:
    def __init__(self, index: int = 0):
        self._index = index
        self._cap = None
        self._running = False
        self._thread = None
        self._frame = None
        self._lock = threading.Lock()

    def open(self) -> bool:
        """打开指定摄像头（DirectShow 优先）；默认使用最大分辨率，失败回退默认后端。"""
        self._cap = cv2.VideoCapture(self._index, cv2.CAP_DSHOW)
        if not self._cap.isOpened():
            self._cap = cv2.VideoCapture(self._index)
        if not self._cap.isOpened():
            self._cap = None
            return False
        w, h = _max_resolution(self._cap)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def _run(self):
        while self._running:
            ok, frame = self._cap.read()
            if ok:
                with self._lock:
                    self._frame = frame
            else:
                # 读取失败时轻微让出 CPU
                import time
                time.sleep(0.005)

    def latest_frame(self):
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def close(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=0.6)
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._frame = None