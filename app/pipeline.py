"""图像处理管道：预处理 → 降采样 → 逐 Processor(降噪/ROI/二值化/后处理/色块提取/过滤) → 合并。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import cv2
import numpy as np

from . import config as C


def _odd(n: int, minv: int = 1) -> int:
    n = int(n)
    if n < minv:
        n = minv
    if n % 2 == 0:
        n += 1
    return n


@dataclass
class Blob:
    cx: float
    cy: float
    area: float
    density: float
    aspect_ratio: float
    arc_length: float
    circularity: float
    rect: tuple  # ((cx, cy), (w, h), angle) —— 全图（降采样）坐标
    circle: tuple  # (cx, cy, r)
    contour: np.ndarray  # 全图（降采样）坐标的轮廓点
    filtered: bool = False
    rank: int = 0
    processor_index: int = 0


@dataclass
class ProcessorResult:
    index: int
    denoised: np.ndarray  # 降噪后全图（降采样坐标）
    roi_rect: tuple  # (x, y, w, h)
    roi_bgr: np.ndarray  # ROI 裁剪 (BGR)
    mask: np.ndarray  # ROI 尺寸的二值 mask
    post_mask: np.ndarray  # ROI 尺寸的后处理 mask
    full_mask: np.ndarray  # 全图尺寸（ROI 位置放置 post_mask）
    blobs: List[Blob]


@dataclass
class PipelineResult:
    original: np.ndarray  # 最长边缩放到 640（BGR）
    downsampled: np.ndarray  # 降采样后（BGR）
    processors: List[ProcessorResult]
    merged_mask: np.ndarray
    merged_blobs: List[Blob]


# ---------------------------------------------------------------------------
# 图像预处理
# ---------------------------------------------------------------------------

def preprocess(image_bgr: np.ndarray) -> np.ndarray:
    """最长边缩放至 640（保持宽高比）。"""
    h, w = image_bgr.shape[:2]
    m = max(h, w)
    if m > 640:
        scale = 640.0 / m
        image_bgr = cv2.resize(
            image_bgr,
            (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    return image_bgr


def downsample(image_bgr: np.ndarray, factor: int) -> np.ndarray:
    if factor <= 1:
        return image_bgr.copy()
    h, w = image_bgr.shape[:2]
    return cv2.resize(image_bgr, (max(1, w // factor), max(1, h // factor)), interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------------------
# ROI
# ---------------------------------------------------------------------------

def compute_roi_rect(proc: C.ProcessorConfig, W: int, H: int) -> Tuple[int, int, int, int]:
    """返回 (x, y, w, h) 像素 ROI；无效时回退整帧。"""
    if proc.roi_mode == C.ROI_NORMALIZED:
        u_min, v_max, u_max, v_min = proc.roi_norm
        x_left = int(round((u_min + 1.0) / 2.0 * W))
        x_right = int(round((u_max + 1.0) / 2.0 * W))
        y_top = int(round((1.0 - v_max) / 2.0 * H))  # v=+1 顶部 -> y 较小
        y_bottom = int(round((1.0 - v_min) / 2.0 * H))
        x_left = max(0, min(W, x_left))
        x_right = max(0, min(W, x_right))
        y_top = max(0, min(H, y_top))
        y_bottom = max(0, min(H, y_bottom))
        if x_right <= x_left or y_bottom <= y_top:
            return (0, 0, W, H)
        return (x_left, y_top, x_right - x_left, y_bottom - y_top)

    return (0, 0, W, H)  # 整帧


# ---------------------------------------------------------------------------
# 二值化
# ---------------------------------------------------------------------------

def binarize(bgr: np.ndarray, proc: C.ProcessorConfig) -> np.ndarray:
    cs = proc.color_space
    if cs == "YCrCb":
        conv = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    elif cs == "HSV":
        conv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    else:
        conv = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    lower = [int(v) for v in proc.lower]
    upper = [int(v) for v in proc.upper]

    low = np.array([min(lower[i], upper[i]) for i in range(3)], np.uint8)
    up = np.array([max(lower[i], upper[i]) for i in range(3)], np.uint8)
    return cv2.inRange(conv, low, up)


# ---------------------------------------------------------------------------
# 后处理（形态学）
# ---------------------------------------------------------------------------

def postprocess(mask: np.ndarray, proc: C.ProcessorConfig) -> np.ndarray:
    m = mask
    e = proc.erode_size
    d = proc.dilate_size

    def _erode():
        nonlocal m
        if e > 0:
            m = cv2.erode(m, np.ones((e, e), np.uint8))

    def _dilate():
        nonlocal m
        if d > 0:
            m = cv2.dilate(m, np.ones((d, d), np.uint8))

    if proc.morph_type == C.MORPH_OPENING:  # 先腐蚀后膨胀
        _erode()
        _dilate()
    else:  # CLOSING 先膨胀后腐蚀
        _dilate()
        _erode()
    return m


# ---------------------------------------------------------------------------
# 色块提取
# ---------------------------------------------------------------------------

_CRIT_MAP = {
    "BY_CONTOUR_AREA": lambda b: b.area,
    "BY_DENSITY": lambda b: b.density,
    "BY_ASPECT_RATIO": lambda b: b.aspect_ratio,
    "BY_ARC_LENGTH": lambda b: b.arc_length,
    "BY_CIRCULARITY": lambda b: b.circularity,
}


def _metric(blob: Blob, criterion: str) -> float:
    return _CRIT_MAP[criterion](blob)


def extract_blobs(mask: np.ndarray, contour_mode: str, off_x: int, off_y: int) -> List[Blob]:
    mode = cv2.RETR_EXTERNAL if contour_mode == C.CONTOUR_EXTERNAL else cv2.RETR_LIST
    contours, _ = cv2.findContours(mask, mode, cv2.CHAIN_APPROX_SIMPLE)

    blobs: List[Blob] = []
    for c in contours:
        area = float(cv2.contourArea(c))
        if area < 1:
            continue
        hull = cv2.convexHull(c)
        hull_area = float(cv2.contourArea(hull))
        density = area / hull_area if hull_area > 0 else 0.0
        arc = float(cv2.arcLength(c, True))
        circularity = (4.0 * np.pi * area) / (arc * arc) if arc > 0 else 0.0

        (rx, ry), (rw, rh), ang = cv2.minAreaRect(c)
        if rw == 0 or rh == 0:
            aspect = 1.0
        else:
            aspect = max(rw, rh) / min(rw, rh)

        (ccx, ccy), rad = cv2.minEnclosingCircle(c)

        contour_full = c + np.array([off_x, off_y], dtype=np.int32)

        blobs.append(Blob(
            cx=rx + off_x,
            cy=ry + off_y,
            area=area,
            density=density,
            aspect_ratio=aspect,
            arc_length=arc,
            circularity=circularity,
            rect=((rx + off_x, ry + off_y), (rw, rh), ang),
            circle=(ccx + off_x, ccy + off_y, rad),
            contour=contour_full,
        ))

    blobs.sort(key=lambda b: b.area, reverse=True)
    return blobs


def apply_filters(blobs: List[Blob], rules: List[C.FilterRule]) -> List[Blob]:
    """应用过滤规则；空规则不重置 filtered 标志。"""
    if not rules:
        return blobs
    for b in blobs:
        b.filtered = False
        for rule in rules:
            val = _metric(b, rule.criterion)
            if val < rule.min_value or val > rule.max_value:
                b.filtered = True
                break
    return blobs


def sort_blobs(blobs: List[Blob], criterion: str, order: str) -> List[Blob]:
    rev = order == "DESCENDING"
    blobs.sort(key=lambda b: _metric(b, criterion), reverse=rev)
    return blobs


def _assign_rank(blobs: List[Blob]) -> None:
    rank = 0
    for b in blobs:
        if not b.filtered:
            rank += 1
            b.rank = rank
        else:
            b.rank = 0


# ---------------------------------------------------------------------------
# 单 Processor 处理
# ---------------------------------------------------------------------------

def _process_one(ds: np.ndarray, proc: C.ProcessorConfig, index: int) -> ProcessorResult:
    H, W = ds.shape[:2]

    b = _odd(proc.blur_size)
    denoised = ds if b <= 1 else cv2.GaussianBlur(ds, (b, b), 0)

    roi_rect = compute_roi_rect(proc, W, H)
    x, y, w, h = roi_rect
    roi_bgr = denoised[y:y + h, x:x + w]

    mask = binarize(roi_bgr, proc)
    post_mask = postprocess(mask, proc)

    full_mask = np.zeros((H, W), np.uint8)
    full_mask[y:y + h, x:x + w] = post_mask

    blobs = extract_blobs(post_mask, proc.contour_mode, x, y)
    apply_filters(blobs, proc.filter_rules)

    return ProcessorResult(
        index=index,
        denoised=denoised,
        roi_rect=roi_rect,
        roi_bgr=roi_bgr,
        mask=mask,
        post_mask=post_mask,
        full_mask=full_mask,
        blobs=blobs,
    )


# ---------------------------------------------------------------------------
# 完整管道
# ---------------------------------------------------------------------------

def run_pipeline(image_bgr: np.ndarray, global_cfg: C.GlobalConfig,
                 processors: List[C.ProcessorConfig]) -> PipelineResult:
    original = preprocess(image_bgr)
    ds = downsample(original, global_cfg.downsample_rate)
    H, W = ds.shape[:2]

    proc_results: List[ProcessorResult] = []
    all_blobs: List[Blob] = []
    for i, proc in enumerate(processors):
        pr = _process_one(ds, proc, i)
        proc_results.append(pr)
        for b in pr.blobs:
            b.processor_index = i
        all_blobs.extend(pr.blobs)

    merged_mask = np.zeros((H, W), np.uint8)
    for pr in proc_results:
        merged_mask = cv2.bitwise_or(merged_mask, pr.full_mask)

    sort_blobs(all_blobs, global_cfg.sort_criterion, global_cfg.sort_order)
    _assign_rank(all_blobs)

    return PipelineResult(
        original=original,
        downsampled=ds,
        processors=proc_results,
        merged_mask=merged_mask,
        merged_blobs=all_blobs,
    )


def downsample_size(image_bgr: np.ndarray, factor: int) -> Tuple[int, int]:
    """返回降采样后的 (宽, 高)，供 ROI 滑条范围与代码生成使用。"""
    scaled = preprocess(image_bgr)
    d = downsample(scaled, factor)
    h, w = d.shape[:2]
    return w, h