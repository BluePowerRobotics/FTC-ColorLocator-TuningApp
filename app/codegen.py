"""生成对应的 FTC Java 代码（ColorBlobLocatorProcessor）。"""

from __future__ import annotations

from typing import List, Tuple

from . import config as C


def _fmt(v: float) -> str:
    """把浮点格式化为简洁字符串，避免多余尾零。"""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    if s in ("", "-0"):
        return "0"
    return s


def _color_range(proc: C.ProcessorConfig) -> str:
    if proc.preset != "自定义":
        return f"ColorRange.{proc.preset}"
    return (
        f"new ColorRange(ColorSpace.{proc.color_space},\n"
        f"                                new Scalar({proc.lower[0]}, {proc.lower[1]}, {proc.lower[2]}),\n"
        f"                                new Scalar({proc.upper[0]}, {proc.upper[1]}, {proc.upper[2]}))"
    )


def _roi(proc: C.ProcessorConfig) -> str:
    if proc.roi_mode == C.ROI_ENTIRE:
        return "ImageRegion.entireFrame()"
    u_min, v_max, u_max, v_min = proc.roi_norm
    return (
        f"ImageRegion.asUnityCenterCoordinates({_fmt(u_min)}, {_fmt(v_min)}, "
        f"{_fmt(u_max)}, {_fmt(v_max)})"
    )


def _builder(proc: C.ProcessorConfig, index: int) -> List[str]:
    name = f"proc{index}"
    lines = [
        f"        ColorBlobLocatorProcessor {name} = new ColorBlobLocatorProcessor.Builder()",
        f"                .setTargetColorRange({_color_range(proc)})",
        f"                .setContourMode(ColorBlobLocatorProcessor.ContourMode.{proc.contour_mode})",
        f"                .setRoi({_roi(proc)})",
        f"                .setBlurSize({C.ensure_odd(proc.blur_size)})",
        f"                .setErodeSize({proc.erode_size})",
        f"                .setDilateSize({proc.dilate_size})",
        f"                .setMorphOperationType(ColorBlobLocatorProcessor.MorphOperationType.{proc.morph_type})",
        f"                .build();",
    ]
    return lines


def generate_java(global_cfg: C.GlobalConfig, processors: List[C.ProcessorConfig],
                  ds_size: Tuple[int, int]) -> str:
    ds_w, ds_h = ds_size

    out: List[str] = []
    out.append("        // ===== 需要的 import（按项目模板补充）=====")
    out.append("        // import java.util.ArrayList;")
    out.append("        // import java.util.List;")
    out.append("        // import org.firstinspires.ftc.robotcore.external.hardware.camera.WebcamName;")
    out.append("        // import org.firstinspires.ftc.robotcore.external.navigation.Size;")
    out.append("        // import org.firstinspires.ftc.robotcore.external.tfod.SortOrder;")
    out.append("        // import org.firstinspires.ftc.vision.VisionPortal;")
    out.append("        // import org.firstinspires.ftc.vision.opencv.ColorBlobLocatorProcessor;")
    out.append("        // import org.firstinspires.ftc.vision.opencv.ColorRange;")
    out.append("        // import org.firstinspires.ftc.vision.opencv.ColorSpace;")
    out.append("        // import org.firstinspires.ftc.vision.opencv.ImageRegion;")
    out.append("        // import org.opencv.core.RotatedRect;")
    out.append("")
    out.append("        // ===== 初始化阶段 =====  （放入 runOpMode 开头）")
    out.append("        // 构建每个 ColorBlobLocatorProcessor")
    for i, proc in enumerate(processors):
        out.extend(_builder(proc, i))
        out.append("")

    out.append("        // 挂载到同一个 VisionPortal")
    out.append("        VisionPortal portal = new VisionPortal.Builder()")
    for i in range(len(processors)):
        out.append(f"                .addProcessor(proc{i})")
    out.append("                .setCameraResolution(new Size(" + str(ds_w) + ", " + str(ds_h) + "))")
    out.append("                .setCamera(hardwareMap.get(WebcamName.class, \"Webcam 1\"))  // 或用 BuiltinCameraDirection.BACK")
    out.append("                .build();")
    out.append("")
    out.append("        telemetry.setMsTransmissionInterval(100);")
    out.append("")

    out.append("        // ===== 运行阶段 =====  （放入 runOpMode 循环体内）")
    out.append("        // 读取每个 Processor 的色块列表")
    for i, proc in enumerate(processors):
        out.append(f"        List<ColorBlobLocatorProcessor.Blob> blobs{i} = proc{i}.getBlobs();")
        for rule in proc.filter_rules:
            out.append(
                f"        ColorBlobLocatorProcessor.Util.filterByCriteria("
                f"ColorBlobLocatorProcessor.BlobCriteria.{rule.criterion}, "
                f"{_fmt(rule.min_value)}, {_fmt(rule.max_value)}, blobs{i});"
            )

    out.append("")
    out.append("        // 合并所有色块")
    out.append("        List<ColorBlobLocatorProcessor.Blob> allBlobs = new ArrayList<>();")
    for i in range(len(processors)):
        out.append(f"        allBlobs.addAll(blobs{i});")
    out.append("")
    out.append("        // 全局排序")
    out.append(
        f"        ColorBlobLocatorProcessor.Util.sortByCriteria("
        f"ColorBlobLocatorProcessor.BlobCriteria.{global_cfg.sort_criterion}, "
        f"SortOrder.{global_cfg.sort_order}, allBlobs);"
    )
    out.append("")
    out.append("        // 只提取排序第一的色块，并存储其拟合形状与关键参数")
    out.append("        if (!allBlobs.isEmpty()) {")
    out.append("            ColorBlobLocatorProcessor.Blob firstBlob = allBlobs.get(0);")
    if global_cfg.fit_mode == C.FIT_BOX:
        out.append("            RotatedRect box = firstBlob.getBoxFit();")
        out.append("            double boxCenterX = box.center.x;")
        out.append("            double boxCenterY = box.center.y;")
        out.append("            double boxWidth = box.size.width;")
        out.append("            double boxHeight = box.size.height;")
        out.append("            double boxAngle = box.angle;")
    else:
        out.append("            ColorBlobLocatorProcessor.Circle circle = firstBlob.getCircle();")
        out.append("            double circleCenterX = circle.getX();")
        out.append("            double circleCenterY = circle.getY();")
        out.append("            double circleRadius = circle.getRadius();")
    out.append("        }")

    return "\n".join(out) + "\n"