"""摄像头采集：DirectShow 打开默认摄像头，独立线程持续抓帧，旧帧自动丢弃。"""

from __future__ import annotations

import threading

import cv2


class Camera:
    def __init__(self):
        self._cap = None
        self._running = False
        self._thread = None
        self._frame = None
        self._lock = threading.Lock()

    def open(self) -> bool:
        """打开默认摄像头（DirectShow，1280x720）；失败回退默认后端。"""
        self._cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        if not self._cap.isOpened():
            self._cap = cv2.VideoCapture(0)
        if not self._cap.isOpened():
            self._cap = None
            return False
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
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