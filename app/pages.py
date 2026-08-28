"""向导页面：上传 / 预处理 / ROI / 降噪 / 色范围 / 后处理 / 过滤排序+代码。"""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import config as C
from . import codegen
from . import pipeline as P
from .camera import enumerate_cameras
from .widgets import ImageView, OptionBox, SliderSpin, ThresholdGroupCard


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def _bgr2rgb(img):
    if img is None:
        return None
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def _make_split(parent, widgets, ratios, orientation):
    """把 widgets 放入可拖动分界线的 QSplitter，并设为 parent 的布局。"""
    splitter = QSplitter(orientation)
    for w in widgets:
        splitter.addWidget(w)
    total = 1200 if orientation == Qt.Horizontal else 800
    unit = total // max(1, sum(ratios))
    splitter.setSizes([r * unit for r in ratios])
    for i, r in enumerate(ratios):
        splitter.setStretchFactor(i, r)
    splitter.setChildrenCollapsible(False)
    lay = QHBoxLayout(parent) if orientation == Qt.Horizontal else QVBoxLayout(parent)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(splitter)


def _h_split(parent, widgets, ratios):
    _make_split(parent, widgets, ratios, Qt.Horizontal)


def _v_split(parent, widgets, ratios):
    _make_split(parent, widgets, ratios, Qt.Vertical)


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())


def _label(text, bold=False):
    lb = QLabel(text)
    if bold:
        f = lb.font()
        f.setBold(True)
        lb.setFont(f)
    return lb


def _params_scroll(widget):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(widget)
    # 允许内部内容收缩到视口宽度以下，避免因最小尺寸过大被右侧裁剪
    widget.setMinimumWidth(0)
    widget.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Preferred)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    return scroll


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _make_tutorial(title, lines):
    box = QGroupBox(title)
    v = QVBoxLayout(box)
    v.setContentsMargins(6, 8, 6, 8)
    parts = ['<div style="font-size:12px; color:#3a3a3a;">']
    for ln in lines:
        if ln.startswith("# "):
            parts.append(f'<div style="font-weight:bold; margin-top:8px;">{_esc(ln[2:])}</div>')
        elif ln.startswith("- "):
            parts.append(f'<div style="margin-left:10px;">• {_esc(ln[2:])}</div>')
        elif ln == "":
            parts.append('<div style="height:4px;"></div>')
        else:
            parts.append(f'<div>{_esc(ln)}</div>')
    parts.append('</div>')
    txt = QLabel("".join(parts))
    txt.setTextFormat(Qt.RichText)
    txt.setWordWrap(True)
    txt.setAlignment(Qt.AlignTop | Qt.AlignLeft)
    txt.setTextInteractionFlags(Qt.TextSelectableByMouse)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setWidget(txt)
    scroll.setMinimumHeight(150)
    v.addWidget(scroll)
    return box


def _gradient(t, anchors):
    t = max(0.0, min(1.0, t))
    for i in range(len(anchors) - 1):
        t0, c0 = anchors[i]
        t1, c1 = anchors[i + 1]
        if t0 <= t <= t1:
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return tuple(int(c0[j] + (c1[j] - c0[j]) * f) for j in range(3))
    return anchors[-1][1]


_RANK_ANCHORS = [(0.0, (0, 255, 0)), (0.33, (0, 255, 255)), (0.66, (0, 80, 255)), (1.0, (170, 0, 255))]


_MIN_LABEL_AREA_RATIO = 0.1  # 排名数字标注的面积比例阈值（相对最大色块，低于则不标注）


def _rank_color(rank, total):
    if total <= 1:
        t = 0.0
    else:
        t = (rank - 1) / (total - 1)
    return _gradient(t, _RANK_ANCHORS)


def _union_roi_rect(results, W, H, margin=8):
    if not results:
        return (0, 0, W, H)
    x1, y1 = W, H
    x2, y2 = 0, 0
    for pr in results:
        x, y, w, h = pr.roi_rect
        x1 = min(x1, x)
        y1 = min(y1, y)
        x2 = max(x2, x + w)
        y2 = max(y2, y + h)
    x1 = max(0, x1 - margin)
    y1 = max(0, y1 - margin)
    x2 = min(W, x2 + margin)
    y2 = min(H, y2 + margin)
    if x2 <= x1 or y2 <= y1:
        return (0, 0, W, H)
    return (x1, y1, x2 - x1, y2 - y1)


def _draw_roi_rect(img, roi_rect, color=(0, 255, 255), thickness=2):
    x, y, w, h = roi_rect
    cv2.rectangle(img, (x, y), (x + w, y + h), color, thickness)
    return img


# ---------------------------------------------------------------------------
# 页面基类
# ---------------------------------------------------------------------------

class Page(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.tutorial_panel = None

    def rebuild_params(self):
        pass

    def refresh(self):
        pass

    def set_tutorial_visible(self, v):
        if self.tutorial_panel is not None:
            self.tutorial_panel.setVisible(v)


# ---------------------------------------------------------------------------
# 页面 0：上传页
# ---------------------------------------------------------------------------

class UploadPage(Page):
    def __init__(self, controller):
        super().__init__(controller)
        self.tutorial_panel = _make_tutorial("教程", [
            "# 工作流程",
            "- 本应用是一个“分步向导”：上传 → 预处理 → ROI → 降噪 → 色范围 → 后处理 → 过滤排序+代码，",
            "  每一步调整一个视觉处理环节，最终一键生成 FTC Java 代码。",
            "# 操作",
            "- 先用静态图片调参，再用摄像头微调。",
            "- 摄像头模式下可“冻结画面”精细调参。",
            "- 选择图片或打开摄像头后自动进入第 1 页。",
            "# 文件管理",
            "- 顶部栏左侧的「文件」菜单与按钮，可把当前整组参数（全局配置 + 各 Processor）保存为 .clp 文件随时复用。",
            "- 打开：载入 .clp 文件并覆盖当前参数；保存：写回当前文件；另存为：另存为新文件。",
            "- 打开并合并：把文件中的 Processor 追加为新 Processor，不覆盖已有参数；合并时分辨率取两者较大。",
            "- 另存并合并到：把当前 Processor 追加进所选文件，不覆盖该文件已有参数，保存后自动打开合并结果。",
            "# 什么是色块分割",
            "- 把图像中符合某种颜色范围的区域（blob，色块）找出来，输出它的位置、大小、形状与若干指标，",
            "  供机器人在画面中定位目标物体。",
        ])

        title = _label("色块分割调参应用", bold=True)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size:24px;")
        subtitle = _label("为 FTC ColorBlobLocatorProcessor 可视化调参并生成 Java 代码")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color:#666;")

        self.btn_image = QPushButton("选择图片")
        self.btn_camera = QPushButton("打开摄像头")
        self.btn_image.setMinimumHeight(40)
        self.btn_camera.setMinimumHeight(40)

        self.thumb = ImageView()
        self.thumb.setMinimumHeight(260)

        center = QWidget()
        cv = QVBoxLayout(center)
        cv.addWidget(title)
        cv.addWidget(subtitle)
        cv.addSpacing(16)
        cv.addWidget(self.btn_image)
        cv.addWidget(self.btn_camera)
        cv.addWidget(self.thumb, 1)
        cv.addStretch(0)

        _h_split(self, [center, self.tutorial_panel], [1, 1])

        self.btn_image.clicked.connect(self._pick_image)
        self.btn_camera.clicked.connect(self._open_camera)

    def _pick_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)")
        if not path:
            return
        # cv2.imread 在 Windows 上无法读取含中文等非 ASCII 字符的路径，改用 imdecode 解码字节流。
        img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return
        self.btn_image.setText("重新选择图片")
        self.controller.set_image(img)

    def _open_camera(self):
        # 先释放当前摄像头，避免再次枚举时与占用中的设备冲突导致界面卡死
        self.controller._stop_camera()
        devices = enumerate_cameras()
        if not devices:
            QMessageBox.warning(self, "摄像头", "未检测到可用摄像头。")
            return
        if len(devices) == 1:
            self.controller.start_camera(devices[0].index)
            return
        entries = [(d.index, f"摄像头 {d.index}（{d.width}×{d.height}）") for d in devices]
        label, ok = QInputDialog.getItem(self, "选择摄像头", "检测到多个摄像头，请选择：",
                                         [e[1] for e in entries], 0, False)
        if ok:
            idx = next(e[0] for e in entries if e[1] == label)
            self.controller.start_camera(idx)

    def refresh(self):
        img = self.controller.image
        self.thumb.set_image(_bgr2rgb(img))


# ---------------------------------------------------------------------------
# 页面 1：预处理（降采样）
# ---------------------------------------------------------------------------

class PreprocessPage(Page):
    def __init__(self, controller):
        super().__init__(controller)
        self.tutorial_panel = _make_tutorial("教程", [
            "# 操作",
            "- 降采样率越高，处理越快但细节越少，建议取值 1~4。",
            "- 下方实时显示降采样后的分辨率。",
            "# 什么是降采样",
            "- 把图像按比例缩小：每 N 个像素取 1 个，边长缩小为 1/N，像素总数缩小为 1/N²。",
            "# 为什么降采样",
            "- 分辨率降低后，颜色转换、模糊、找轮廓、过滤等所有运算都更省时，适合机器人端的有限算力。",
            "- 代价是丢失高频细节，降得过小会看不清小目标，需在速度与精度间取舍。",
        ])

        self.rate = SliderSpin("降采样率", 1, 8, controller.global_cfg.downsample_rate)
        self.res_label = _label("", True)

        params = QWidget()
        pv = QVBoxLayout(params)
        pv.addWidget(self.rate)
        pv.addWidget(self.res_label)
        pv.addStretch(1)

        self.view = ImageView()

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(_params_scroll(params), 1)
        lv.addWidget(self.tutorial_panel)

        _h_split(self, [left, self.view], [1, 3])

        self.rate.value_changed.connect(self._on_change)

    def _on_change(self, _v):
        self.controller.global_cfg.downsample_rate = int(self.rate.value())
        self.controller.on_param_changed()

    def rebuild_params(self):
        self.rate.set_value_silent(self.controller.global_cfg.downsample_rate)

    def refresh(self):
        res = self.controller.result
        if res is None:
            self.res_label.setText("当前分辨率：-")
            self.view.set_image(None)
            return
        h, w = res.downsampled.shape[:2]
        oh, ow = res.original.shape[:2]
        self.res_label.setText(f"当前分辨率：{w} × {h}（原始 {ow} × {oh} / {self.rate.value()}）")
        self.view.set_image(_bgr2rgb(res.downsampled))


# ---------------------------------------------------------------------------
# 页面 2：ROI
# ---------------------------------------------------------------------------

class RoiPage(Page):
    def __init__(self, controller):
        super().__init__(controller)
        self.tutorial_panel = _make_tutorial("教程", [
            "# 操作",
            "- 整帧：在整个画面搜索目标。",
            "- 归一化坐标：u∈[-1,1]，v∈[-1,1]，中心为原点，右上为正。",
            "- 每个 Processor 有独立 ROI；右侧下拉框切换显示单个 Processor。",
            "# 什么是 ROI",
            "- ROI（Region of Interest，感兴趣区域）只保留画面中的一部分进行处理，其余忽略。",
            "# 为什么用 ROI",
            "- 缩小搜索范围可排除背景干扰、提升速度；目标进入该区域才被识别。",
            "# 归一化坐标",
            "- asUnityCenterCoordinates 把画面宽高都映射到 [-1,1]：中心为 (0,0)，左上角靠近 (-1,-1)。",
            "- v 轴方向与图像 y 相反，上方为正。这样写法与分辨率解耦，更换摄像头分辨率无需改数值。",
        ])

        self.groups_vbox = QVBoxLayout()
        self.groups_vbox.addStretch(1)
        self._group_refs = []  # (index, roi_mode, pixel_sliders, norm_sliders)

        self.selector = QComboBox()
        self.selector.currentIndexChanged.connect(lambda _i: self.refresh())

        params = QWidget()
        pv = QVBoxLayout(params)
        pv.addLayout(self.groups_vbox)
        self.add_btn = QPushButton("添加 Processor")
        pv.addWidget(self.add_btn)
        self.add_btn.clicked.connect(lambda: self.controller.add_processor())

        self.view = ImageView()
        self.view.hover_moved.connect(self._on_hover)
        self.view.hover_left.connect(lambda: self._status.setText(""))
        self._status = QLabel("")
        self._status.setStyleSheet("color:#555;")

        right = QWidget()
        rv = QVBoxLayout(right)
        sel_row = QHBoxLayout()
        sel_row.addWidget(_label("显示 Processor"))
        sel_row.addWidget(self.selector, 1)
        rv.addLayout(sel_row)
        rv.addWidget(self.view, 1)
        rv.addWidget(self._status)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(_params_scroll(params), 1)
        lv.addWidget(self.tutorial_panel)

        _h_split(self, [left, right], [1, 3])

    # --- 参数重建 ---
    def rebuild_params(self):
        _clear_layout(self.groups_vbox)
        self._group_refs = []
        procs = self.controller.processors
        for i, proc in enumerate(procs):
            self._build_group(i, proc)
        self.groups_vbox.addStretch(1)
        self._sync_selector()

    def _build_group(self, i, proc):
        box = QGroupBox(f"Processor {i + 1}")
        v = QVBoxLayout(box)
        header = QHBoxLayout()
        header.addStretch(1)
        del_btn = QPushButton("删除")
        del_btn.setStyleSheet("color:#c33;")
        del_btn.clicked.connect(lambda _=False, idx=i: self.controller.remove_processor(idx))
        header.addWidget(del_btn)
        v.addLayout(header)

        mode = OptionBox("ROI 模式", C.ROI_MODES)
        mode.set_value(proc.roi_mode, silent=True)

        # 归一化坐标滑条
        nm = []
        for name, val in zip(("uMin", "vMax", "uMax", "vMin"), proc.roi_norm):
            s = SliderSpin(name, -1.0, 1.0, val, step=0.01, integer=False)
            nm.append(s)
        nm_wrap = QWidget()
        nm_lay = QVBoxLayout(nm_wrap)
        nm_lay.setContentsMargins(0, 0, 0, 0)
        for s in nm:
            nm_lay.addWidget(s)

        v.addWidget(mode)
        v.addWidget(nm_wrap)

        self.groups_vbox.addWidget(box)
        self._group_refs.append((i, mode, nm, nm_wrap))

        self._apply_mode_visibility(i, proc.roi_mode)

        # 信号
        mode.changed.connect(lambda _v, idx=i: self._on_mode_changed(idx))
        for s in nm:
            s.value_changed.connect(lambda _v, idx=i: self._on_roi_changed(idx))

    def _apply_mode_visibility(self, idx, mode):
        _, _, _nm, nm_wrap = self._group_refs[idx]
        nm_wrap.setVisible(mode == C.ROI_NORMALIZED)

    def _on_mode_changed(self, idx):
        _, mode_box, _nm, _nm_wrap = self._group_refs[idx]
        mode = mode_box.value()
        proc = self.controller.processors[idx]
        proc.roi_mode = mode
        self._apply_mode_visibility(idx, mode)
        self.controller.on_param_changed()

    def _on_roi_changed(self, idx):
        _, _mode_box, nm, _nm_wrap = self._group_refs[idx]
        proc = self.controller.processors[idx]
        proc.roi_norm = [float(s.value()) for s in nm]
        self.controller.on_param_changed()

    def _sync_selector(self):
        names = [f"Processor {i + 1}" for i in range(len(self.controller.processors))]
        cur = self.selector.currentIndex()
        self.selector.blockSignals(True)
        self.selector.clear()
        self.selector.addItems(names)
        self.selector.setCurrentIndex(max(0, min(cur, len(names) - 1)))
        self.selector.blockSignals(False)

    # --- 预览 ---
    def _on_hover(self, x, y):
        self._status.setText(f"图像坐标：({x}, {y})")

    def refresh(self):
        res = self.controller.result
        if res is None:
            self.view.set_image(None)
            return
        idx = max(0, self.selector.currentIndex())
        if idx >= len(res.processors):
            idx = 0
        pr = res.processors[idx] if res.processors else None
        base = res.downsampled.copy()
        if pr is not None:
            _draw_roi_rect(base, pr.roi_rect)
        self.view.set_image(_bgr2rgb(base))


# ---------------------------------------------------------------------------
# 页面 3：降噪
# ---------------------------------------------------------------------------

class DenoisePage(Page):
    def __init__(self, controller):
        super().__init__(controller)
        self.tutorial_panel = _make_tutorial("教程", [
            "# 操作",
            "- blurSize：高斯模糊核大小，必须为奇数（偶数自动 +1）。",
            "- 数值越大越模糊，越能隐藏细小噪声与纹理；低分辨率下建议 5。",
            "- 每个 Processor 独立设置。",
            "# 什么是高斯模糊",
            "- 用“高斯核”对图像做加权平均：每个像素用它邻域像素的加权和替代。",
            "- 权重按离中心像素的距离呈钟形（高斯）分布，中心最重、越远越轻。",
            "# 为什么降噪",
            "- 低光照下相机会产生噪点，破坏色块轮廓（出现碎块、毛刺、孔洞）。",
            "- 先平滑能让后续阈值分割与轮廓提取更稳定。",
            "# 与降采样的区别",
            "- 降采样改变图像尺寸，高斯模糊只平滑、不改变尺寸。",
        ])

        self.groups_vbox = QVBoxLayout()
        self.groups_vbox.addStretch(1)
        self._sliders = []

        self.selector = QComboBox()
        self.selector.currentIndexChanged.connect(lambda _i: self.refresh())

        params = QWidget()
        pv = QVBoxLayout(params)
        pv.addLayout(self.groups_vbox)
        self.add_btn = QPushButton("添加 Processor")
        pv.addWidget(self.add_btn)
        self.add_btn.clicked.connect(lambda: self.controller.add_processor())

        self.view = ImageView()
        right = QWidget()
        rv = QVBoxLayout(right)
        sel_row = QHBoxLayout()
        sel_row.addWidget(_label("显示 Processor"))
        sel_row.addWidget(self.selector, 1)
        rv.addLayout(sel_row)
        rv.addWidget(self.view, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(_params_scroll(params), 1)
        lv.addWidget(self.tutorial_panel)

        _h_split(self, [left, right], [1, 3])

    def rebuild_params(self):
        _clear_layout(self.groups_vbox)
        self._sliders = []
        for i, proc in enumerate(self.controller.processors):
            box = QGroupBox(f"Processor {i + 1}")
            v = QVBoxLayout(box)
            header = QHBoxLayout()
            header.addStretch(1)
            del_btn = QPushButton("删除")
            del_btn.setStyleSheet("color:#c33;")
            del_btn.clicked.connect(lambda _=False, idx=i: self.controller.remove_processor(idx))
            header.addWidget(del_btn)
            v.addLayout(header)

            s = SliderSpin("blurSize", 1, 31, proc.blur_size, integer=True, odd=True)
            v.addWidget(s)
            self.groups_vbox.addWidget(box)
            self._sliders.append((i, s))
            s.value_changed.connect(lambda _v, idx=i: self._on_change(idx))
        self.groups_vbox.addStretch(1)
        self._sync_selector()

    def _on_change(self, idx):
        _i, s = self._sliders[idx]
        self.controller.processors[idx].blur_size = int(s.value())
        self.controller.on_param_changed()

    def _sync_selector(self):
        names = [f"Processor {i + 1}" for i in range(len(self.controller.processors))]
        cur = self.selector.currentIndex()
        self.selector.blockSignals(True)
        self.selector.clear()
        self.selector.addItems(names)
        self.selector.setCurrentIndex(max(0, min(cur, len(names) - 1)))
        self.selector.blockSignals(False)

    def refresh(self):
        res = self.controller.result
        if res is None:
            self.view.set_image(None)
            return
        idx = max(0, self.selector.currentIndex())
        if idx >= len(res.processors):
            idx = 0
        pr = res.processors[idx] if res.processors else None
        self.view.set_image(_bgr2rgb(pr.denoised if pr else res.downsampled))


# ---------------------------------------------------------------------------
# 页面 4：色范围
# ---------------------------------------------------------------------------

class ColorRangePage(Page):
    def __init__(self, controller):
        super().__init__(controller)
        self.tutorial_panel = _make_tutorial("教程", [
            "# 操作",
            "- 三通道阈值：像素三个通道都落在上下界之间才判为“目标”。",
            "- 预定义颜色选择后自动填充色彩空间与阈值。",
            "- 添加多个 Processor 可同时查找多种颜色。",
            "# 什么是色空间",
            "- 色空间是用一组数值表示颜色的坐标系统；同一颜色在不同空间中的数值不同。",
            "# RGB",
            "- 红、绿、蓝三原色相加，符合显示器直觉，但对光照/阴影变化很敏感。",
            "# HSV",
            "- 色相(Hue)、饱和度(Saturation)、明度(Value)：H 描述“是什么颜色”，S/V 描述浓度与明暗，更贴近人的感知。",
            "- OpenCV 中 H 为 0~179（本应用滑条标为 0~180）。",
            "# YCrCb",
            "- 亮度(Y) 与两个色差分量(Cr/Cb) 分离，把颜色与明暗解耦，受光照变化影响小，常用于目标检测定位。",
            "# 阈值选色技巧",
            "- 先选最接近目标的预定义色，再微调上下界。",
            "- 把鼠标移到画面上，底部会显示该点在各色空间中的具体数值，可据此抄写下界/上界。",
            "- 范围宜“先松后紧”：先用宽范围让目标出现，再逐步收紧以排除干扰。",
        ])

        self.groups_vbox = QVBoxLayout()
        self.groups_vbox.addStretch(1)
        self._groups = []  # (index, preset_box, card)

        self.selector = QComboBox()
        self.selector.currentIndexChanged.connect(lambda _i: self.refresh())

        params = QWidget()
        pv = QVBoxLayout(params)
        pv.addLayout(self.groups_vbox)
        self.add_btn = QPushButton("添加 Processor")
        pv.addWidget(self.add_btn)
        self.add_btn.clicked.connect(lambda: self.controller.add_processor())

        self.view = ImageView()
        self.view.hover_moved.connect(self._on_hover)
        self.view.hover_left.connect(lambda: self._status.setText(""))
        self._status = QLabel("")
        self._status.setStyleSheet("color:#555;")

        right = QWidget()
        rv = QVBoxLayout(right)
        sel_row = QHBoxLayout()
        sel_row.addWidget(_label("显示 Processor"))
        sel_row.addWidget(self.selector, 1)
        rv.addLayout(sel_row)
        rv.addWidget(self.view, 1)
        rv.addWidget(self._status)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(_params_scroll(params), 1)
        lv.addWidget(self.tutorial_panel)

        _h_split(self, [left, right], [1, 3])

    def rebuild_params(self):
        _clear_layout(self.groups_vbox)
        self._groups = []
        for i, proc in enumerate(self.controller.processors):
            box = QGroupBox(f"Processor {i + 1}")
            v = QVBoxLayout(box)
            header = QHBoxLayout()
            header.addStretch(1)
            del_btn = QPushButton("删除")
            del_btn.setStyleSheet("color:#c33;")
            del_btn.clicked.connect(lambda _=False, idx=i: self.controller.remove_processor(idx))
            header.addWidget(del_btn)
            v.addLayout(header)

            preset = OptionBox("预定义颜色", C.PREDEFINED_NAMES)
            preset.set_value(proc.preset, silent=True)
            card = ThresholdGroupCard("色范围")
            card.load(proc)
            v.addWidget(preset)
            v.addWidget(card)

            self.groups_vbox.addWidget(box)
            self._groups.append((i, preset, card))

            preset.changed.connect(lambda _v, idx=i: self._on_preset(idx))
            card.changed.connect(lambda idx=i: self._on_card_edit(idx))
        self.groups_vbox.addStretch(1)
        self._sync_selector()

    def _on_preset(self, idx):
        _i, preset_box, card = self._groups[idx]
        proc = self.controller.processors[idx]
        name = preset_box.value()
        if name == "自定义":
            proc.preset = name
        else:
            space, lower, upper = C.PREDEFINED_COLORS[name]
            proc.preset = name
            proc.color_space = space
            proc.lower = list(lower)
            proc.upper = list(upper)
            card.load(proc)
        self.controller.on_param_changed()

    def _on_card_edit(self, idx):
        _i, preset_box, card = self._groups[idx]
        proc = self.controller.processors[idx]
        card.write(proc)
        # 手动编辑后切换为“自定义”，避免与预定义回填冲突
        if proc.preset != "自定义":
            proc.preset = "自定义"
            preset_box.set_value("自定义", silent=True)
        self.controller.on_param_changed()

    def _sync_selector(self):
        names = [f"Processor {i + 1}" for i in range(len(self.controller.processors))]
        cur = self.selector.currentIndex()
        self.selector.blockSignals(True)
        self.selector.clear()
        self.selector.addItems(names)
        self.selector.setCurrentIndex(max(0, min(cur, len(names) - 1)))
        self.selector.blockSignals(False)

    def _on_hover(self, x, y):
        res = self.controller.result
        idx = max(0, self.selector.currentIndex())
        if res is None or idx >= len(res.processors):
            return
        pr = res.processors[idx]
        h, w = pr.mask.shape[:2]
        if 0 <= x < w and 0 <= y < h:
            is_on = int(pr.mask[y, x] > 0)
            bgr = pr.roi_bgr[y, x]
            ycrcb = cv2.cvtColor(np.array([[bgr]], np.uint8), cv2.COLOR_BGR2YCrCb)[0][0]
            rgb = cv2.cvtColor(np.array([[bgr]], np.uint8), cv2.COLOR_BGR2RGB)[0][0]
            hsv = cv2.cvtColor(np.array([[bgr]], np.uint8), cv2.COLOR_BGR2HSV)[0][0]
            self._status.setText(
                f"坐标({x},{y})  判定={is_on}  "
                f"YCrCb=({ycrcb[0]},{ycrcb[1]},{ycrcb[2]})  "
                f"RGB=({rgb[0]},{rgb[1]},{rgb[2]})  "
                f"HSV=({hsv[0]},{hsv[1]},{hsv[2]})")
        else:
            self._status.setText("")

    def refresh(self):
        res = self.controller.result
        if res is None:
            self.view.set_image(None)
            return
        idx = max(0, self.selector.currentIndex())
        if idx >= len(res.processors):
            idx = 0
        pr = res.processors[idx] if res.processors else None
        if pr is None:
            self.view.set_image(None)
            return
        out = pr.roi_bgr.copy()
        out[pr.mask == 0] = 0
        self.view.set_image(_bgr2rgb(out))


# ---------------------------------------------------------------------------
# 页面 5：后处理
# ---------------------------------------------------------------------------

class PostprocessPage(Page):
    def __init__(self, controller):
        super().__init__(controller)
        self.tutorial_panel = _make_tutorial("教程", [
            "# 操作",
            "- erodeSize：腐蚀，去孤立点、缩小物体、放大内部孔洞。",
            "- dilateSize：膨胀，填小孔、连接断裂、放大物体。",
            "- morphType：CLOSING 先膨胀后腐蚀（填边缘缺口）；OPENING 先腐蚀后膨胀（去噪）。",
            "- 右侧叠加显示所有 Processor：目标保留原色，非目标显示为黑色遮罩。",
            "# 什么是形态学操作",
            "- 形态学用“结构元素”（小方块/小圆盘）在二值图上前景上滑动，做腐蚀、膨胀等集合运算。",
            "# 腐蚀 Erode",
            "- 只有结构元素被前景完全覆盖时中心像素才保留，物体会“收缩”，细线和小噪点消失。",
            "# 膨胀 Dilate",
            "- 只要结构元素碰到前景，中心就置为前景，物体会“膨胀”，孔洞和断裂被填上。",
            "# 开 / 闭运算",
            "- 开运算 Opening = 先腐蚀后膨胀：去除小噪点、断开细连接。",
            "- 闭运算 Closing = 先膨胀后腐蚀：填小孔、连接邻近区域。",
            "# 使用建议",
            "- 噪声多、有孤立碎片 → 用 OPENING；目标有孔洞或边缘断裂 → 用 CLOSING。",
            "- 低分辨率下 erode / dilate 建议 2~4。",
        ])

        self.groups_vbox = QVBoxLayout()
        self.groups_vbox.addStretch(1)
        self._groups = []  # (index, erode, dilate, morph, contour)

        params = QWidget()
        pv = QVBoxLayout(params)
        pv.addLayout(self.groups_vbox)
        self.add_btn = QPushButton("添加 Processor")
        pv.addWidget(self.add_btn)
        self.add_btn.clicked.connect(lambda: self.controller.add_processor())

        self.view = ImageView()
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(self.view, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(_params_scroll(params), 1)
        lv.addWidget(self.tutorial_panel)

        _h_split(self, [left, right], [1, 3])

    def rebuild_params(self):
        _clear_layout(self.groups_vbox)
        self._groups = []
        for i, proc in enumerate(self.controller.processors):
            box = QGroupBox(f"Processor {i + 1}")
            v = QVBoxLayout(box)
            header = QHBoxLayout()
            header.addStretch(1)
            del_btn = QPushButton("删除")
            del_btn.setStyleSheet("color:#c33;")
            del_btn.clicked.connect(lambda _=False, idx=i: self.controller.remove_processor(idx))
            header.addWidget(del_btn)
            v.addLayout(header)

            erode = SliderSpin("erodeSize", 0, 31, proc.erode_size, integer=True)
            dilate = SliderSpin("dilateSize", 0, 31, proc.dilate_size, integer=True)
            morph = OptionBox("morphType", C.MORPH_TYPES)
            morph.set_value(proc.morph_type, silent=True)
            contour = OptionBox("contourMode", C.CONTOUR_MODES)
            contour.set_value(proc.contour_mode, silent=True)

            v.addWidget(erode)
            v.addWidget(dilate)
            v.addWidget(morph)
            v.addWidget(contour)

            self.groups_vbox.addWidget(box)
            self._groups.append((i, erode, dilate, morph, contour))

            for w in (erode, dilate):
                w.value_changed.connect(lambda _v, idx=i: self._on_slider(idx))
            morph.changed.connect(lambda _v, idx=i: self._on_option(idx))
            contour.changed.connect(lambda _v, idx=i: self._on_option(idx))
        self.groups_vbox.addStretch(1)

    def _on_slider(self, idx):
        _i, erode, dilate, _m, _c = self._groups[idx]
        proc = self.controller.processors[idx]
        proc.erode_size = int(erode.value())
        proc.dilate_size = int(dilate.value())
        self.controller.on_param_changed()

    def _on_option(self, idx):
        _i, _e, _d, morph, contour = self._groups[idx]
        proc = self.controller.processors[idx]
        proc.morph_type = morph.value()
        proc.contour_mode = contour.value()
        self.controller.on_param_changed()

    def refresh(self):
        res = self.controller.result
        if res is None:
            self.view.set_image(None)
            return
        H, W = res.merged_mask.shape[:2]
        x, y, w, h = _union_roi_rect(res.processors, W, H)
        base = res.downsampled[y:y + h, x:x + w]
        mask = res.merged_mask[y:y + h, x:x + w]
        out = base.copy()
        out[mask == 0] = 0
        self.view.set_image(_bgr2rgb(out))


# ---------------------------------------------------------------------------
# 页面 6：过滤排序 + 代码生成
# ---------------------------------------------------------------------------

class _FilterRuleRow(QWidget):
    # 各指标可用的滑条范围/步长（预览用，可继续在数值框精确输入）
    _CRITERION_RANGES = {
        "BY_CONTOUR_AREA": (0.0, 300000.0, 1.0),
        "BY_DENSITY": (0.0, 1.0, 0.01),
        "BY_ASPECT_RATIO": (0.0, 100.0, 0.01),
        "BY_ARC_LENGTH": (0.0, 20000.0, 1.0),
        "BY_CIRCULARITY": (0.0, 1.0, 0.01),
    }

    def __init__(self, rule, on_delete, on_change):
        super().__init__()
        self.rule = rule
        self.on_delete = on_delete
        self.on_change = on_change

        self.criterion = QComboBox()
        self.criterion.addItems(C.CRITERIA)
        self.criterion.setCurrentText(rule.criterion)
        self.criterion.setMinimumWidth(0)
        self.criterion.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.criterion.setMinimumContentsLength(1)

        lo, hi, step = self._CRITERION_RANGES.get(rule.criterion, self._CRITERION_RANGES["BY_CONTOUR_AREA"])
        self.min_slider = SliderSpin("min", lo, hi, rule.min_value, step=step, integer=False)
        self.max_slider = SliderSpin("max", lo, hi, rule.max_value, step=step, integer=False)

        self.del_btn = QPushButton("×")
        self.del_btn.setFixedWidth(28)
        self.del_btn.setStyleSheet("color:#c33;")

        # 纵向布局：第一行 指标下拉 + 删除，随后 min / max 各一行滑条
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.addWidget(self.criterion, 1)
        top.addWidget(self.del_btn)
        lay.addLayout(top)
        lay.addWidget(self.min_slider)
        lay.addWidget(self.max_slider)

        self.criterion.currentIndexChanged.connect(self._on_criterion)
        self.min_slider.value_changed.connect(self._update)
        self.max_slider.value_changed.connect(self._update)
        self.del_btn.clicked.connect(lambda: self.on_delete(self))

    def _on_criterion(self, *_a):
        self.rule.criterion = self.criterion.currentText()
        lo, hi, step = self._CRITERION_RANGES.get(self.rule.criterion, self._CRITERION_RANGES["BY_CONTOUR_AREA"])
        self.min_slider.set_range(lo, hi, step)
        self.max_slider.set_range(lo, hi, step)
        self.on_change()

    def _update(self, *_a):
        self.rule.criterion = self.criterion.currentText()
        self.rule.min_value = self.min_slider.value()
        self.rule.max_value = self.max_slider.value()
        self.on_change()


class FilterSortPage(Page):
    def __init__(self, controller):
        super().__init__(controller)
        self.tutorial_panel = _make_tutorial("教程", [
            "# 操作",
            "- 每个 Processor 有独立过滤规则（指标 + min/max）。",
            "- 全局排序/拟合模式所有 Processor 共享。",
            "- 右侧叠加显示所有 Processor 的色块轮廓与拟合框。",
            "- 悬停色块显示黄色粗轮廓；未过滤按排名绿→青→蓝→紫，被过滤则显示灰线。",
            "# 什么是色块与连通域",
            "- 二值化后连成一片的前景像素称为连通域 / 色块（blob），每个 blob 即一个候选目标。",
            "# 常用指标",
            "- 轮廓面积：blob 内部像素数，越大目标越大。",
            "- 密度：轮廓面积 ÷ 凸包面积，越接近 1 越“实心”。",
            "- 长宽比：拟合矩形长边/短边，正方形≈1。",
            "- 周长（弧长）：轮廓边界长度。",
            "- 圆形度：越接近 1 越像正圆，越小越不圆。",
            "# 过滤 vs 排序",
            "- filterByCriteria 按指标范围剔除不合格 blob（如面积太小、不够圆）。",
            "- sortByCriteria 按指标排序，配合 DESCENDING 让最大/最圆的排第一。",
            "# 拟合形状",
            "- boxFit：包裹轮廓的最小旋转矩形，给出中心、宽高、角度。",
            "- circleFit：包裹轮廓的最小圆，给出中心与半径。",
        ])

        self.groups_vbox = QVBoxLayout()
        self.groups_vbox.addStretch(1)
        self._groups = []  # (index, rules_vbox)

        self.sort_criterion = OptionBox("排序条件", C.CRITERIA)
        self.sort_criterion.set_value(controller.global_cfg.sort_criterion, silent=True)
        self.sort_order = OptionBox("排序方向", C.SORT_ORDERS)
        self.sort_order.set_value(controller.global_cfg.sort_order, silent=True)
        self.fit_mode = OptionBox("拟合模式", C.FIT_MODES)
        self.fit_mode.set_value(controller.global_cfg.fit_mode, silent=True)

        self.code_box = QPlainTextEdit()
        self.code_box.setReadOnly(True)
        self.code_box.setFont(QFont("Consolas", 9))
        self.code_box.setStyleSheet("background:#1e1e1e; color:#d4d4d4;")
        self.code_box.setMinimumHeight(240)

        params = QWidget()
        pv = QVBoxLayout(params)
        pv.addLayout(self.groups_vbox)
        self.add_btn = QPushButton("添加 Processor")
        pv.addWidget(self.add_btn)
        self.add_btn.clicked.connect(lambda: self.controller.add_processor())

        gl = QGroupBox("全局排序 / 拟合")
        gv = QVBoxLayout(gl)
        gv.addWidget(self.sort_criterion)
        gv.addWidget(self.sort_order)
        gv.addWidget(self.fit_mode)
        pv.addWidget(gl)

        self.view = ImageView()
        self.view.hover_moved.connect(self._on_hover)
        self.view.hover_left.connect(self._on_hover_leave)
        self._status = QLabel("")
        self._status.setStyleSheet("color:#555;")
        self._hover_pos = None
        self._last_code = None

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(self.view, 1)
        rv.addWidget(self._status)

        left = QWidget()
        _v_split(left, [_params_scroll(params), self.code_box, self.tutorial_panel], [3, 2, 2])

        _h_split(self, [left, right], [1, 3])

        self.sort_criterion.changed.connect(self._on_global)
        self.sort_order.changed.connect(self._on_global)
        self.fit_mode.changed.connect(self._on_global)

    def _on_global(self, _v):
        gc = self.controller.global_cfg
        gc.sort_criterion = self.sort_criterion.value()
        gc.sort_order = self.sort_order.value()
        gc.fit_mode = self.fit_mode.value()
        self.controller.on_param_changed()

    def rebuild_params(self):
        gc = self.controller.global_cfg
        self.sort_criterion.set_value(gc.sort_criterion, silent=True)
        self.sort_order.set_value(gc.sort_order, silent=True)
        self.fit_mode.set_value(gc.fit_mode, silent=True)
        _clear_layout(self.groups_vbox)
        self._groups = []
        for i, proc in enumerate(self.controller.processors):
            box = QGroupBox(f"Processor {i + 1}")
            v = QVBoxLayout(box)
            header = QHBoxLayout()
            header.addStretch(1)
            del_btn = QPushButton("删除")
            del_btn.setStyleSheet("color:#c33;")
            del_btn.clicked.connect(lambda _=False, idx=i: self.controller.remove_processor(idx))
            header.addWidget(del_btn)
            v.addLayout(header)

            rules_vbox = QVBoxLayout()
            v.addLayout(rules_vbox)
            add_rule_btn = QPushButton("+ 添加过滤规则")
            v.addWidget(add_rule_btn)

            self.groups_vbox.addWidget(box)
            self._groups.append((i, rules_vbox))

            for rule in proc.filter_rules:
                self._add_rule_row(i, rules_vbox, rule)
            add_rule_btn.clicked.connect(lambda _=False, idx=i: self._add_rule(idx))
        self.groups_vbox.addStretch(1)

    def _add_rule(self, idx):
        proc = self.controller.processors[idx]
        rule = C.FilterRule()
        proc.filter_rules.append(rule)
        _i, rules_vbox = self._groups[idx]
        self._add_rule_row(idx, rules_vbox, rule)
        self.controller.on_param_changed()

    def _add_rule_row(self, idx, rules_vbox, rule):
        row = _FilterRuleRow(
            rule,
            on_delete=lambda r: self._del_rule(idx, r),
            on_change=lambda: self.controller.on_param_changed(),
        )
        rules_vbox.addWidget(row)

    def _del_rule(self, idx, row):
        proc = self.controller.processors[idx]
        if row.rule in proc.filter_rules:
            proc.filter_rules.remove(row.rule)
        row.setParent(None)
        row.deleteLater()
        self.controller.on_param_changed()

    # --- 预览 ---
    def _on_hover(self, x, y):
        self._hover_pos = (x, y)
        self.refresh()

    def _on_hover_leave(self):
        self._hover_pos = None
        self.refresh()

    def _hit_blob(self, x, y):
        res = self.controller.result
        if res is None:
            return None
        H, W = res.downsampled.shape[:2]
        ox, oy, _w, _h = _union_roi_rect(res.processors, W, H)
        for b in res.merged_blobs:
            if not b.filtered and cv2.pointPolygonTest(b.contour, (x + ox, y + oy), False) >= 0:
                return b
        return None

    def _top_blob(self):
        res = self.controller.result
        if res is None:
            return None
        for b in res.merged_blobs:
            if not b.filtered and b.rank == 1:
                return b
        for b in res.merged_blobs:
            if not b.filtered:
                return b
        return None

    def _update_status(self, b):
        if b is None:
            res = self.controller.result
            total = len(res.merged_blobs) if res else 0
            self._status.setText(f"排名 -/{total}")
            return
        res = self.controller.result
        total = len(res.merged_blobs)
        self._status.setText(
            f"排名 {b.rank}/{total}  面积={b.area:.0f}  密度={b.density:.3f}  "
            f"长宽比={b.aspect_ratio:.2f}  圆形度={b.circularity:.3f}  "
            f"中心=({b.cx:.1f},{b.cy:.1f})")

    def _line_width(self, base: int) -> int:
        """线条宽度随降采样率等比例缩小，保持视觉上与原图比例一致。"""
        r = max(1, int(self.controller.global_cfg.downsample_rate))
        return max(1, int(round(base / r)))

    def _active_blob(self):
        """当前高亮对象：悬停色块优先，否则默认显示排名第一的色块。"""
        if self._hover_pos is not None:
            hit = self._hit_blob(*self._hover_pos)
            if hit is not None:
                return hit
        return self._top_blob()

    def _build_view(self):
        res = self.controller.result
        H, W = res.downsampled.shape[:2]
        base = res.processors[0].denoised if res.processors else res.downsampled
        rgb = _bgr2rgb(base).copy()

        x, y, w, h = _union_roi_rect(res.processors, W, H)
        # mask 叠加（半透明绿色高亮）
        overlay = rgb.copy()
        reg = overlay[y:y + h, x:x + w]
        merged_reg = res.merged_mask[y:y + h, x:x + w]
        reg[merged_reg > 0] = (reg[merged_reg > 0] * 0.5 + np.array([0, 200, 0]) * 0.5).astype(np.uint8)
        cv2.addWeighted(overlay, 0.55, rgb, 0.45, 0, rgb)

        active = self._active_blob()
        total = sum(1 for b in res.merged_blobs if not b.filtered)
        max_area = max((b.area for b in res.merged_blobs if not b.filtered), default=0.0)
        # 高亮当前色块（悬停或默认排名第一），黄色粗轮廓 + 拟合形状
        if active is not None:
            cv2.drawContours(rgb, [active.contour], -1, (0, 255, 255), self._line_width(3))
            self._draw_fit(rgb, active)
        for b in res.merged_blobs:
            if b is active:
                continue
            if b.filtered:
                cv2.drawContours(rgb, [b.contour], -1, (160, 160, 160), self._line_width(1))
            else:
                color = _rank_color(b.rank, total)
                cv2.drawContours(rgb, [b.contour], -1, color, self._line_width(2))
                # 排名数字：高亮色块不标、面积过小不标，其余标注在拟合轮廓中心
                if b.area >= _MIN_LABEL_AREA_RATIO * max_area:
                    self._draw_rank_label(rgb, b, color)

        # 裁剪到 ROI 并集
        return rgb[y:y + h, x:x + w]

    def _draw_fit(self, rgb, b):
        t = self._line_width(2)
        # 中心点半径：随降采样率缩放，但保证最小可见
        dot_r = max(2, self._line_width(3))
        if self.controller.global_cfg.fit_mode == C.FIT_BOX:
            pts = cv2.boxPoints(b.rect)
            pts = np.round(pts).astype(np.int32)
            cv2.polylines(rgb, [pts], True, (0, 0, 255), t)
            cx, cy = b.rect[0]
        else:
            cx, cy, r = b.circle
            cv2.circle(rgb, (int(cx), int(cy)), int(r), (0, 0, 255), t)
        # 一并绘出中心点（实心红点）
        cv2.circle(rgb, (int(cx), int(cy)), dot_r, (0, 0, 255), -1)

    def _draw_rank_label(self, rgb, b, color):
        """在拟合轮廓中心标注排名数字（黑色描边保证对比度 + 排名色填充）。"""
        if self.controller.global_cfg.fit_mode == C.FIT_BOX:
            cx, cy = b.rect[0]
        else:
            cx, cy, _ = b.circle
        text = str(b.rank)
        org = (int(cx) - 6, int(cy) + 6)
        cv2.putText(rgb, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 0, 0), self._line_width(3), cv2.LINE_AA)
        cv2.putText(rgb, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    color, self._line_width(1), cv2.LINE_AA)

    def refresh(self):
        res = self.controller.result
        if res is None:
            self.view.set_image(None)
            self._set_code("// 请先选择图片或打开摄像头")
            return
        self.view.set_image(self._build_view())
        self._update_status(self._active_blob())
        ds = self.controller.ds_size()
        size = ds if ds is not None else (320, 240)
        self._set_code(codegen.generate_java(self.controller.global_cfg,
                                             self.controller.processors, size))

    def _set_code(self, text):
        # 若代码未变化则跳过 setPlainText，避免在实时刷新时反复重置滚动位置
        if text == self._last_code:
            return
        sb = self.code_box.verticalScrollBar()
        pos = sb.value()
        self._last_code = text
        self.code_box.setPlainText(text)
        sb.setValue(pos)


# 页面索引映射
PAGE_CLASSES = [UploadPage, PreprocessPage, RoiPage, DenoisePage, ColorRangePage, PostprocessPage, FilterSortPage]