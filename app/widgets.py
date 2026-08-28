"""可复用的 Qt 控件：SliderSpin、OptionBox、ThresholdGroupCard、ImageView。"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import config as C


# ---------------------------------------------------------------------------
# SliderSpin：标签 + 滑条 + 输入框，双向联动
# ---------------------------------------------------------------------------

class SliderSpin(QWidget):
    value_changed = Signal(object)  # int 或 float

    def __init__(self, label: str, minimum, maximum, default, step=1,
                 integer: bool = True, odd: bool = False, parent=None):
        super().__init__(parent)
        self._min = minimum
        self._max = maximum
        self._integer = integer
        self._odd = odd
        # 奇数模式下步进 2，避免“偶数→奇数”归一化导致数值框下键卡死不回退
        if odd:
            step = 2
        self._step = step
        self._updating = False

        self._label = QLabel(label)
        self._label.setMinimumWidth(56)

        if integer:
            self.spin = QSpinBox()
            self.spin.setRange(int(minimum), int(maximum))
            self.spin.setSingleStep(int(step))
        else:
            self.spin = QDoubleSpinBox()
            self.spin.setRange(float(minimum), float(maximum))
            self.spin.setSingleStep(step)
            self.spin.setDecimals(2)

        slider_steps = max(1, int(round((maximum - minimum) / step)))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, slider_steps)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._label)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)

        self.slider.valueChanged.connect(self._on_slider)
        self.spin.valueChanged.connect(self._on_spin)
        self.set_value(default)

    # --- 值 <-> 滑条 ---
    def _value_to_slider(self, v) -> int:
        return int(round((v - self._min) / self._step))

    def _slider_to_value(self, s) -> float:
        return self._min + s * self._step

    def _normalize(self, v):
        if self._integer:
            v = int(round(v))
        v = max(self._min, min(self._max, v))
        # 奇数强制：0 表示“关闭”需保留，其余正偶数 +1
        if self._integer and self._odd and v != 0 and v % 2 == 0:
            v += 1
            if v > self._max:
                v -= 2
        return v

    # --- 信号处理 ---
    def _on_slider(self, s: int):
        if self._updating:
            return
        self._sync_from(self._slider_to_value(s))
        self.value_changed.emit(self.value())

    def _on_spin(self, v):
        if self._updating:
            return
        self._sync_from(v)
        self.value_changed.emit(self.value())

    def _sync_from(self, v):
        v = self._normalize(v)
        self._updating = True
        self.spin.setValue(v)
        self.slider.setValue(self._value_to_slider(v))
        self._updating = False

    # --- 对外接口 ---
    def set_range(self, minimum, maximum, step=None, value=None):
        """动态更新数值范围与步长（供过滤规则等按指标切换范围使用）。"""
        if step is None:
            step = self._step
        self._min = minimum
        self._max = maximum
        self._step = step
        if self._integer:
            self.spin.setRange(int(minimum), int(maximum))
            self.spin.setSingleStep(int(step))
        else:
            self.spin.setRange(float(minimum), float(maximum))
            self.spin.setSingleStep(step)
        steps = max(1, int(round((maximum - minimum) / step)))
        self.slider.setRange(0, steps)
        cur = self.spin.value() if value is None else value
        self.set_value_silent(cur)

    def set_value(self, v):
        """设置值并触发 value_changed 信号。"""
        self._sync_from(v)
        self.value_changed.emit(self.value())

    def set_value_silent(self, v):
        """设置值但不触发信号，仅同步控件显示。"""
        self._sync_from(v)

    def value(self):
        return self.spin.value()


# ---------------------------------------------------------------------------
# OptionBox：标签 + 下拉框
# ---------------------------------------------------------------------------

class OptionBox(QWidget):
    changed = Signal(object)

    def __init__(self, label: str, options, parent=None):
        super().__init__(parent)
        self._label = QLabel(label)
        self._label.setMinimumWidth(56)
        self.combo = QComboBox()
        self.combo.addItems(options)
        # 允许下拉框在窄面板中收缩，避免被长选项文本撑宽导致右侧裁剪
        self.combo.setMinimumWidth(0)
        self.combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setMinimumContentsLength(1)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._label)
        lay.addWidget(self.combo, 1)
        self.combo.currentIndexChanged.connect(lambda _i: self.changed.emit(self.value()))

    def set_value(self, v, silent: bool = False):
        idx = self.combo.findText(str(v))
        if idx < 0:
            idx = 0
        if silent:
            self.combo.blockSignals(True)
            self.combo.setCurrentIndex(idx)
            self.combo.blockSignals(False)
        else:
            self.combo.setCurrentIndex(idx)

    def value(self) -> str:
        return self.combo.currentText()


# ---------------------------------------------------------------------------
# ThresholdGroupCard：色彩空间 + 6 个阈值滑条
# ---------------------------------------------------------------------------

class ThresholdGroupCard(QGroupBox):
    changed = Signal()

    def __init__(self, title: str = "色范围", parent=None):
        super().__init__(title, parent)
        self._space = OptionBox("色彩空间", C.COLOR_SPACES)
        self._lower: list[SliderSpin] = []
        self._upper: list[SliderSpin] = []

        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        lay.addWidget(self._space)

        for i in range(3):
            lo = SliderSpin("下限", 0, 255, 0, integer=True)
            hi = SliderSpin("上限", 0, 255, 255, integer=True)
            self._lower.append(lo)
            self._upper.append(hi)
            # 上下界各占一行，保证每个滑条都有足够宽度
            lay.addWidget(lo)
            lay.addWidget(hi)

        # 中文通道名显示在标签上
        self._space.changed.connect(self._on_space_changed)
        for w in self._lower + self._upper:
            w.value_changed.connect(lambda _v: self.changed.emit())

    def _on_space_changed(self, cs: str):
        self._apply_channel_meta(cs)
        self.changed.emit()

    def _apply_channel_meta(self, cs: str):
        meta = C.COLOR_SPACE_META[cs]
        chans = meta["channels"]
        ranges = meta["ranges"]
        for i in range(3):
            lo, hi = ranges[i]
            self._lower[i]._label.setText(f"下限 {chans[i]}")
            self._upper[i]._label.setText(f"上限 {chans[i]}")
            self._lower[i].set_range(lo, hi)
            self._upper[i].set_range(lo, hi)

    def load(self, proc: C.ProcessorConfig):
        self._space.set_value(proc.color_space, silent=True)
        self._apply_channel_meta(proc.color_space)
        for i in range(3):
            lo = proc.lower[i] if i < len(proc.lower) else 0
            hi = proc.upper[i] if i < len(proc.upper) else 255
            self._lower[i].set_value_silent(lo)
            self._upper[i].set_value_silent(hi)

    def write(self, proc: C.ProcessorConfig):
        proc.color_space = self._space.value()
        proc.lower = [int(self._lower[i].value()) for i in range(3)]
        proc.upper = [int(self._upper[i].value()) for i in range(3)]

    def color_space(self) -> str:
        return self._space.value()


# ---------------------------------------------------------------------------
# ImageView：按 contain 方式居中显示图像（letterbox 留白），发射原始图像坐标
# ---------------------------------------------------------------------------

class ImageView(QWidget):
    hover_moved = Signal(int, int)  # 图像坐标 (x 列, y 行)
    hover_left = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rgb: np.ndarray | None = None
        self._pixmap: QPixmap | None = None
        self._scale = 1.0
        self._ox = 0.0
        self._oy = 0.0
        self.setMouseTracking(True)
        self.setMinimumSize(320, 240)

    def set_image(self, rgb: np.ndarray | None):
        if rgb is None:
            self._rgb = None
            self._pixmap = None
        else:
            arr = np.ascontiguousarray(rgb)
            h, w = arr.shape[:2]
            qimg = QImage(arr.data, w, h, arr.strides[0], QImage.Format_RGB888)
            self._rgb = arr
            self._pixmap = QPixmap.fromImage(qimg.copy())
        self._recompute()
        self.update()

    def has_image(self) -> bool:
        return self._pixmap is not None

    def image_shape(self):
        return None if self._rgb is None else self._rgb.shape[:2]

    def _recompute(self):
        if self._pixmap is None:
            self._scale = 1.0
            self._ox = self._oy = 0.0
            return
        w, h = self.width(), self.height()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        if pw <= 0 or ph <= 0 or w <= 0 or h <= 0:
            return
        self._scale = min(w / pw, h / ph)
        self._ox = (w - pw * self._scale) / 2.0
        self._oy = (h - ph * self._scale) / 2.0

    def resizeEvent(self, e):
        self._recompute()
        super().resizeEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(18, 18, 20))
        if self._pixmap is None:
            p.setPen(QColor(140, 140, 140))
            p.drawText(self.rect(), Qt.AlignCenter, "无画面")
            return
        p.drawPixmap(int(self._ox), int(self._oy),
                     int(self._pixmap.width() * self._scale),
                     int(self._pixmap.height() * self._scale),
                     self._pixmap)

    def mouseMoveEvent(self, e):
        if self._rgb is None or self._scale <= 0:
            return
        x = (e.position().x() - self._ox) / self._scale
        y = (e.position().y() - self._oy) / self._scale
        h, w = self._rgb.shape[:2]
        if 0 <= x < w and 0 <= y < h:
            self.hover_moved.emit(int(x), int(y))
        else:
            self.hover_left.emit()

    def leaveEvent(self, e):
        self.hover_left.emit()