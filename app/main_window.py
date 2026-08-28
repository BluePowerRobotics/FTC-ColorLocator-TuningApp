"""主窗口：导航、教学模式、实时防抖、摄像头驱动。"""

from __future__ import annotations

import cv2
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import config as C
from . import pipeline as P
from .camera import Camera
from .pages import PAGE_CLASSES

STEP_NAMES = ["上传", "预处理", "ROI", "降噪", "色范围", "后处理", "过滤排序 + 代码"]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("色块分割调参应用")
        self.resize(1200, 800)

        self.global_cfg = C.GlobalConfig()
        self.processors = [C.default_processor(1)]

        self.image = None
        self.result = None
        self.camera = None
        self.camera_mode = False
        self.frozen = False
        self.current_file_path = None

        # 文件操作（顶部栏左侧）
        self.file_btn = QToolButton()
        self.file_btn.setText("文件")
        self.file_btn.setPopupMode(QToolButton.InstantPopup)
        self.file_menu = QMenu(self.file_btn)
        for _text, _slot in (
            ("打开", self.open_file),
            ("打开并合并", self.open_merge),
            ("保存", self.save_file),
            ("另存为", self.save_file_as),
            ("另存并合并到", self.save_merge_to),
        ):
            _act = self.file_menu.addAction(_text)
            _act.triggered.connect(_slot)
        self.file_btn.setMenu(self.file_menu)

        self.open_btn = QPushButton("打开")
        self.open_btn.clicked.connect(self.open_file)
        self.save_btn = QPushButton("保存")
        self.save_btn.clicked.connect(self.save_file)
        self.save_as_btn = QPushButton("另存为")
        self.save_as_btn.clicked.connect(self.save_file_as)

        # 顶部栏
        self.teach_btn = QPushButton("教学模式 关")
        self.teach_btn.setStyleSheet("background:#6a3fb5; color:white;")
        self.teach_btn.clicked.connect(self.toggle_teaching)

        self.step_label = QLabel("")
        self.step_label.setStyleSheet("font-size:15px; font-weight:bold;")

        self.freeze_btn = QPushButton("冻结画面")
        self.freeze_btn.setVisible(False)
        self.freeze_btn.clicked.connect(self.toggle_freeze)

        self.prev_btn = QPushButton("上一步")
        self.next_btn = QPushButton("下一步")
        self.prev_btn.clicked.connect(lambda: self.go_to_page(self.global_cfg.page_index - 1))
        self.next_btn.clicked.connect(lambda: self.go_to_page(self.global_cfg.page_index + 1))

        top = QWidget()
        top_lay = QHBoxLayout(top)
        top_lay.setContentsMargins(8, 6, 8, 6)
        top_lay.addWidget(self.file_btn)
        top_lay.addWidget(self.open_btn)
        top_lay.addWidget(self.save_btn)
        top_lay.addWidget(self.save_as_btn)
        top_lay.addWidget(self.teach_btn)
        top_lay.addStretch(1)
        top_lay.addWidget(self.step_label)
        top_lay.addStretch(1)
        top_lay.addWidget(self.freeze_btn)
        top_lay.addWidget(self.prev_btn)
        top_lay.addWidget(self.next_btn)

        # 页面
        self.stack = QStackedWidget()
        self.pages = []
        for cls in PAGE_CLASSES:
            page = cls(self)
            self.pages.append(page)
            self.stack.addWidget(page)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(top)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        # 定时器
        self._pipeline_timer = QTimer(self)
        self._pipeline_timer.setSingleShot(True)
        self._pipeline_timer.setInterval(30)
        self._pipeline_timer.timeout.connect(self._run_pipeline)

        self._camera_timer = QTimer(self)
        self._camera_timer.setInterval(30)
        self._camera_timer.timeout.connect(self._camera_tick)

        # 初始状态
        for p in self.pages:
            p.set_tutorial_visible(self.global_cfg.teaching_mode)
        self._apply_teaching_style()
        self.go_to_page(0)

    # ------------------------------------------------------------------
    # 教学模式
    # ------------------------------------------------------------------
    def toggle_teaching(self):
        self.global_cfg.teaching_mode = not self.global_cfg.teaching_mode
        for p in self.pages:
            p.set_tutorial_visible(self.global_cfg.teaching_mode)
        self._apply_teaching_style()

    def _apply_teaching_style(self):
        if self.global_cfg.teaching_mode:
            self.teach_btn.setText("教学模式 开")
            self.teach_btn.setStyleSheet("background:#2e7d32; color:white;")
        else:
            self.teach_btn.setText("教学模式 关")
            self.teach_btn.setStyleSheet("background:#6a3fb5; color:white;")

    # ------------------------------------------------------------------
    # 导航
    # ------------------------------------------------------------------
    def go_to_page(self, index):
        index = max(0, min(6, index))
        self.global_cfg.page_index = index
        self.stack.setCurrentIndex(index)
        page = self.pages[index]
        page.rebuild_params()
        page.refresh()
        self._update_nav()

    def _update_nav(self):
        idx = self.global_cfg.page_index
        self.step_label.setText(self._step_text())
        self.prev_btn.setVisible(idx != 0)
        self.next_btn.setVisible(idx != 0)
        self.prev_btn.setEnabled(idx > 0)
        self.next_btn.setEnabled(idx < 6)
        self.freeze_btn.setVisible(self.camera_mode)

    def _step_text(self):
        name = STEP_NAMES[self.global_cfg.page_index]
        if self.camera_mode and not self.frozen and self.global_cfg.page_index != 0:
            name += " [实时]"
        return name

    # ------------------------------------------------------------------
    # 输入源
    # ------------------------------------------------------------------
    def set_image(self, bgr):
        self._stop_camera()
        self.image = bgr
        self.camera_mode = False
        self.frozen = False
        self._run_pipeline()
        self.go_to_page(1)

    def start_camera(self, index: int = 0):
        self._stop_camera()
        self.camera = Camera(index)
        if not self.camera.open():
            QMessageBox.warning(self, "摄像头", "无法打开所选摄像头。")
            self.camera = None
            return
        self.camera_mode = True
        self.frozen = False
        self._camera_timer.start()
        self._update_nav()
        self.go_to_page(1)

    def toggle_freeze(self):
        if not self.camera_mode:
            return
        self.frozen = not self.frozen
        if self.frozen:
            self.freeze_btn.setText("恢复画面")
            self.freeze_btn.setStyleSheet("background:#ef6c00; color:white;")
        else:
            self.freeze_btn.setText("冻结画面")
            self.freeze_btn.setStyleSheet("")
        self._update_nav()

    def _stop_camera(self):
        self._camera_timer.stop()
        if self.camera is not None:
            self.camera.close()
            self.camera = None
        self.camera_mode = False
        self.frozen = False

    def _camera_tick(self):
        if not self.camera_mode or self.frozen:
            return
        frame = self.camera.latest_frame()
        if frame is not None:
            self.image = frame
            self._run_pipeline()

    # ------------------------------------------------------------------
    # 管道
    # ------------------------------------------------------------------
    def on_param_changed(self):
        self._pipeline_timer.start()

    def _run_pipeline(self):
        if self.image is None:
            self.result = None
        else:
            self.result = P.run_pipeline(self.image, self.global_cfg, self.processors)
        for p in self.pages:
            p.refresh()

    def ds_size(self):
        if self.result is not None:
            h, w = self.result.downsampled.shape[:2]
            return (w, h)
        if self.image is not None:
            return P.downsample_size(self.image, self.global_cfg.downsample_rate)
        return None

    # ------------------------------------------------------------------
    # Processor 管理
    # ------------------------------------------------------------------
    def add_processor(self):
        self.processors.append(C.default_processor(len(self.processors) + 1))
        self._rebuild_param_pages()
        self.on_param_changed()

    def remove_processor(self, index):
        if len(self.processors) <= 1:
            return
        if 0 <= index < len(self.processors):
            del self.processors[index]
        self._rebuild_param_pages()
        self.on_param_changed()

    def _rebuild_param_pages(self):
        # 页面 2..6 含 Processor 参数组
        for i in (2, 3, 4, 5, 6):
            self.pages[i].rebuild_params()
        # 刷新当前页
        page = self.pages[self.global_cfg.page_index]
        page.rebuild_params()
        page.refresh()

    # ------------------------------------------------------------------
    # 文件操作
    # ------------------------------------------------------------------
    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开", "", C.FILE_FILTER)
        if not path:
            return
        try:
            g, ps = C.load_state_file(path)
        except Exception as e:
            QMessageBox.warning(self, "打开", f"加载失败：{e}")
            return
        self.global_cfg = g
        self.processors = ps
        self.current_file_path = path
        self._apply_loaded_state()

    def _confirm_merge_resolution(self, host_dsr, guest_dsr):
        """合并前检查分辨率：一致返回 True；不一致弹窗让用户选择是否仍要自动合并。"""
        if host_dsr == guest_dsr:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("自动合并")
        box.setText("自动合并可能导致参数失效")
        box.setIcon(QMessageBox.Warning)
        cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
        merge_btn = box.addButton("仍要自动合并", QMessageBox.AcceptRole)
        box.setDefaultButton(cancel_btn)
        box.exec()
        return box.clickedButton() == merge_btn

    def open_merge(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开并合并", "", C.FILE_FILTER)
        if not path:
            return
        try:
            g, ps = C.load_state_file(path)
        except Exception as e:
            QMessageBox.warning(self, "打开并合并", f"加载失败：{e}")
            return
        if not self._confirm_merge_resolution(self.global_cfg.downsample_rate, g.downsample_rate):
            return
        self.global_cfg = C.merge_global(self.global_cfg, g)
        self.processors = C.merge_processors(self.processors, ps)
        self._apply_loaded_state()

    def save_file(self):
        if self.current_file_path is None:
            self.save_file_as()
            return
        try:
            C.save_state_file(self.current_file_path, self.global_cfg, self.processors)
        except Exception as e:
            QMessageBox.warning(self, "保存", f"保存失败：{e}")

    def save_file_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "另存为", "color_params.clp", C.FILE_FILTER)
        if not path:
            return
        if not path.endswith(C.FILE_EXT):
            path += C.FILE_EXT
        try:
            C.save_state_file(path, self.global_cfg, self.processors)
        except Exception as e:
            QMessageBox.warning(self, "另存为", f"保存失败：{e}")
            return
        self.current_file_path = path

    def save_merge_to(self):
        path, _ = QFileDialog.getOpenFileName(self, "另存并合并到", "", C.FILE_FILTER)
        if not path:
            return
        try:
            g, exist_ps = C.load_state_file(path)
        except Exception as e:
            QMessageBox.warning(self, "另存并合并到", f"加载失败：{e}")
            return
        if not self._confirm_merge_resolution(g.downsample_rate, self.global_cfg.downsample_rate):
            return
        merged_global = C.merge_global(g, self.global_cfg)
        merged = C.merge_processors(exist_ps, self.processors)
        try:
            C.save_state_file(path, merged_global, merged)
        except Exception as e:
            QMessageBox.warning(self, "另存并合并到", f"保存失败：{e}")
            return
        self.global_cfg = merged_global
        self.processors = merged
        self.current_file_path = path
        self._apply_loaded_state()

    def _apply_loaded_state(self):
        # 页面 1：同步降采样率；页面 2..6：重建 Processor 参数组
        self.pages[1].rebuild_params()
        for i in (2, 3, 4, 5, 6):
            self.pages[i].rebuild_params()
        self._run_pipeline()

    # ------------------------------------------------------------------
    def closeEvent(self, e):
        self._stop_camera()
        super().closeEvent(e)