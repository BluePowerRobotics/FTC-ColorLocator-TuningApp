"""配置模型：Processor 参数、全局默认模板、预定义颜色、色空间元数据。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import List

# ---------------------------------------------------------------------------
# 色彩空间
# ---------------------------------------------------------------------------

COLOR_SPACES = ["YCrCb", "HSV", "RGB"]

# 各色彩空间的通道名与范围（8 位图）。HSV 的 H 为 0~180（覆盖 OpenCV 0~179），其余 0~255。
COLOR_SPACE_META = {
    "YCrCb": {"channels": ["Y", "Cr", "Cb"], "ranges": [(0, 255), (0, 255), (0, 255)]},
    "HSV": {"channels": ["H", "S", "V"], "ranges": [(0, 180), (0, 255), (0, 255)]},
    "RGB": {"channels": ["R", "G", "B"], "ranges": [(0, 255), (0, 255), (0, 255)]},
}

# 预定义颜色（YCrCb，下界, 上界）。
# 说明：生成 Java 代码时预定义颜色直接引用 ColorRange.X 常量，机器人端使用 FTC 官方标定值；
# 这里给出近似值仅用于桌面端预览，如需精确匹配可在此统一调整。
PREDEFINED_COLORS = {
    "自定义": ("YCrCb", (0, 0, 0), (255, 255, 255)),
    "RED": ("YCrCb", (150, 150, 0), (255, 255, 128)),
    "BLUE": ("YCrCb", (0, 0, 128), (128, 128, 255)),
    "YELLOW": ("YCrCb", (150, 100, 0), (255, 170, 130)),
    "GREEN": ("YCrCb", (0, 100, 0), (128, 170, 130)),
    "ARTIFACT_GREEN": ("YCrCb", (60, 60, 60), (150, 140, 140)),
    "ARTIFACT_PURPLE": ("YCrCb", (90, 130, 130), (170, 200, 200)),
}

PREDEFINED_NAMES = list(PREDEFINED_COLORS.keys())

# ---------------------------------------------------------------------------
# ROI
# ---------------------------------------------------------------------------

ROI_ENTIRE = "整帧"
ROI_NORMALIZED = "归一化坐标"
ROI_MODES = [ROI_ENTIRE, ROI_NORMALIZED]

# ---------------------------------------------------------------------------
# 形态学 / 轮廓 / 指标 / 排序 / 拟合
# ---------------------------------------------------------------------------

MORPH_CLOSING = "CLOSING"
MORPH_OPENING = "OPENING"
MORPH_TYPES = [MORPH_CLOSING, MORPH_OPENING]

CONTOUR_EXTERNAL = "EXTERNAL_ONLY"
CONTOUR_ALL = "ALL_FLATTENED_HIERARCHY"
CONTOUR_MODES = [CONTOUR_EXTERNAL, CONTOUR_ALL]

CRITERIA = [
    "BY_CONTOUR_AREA",
    "BY_DENSITY",
    "BY_ASPECT_RATIO",
    "BY_ARC_LENGTH",
    "BY_CIRCULARITY",
]

SORT_ORDERS = ["DESCENDING", "ASCENDING"]

FIT_BOX = "boxFit"
FIT_CIRCLE = "circleFit"
FIT_MODES = [FIT_BOX, FIT_CIRCLE]


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class FilterRule:
    criterion: str = "BY_CONTOUR_AREA"
    min_value: float = 0.0
    max_value: float = 0.0


@dataclass
class ProcessorConfig:
    name: str = "Processor 1"
    # ROI
    roi_mode: str = ROI_ENTIRE
    roi_norm: List[float] = field(default_factory=lambda: [-1.0, 1.0, 1.0, -1.0])  # uMin, vMax, uMax, vMin
    # 降噪
    blur_size: int = 5
    # 色范围
    preset: str = "自定义"
    color_space: str = "YCrCb"
    lower: List[int] = field(default_factory=lambda: [0, 0, 0])
    upper: List[int] = field(default_factory=lambda: [255, 255, 255])
    # 后处理
    erode_size: int = 0
    dilate_size: int = 0
    morph_type: str = MORPH_CLOSING
    contour_mode: str = CONTOUR_EXTERNAL
    # 过滤
    filter_rules: List[FilterRule] = field(default_factory=list)

    def clone(self) -> "ProcessorConfig":
        return replace(
            self,
            roi_norm=list(self.roi_norm),
            lower=list(self.lower),
            upper=list(self.upper),
            filter_rules=[replace(r) for r in self.filter_rules],
        )


def default_processor(index: int = 1) -> ProcessorConfig:
    """全局默认 Processor 模板（新增处理器使用）。"""
    space, lower, upper = PREDEFINED_COLORS["自定义"]
    return ProcessorConfig(
        name=f"Processor {index}",
        preset="自定义",
        color_space=space,
        lower=list(lower),
        upper=list(upper),
    )


@dataclass
class GlobalConfig:
    downsample_rate: int = 1  # 1~8
    teaching_mode: bool = False  # 教程面板（默认关闭）
    page_index: int = 0
    sort_criterion: str = "BY_CONTOUR_AREA"
    sort_order: str = "DESCENDING"
    fit_mode: str = FIT_CIRCLE