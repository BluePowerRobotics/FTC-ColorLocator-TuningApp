"""生成对应的完整 `ColorLocator` Java 类（封装摄像头 + ColorBlobLocatorProcessor）。"""

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
        f"                                    new Scalar({proc.lower[0]}, {proc.lower[1]}, {proc.lower[2]}),\n"
        f"                                    new Scalar({proc.upper[0]}, {proc.upper[1]}, {proc.upper[2]}))"
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
    """生成单个处理器的 Builder 链（返回局部变量 procN 的声明）。"""
    name = f"proc{index}"
    return [
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


def _needs_custom(processors: List[C.ProcessorConfig]) -> bool:
    return any(p.preset == "自定义" for p in processors)


def _imports(global_cfg: C.GlobalConfig, processors: List[C.ProcessorConfig]) -> List[str]:
    use_circle = global_cfg.fit_mode == C.FIT_CIRCLE
    use_box = global_cfg.fit_mode == C.FIT_BOX
    use_custom = _needs_custom(processors)

    lines = [
        "import android.util.Size;",
        "",
        "import com.qualcomm.robotcore.hardware.HardwareMap;",
        "import com.qualcomm.robotcore.util.SortOrder;",
        "",
        "import org.firstinspires.ftc.robotcore.external.hardware.camera.WebcamName;",
        "import org.firstinspires.ftc.vision.VisionPortal;",
    ]
    if use_circle:
        lines.append("import org.firstinspires.ftc.vision.opencv.Circle;")
    lines.append("import org.firstinspires.ftc.vision.opencv.ColorBlobLocatorProcessor;")
    lines.append("import org.firstinspires.ftc.vision.opencv.ColorRange;")
    if use_custom:
        lines.append("import org.firstinspires.ftc.vision.opencv.ColorSpace;")
    lines.append("import org.firstinspires.ftc.vision.opencv.ImageRegion;")
    if use_box:
        lines.append("import org.opencv.core.RotatedRect;")
    if use_custom:
        lines.append("import org.opencv.core.Scalar;")
    lines.append("")
    lines.append("import java.util.ArrayList;")
    lines.append("import java.util.List;")
    return lines


def generate_java(global_cfg: C.GlobalConfig, processors: List[C.ProcessorConfig],
                  ds_size: Tuple[int, int]) -> str:
    ds_w, ds_h = ds_size

    out: List[str] = []

    # ---------------- 类头与 import ----------------
    out.append("package org.firstinspires.ftc.teamcode;  // TODO: 改成你的队伍包名")
    out.append("")
    out.extend(_imports(global_cfg, processors))
    out.append("")
    out.append("/**")
    out.append(" * 色块定位器：内部封装摄像头与 ColorBlobLocatorProcessor，")
    out.append(" * 对外提供实时结果接口。")
    out.append(" *")
    out.append(" * 用法：")
    out.append(" *   ColorLocator locator = new ColorLocator(hardwareMap);")
    out.append(" *   while (opModeIsActive()) {")
    out.append(" *       if (locator.update()) {")
    out.append(" *           double x = locator.getCenterX();  // 归一化坐标")
    out.append(" *       }")
    out.append(" *   }")
    out.append(" */")
    out.append("public class ColorLocator {")
    out.append("")
    out.append("    // 摄像头分辨率（已降采样，需与 VisionPortal 设置一致）")
    out.append(f"    private static final int CAMERA_WIDTH = {ds_w};")
    out.append(f"    private static final int CAMERA_HEIGHT = {ds_h};")
    out.append("")
    out.append("    // 摄像头与处理器")
    out.append("    private final List<ColorBlobLocatorProcessor> processors = new ArrayList<>();")
    out.append("    private VisionPortal portal;")
    out.append("")
    out.append("    // 最新结果（调用 update() 后更新）")
    out.append("    private boolean targetFound = false;")
    out.append("    private double centerX = 0.0;   // 归一化 [-1, 1]，右为正")
    out.append("    private double centerY = 0.0;   // 归一化 [-1, 1]，上为正")
    out.append("    private double boxWidth = 0.0;")
    out.append("    private double boxHeight = 0.0;")
    out.append("    private double boxAngle = 0.0;")
    out.append("    private double radius = 0.0;")
    out.append("")
    out.append("    public ColorLocator(HardwareMap hardwareMap) {")
    out.append("        this(hardwareMap, \"Webcam 1\");")
    out.append("    }")
    out.append("")
    out.append("    public ColorLocator(HardwareMap hardwareMap, String webcamName) {")
    out.append("        // 构建各颜色处理器")
    for i, proc in enumerate(processors):
        out.extend(_builder(proc, i))
        out.append(f"        processors.add(proc{i});")
        out.append("")

    out.append("        // 封装摄像头：把所有处理器挂到同一个 VisionPortal")
    out.append("        VisionPortal.Builder builder = new VisionPortal.Builder()")
    for i in range(len(processors)):
        out.append(f"                .addProcessor(proc{i})")
    out.append(f"                .setCameraResolution(new Size(CAMERA_WIDTH, CAMERA_HEIGHT))")
    out.append("                .setCamera(hardwareMap.get(WebcamName.class, webcamName));")
    out.append("        portal = builder.build();")
    out.append("    }")
    out.append("")
    out.append("    /**")
    out.append("     * 读取最新一帧并计算目标色块，返回是否找到。")
    out.append("     * 拟合轮廓中心坐标会被归一化到 [-1, 1]，以右上为正：")
    out.append("     * x 向右增大、y 向上增大，画面中心为 (0, 0)。")
    out.append("     */")
    out.append("    public boolean update() {")
    out.append("        List<ColorBlobLocatorProcessor.Blob> allBlobs = new ArrayList<>();")
    out.append("")
    for i, proc in enumerate(processors):
        out.append(f"        // 处理器 {i}：读取并按规则过滤")
        out.append(f"        List<ColorBlobLocatorProcessor.Blob> blobs{i} = processors.get({i}).getBlobs();")
        for rule in proc.filter_rules:
            out.append(
                f"        ColorBlobLocatorProcessor.Util.filterByCriteria("
                f"ColorBlobLocatorProcessor.BlobCriteria.{rule.criterion}, "
                f"{_fmt(rule.min_value)}, {_fmt(rule.max_value)}, blobs{i});"
            )
        out.append(f"        allBlobs.addAll(blobs{i});")
        out.append("")

    out.append("        // 全局排序")
    out.append(
        f"        ColorBlobLocatorProcessor.Util.sortByCriteria("
        f"ColorBlobLocatorProcessor.BlobCriteria.{global_cfg.sort_criterion}, "
        f"SortOrder.{global_cfg.sort_order}, allBlobs);"
    )
    out.append("")
    out.append("        if (allBlobs.isEmpty()) {")
    out.append("            targetFound = false;")
    out.append("            centerX = 0.0;")
    out.append("            centerY = 0.0;")
    out.append("            return false;")
    out.append("        }")
    out.append("")
    out.append("        targetFound = true;")
    out.append("        ColorBlobLocatorProcessor.Blob first = allBlobs.get(0);")
    if global_cfg.fit_mode == C.FIT_BOX:
        out.append("        RotatedRect box = first.getBoxFit();")
        out.append("        // 归一化到 [-1, 1]：x 向右为正、y 向上为正")
        out.append("        centerX = (box.center.x / CAMERA_WIDTH) * 2.0 - 1.0;")
        out.append("        centerY = 1.0 - (box.center.y / CAMERA_HEIGHT) * 2.0;")
        out.append("        boxWidth = box.size.width;")
        out.append("        boxHeight = box.size.height;")
        out.append("        boxAngle = box.angle;")
    else:
        out.append("        Circle circle = first.getCircle();")
        out.append("        // 归一化到 [-1, 1]：x 向右为正、y 向上为正")
        out.append("        centerX = (circle.getX() / CAMERA_WIDTH) * 2.0 - 1.0;")
        out.append("        centerY = 1.0 - (circle.getY() / CAMERA_HEIGHT) * 2.0;")
        out.append("        radius = circle.getRadius();")
    out.append("        return true;")
    out.append("    }")
    out.append("")
    out.append("    public boolean isTargetFound() { return targetFound; }")
    out.append("    public double getCenterX() { return centerX; }")
    out.append("    public double getCenterY() { return centerY; }")
    if global_cfg.fit_mode == C.FIT_BOX:
        out.append("    public double getBoxWidth() { return boxWidth; }")
        out.append("    public double getBoxHeight() { return boxHeight; }")
        out.append("    public double getBoxAngle() { return boxAngle; }")
    else:
        out.append("    public double getRadius() { return radius; }")
    out.append("")
    out.append("    public void close() {")
    out.append("        if (portal != null) {")
    out.append("            portal.close();")
    out.append("        }")
    out.append("    }")
    out.append("}")

    return "\n".join(out) + "\n"