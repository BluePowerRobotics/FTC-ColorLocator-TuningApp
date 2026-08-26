# ColorSegmentation 现状规格（Baseline Spec）

> 本文件是对**当前已实现**的桌面调参应用「ColorSegmentation」的完整功能与设计记录，
> 作为后续更新/重构时的权威参考。它不是待办需求，而是对现状的忠实描述。
> 用语约定：本文所有「应/当前/固定」均描述**现状行为**，而非未来要求。

---

## 1. 定位与目标

面向 **FTC（FIRST Tech Challenge）** 机器人队伍的桌面应用，用于**可视化调节**
FTC SDK 内置 `ColorBlobLocatorProcessor`（以下简称 CBLP）的各项参数，实时预览色块分割
结果，并**一键生成**可直接复制到机器人 OpMode 的 Java 代码。

核心价值：把需要机器人上反复试错的参数标定工作搬到电脑上，用图片/摄像头实时反馈。

关联文档：
- `README.md`：用户视角简介、运行/打包方式。
- `ColorLocator.md`：CBLP 的完整官方用法与参数详解（本应用所生成代码的语义来源）。

---

## 2. 技术栈与运行

- 语言/版本：Python 3.13
- GUI：PySide6（Qt6），应用使用 `Fusion` 风格
- 视觉：OpenCV（`opencv-python`）+ NumPy
- 打包：PyInstaller（`--onefile --windowed --collect-all PySide6`，产物 `dist/ColorSegmentation.exe`）
- 依赖清单（`requirements.txt`）：`opencv-python>=4.8`、`numpy>=1.24`、`PySide6>=6.6`
- 入口：`python main.py`

---

## 3. 目录结构

```
main.py                    # 程序入口，创建 QApplication 与 MainWindow
app/
  __init__.py
  config.py                # 配置数据模型与所有常量
  pipeline.py              # 核心视觉处理管道（纯 OpenCV/NumPy，无 Qt 依赖）
  codegen.py               # 生成 FTC Java 代码
  camera.py                # 摄像头采集（线程抓帧）
  widgets.py               # 可复用 Qt 控件
  main_window.py           # 主窗口控制器（导航/时序/Processor 管理）
  pages.py                 # 7 个向导页面 + 页面基类 + 绘制辅助函数
```

分层设计：视觉算法（`pipeline`）与 UI（Qt）解耦；代码生成（`codegen`）独立于算法；
`main_window` 作为唯一控制器持有全局配置、输入源、多个 Processor 与管道结果。

---

## 4. 数据模型（config.py）

### 4.1 常量

| 常量 | 值 | 说明 |
|------|----|------|
| `COLOR_SPACES` | `["YCrCb", "HSV", "RGB"]` | 三种可选色彩空间 |
| `ROI_MODES` | `["整帧", "归一化坐标"]` | ROI 两种模式（`ROI_ENTIRE`/`ROI_NORMALIZED`） |
| `MORPH_TYPES` | `["CLOSING", "OPENING"]` | 形态学运算类型 |
| `CONTOUR_MODES` | `["EXTERNAL_ONLY", "ALL_FLATTENED_HIERARCHY"]` | 轮廓模式 |
| `CRITERIA` | `BY_CONTOUR_AREA` / `BY_DENSITY` / `BY_ASPECT_RATIO` / `BY_ARC_LENGTH` / `BY_CIRCULARITY` | 五种色块指标，用于过滤与排序 |
| `SORT_ORDERS` | `["DESCENDING", "ASCENDING"]` | 排序方向 |
| `FIT_MODES` | `["boxFit", "circleFit"]` | 拟合形状：最小旋转矩形 / 最小外接圆 |

### 4.2 色彩空间元数据（`COLOR_SPACE_META`）

| 空间 | 通道名 | 各通道范围（8 位） |
|------|--------|------------------|
| `YCrCb` | Y / Cr / Cb | 均 0~255 |
| `HSV` | H / S / V | H 0~180（覆盖 OpenCV 实际 0~179）、S/V 0~255 |
| `RGB` | R / G / B | 均 0~255 |

> 说明：HSV 的 H 滑条标为 0~180 是为与官方样例注释一致，实际 `cv2.cvtColor` 产出 0~179。

### 4.3 预定义颜色（`PREDEFINED_COLORS`）

7 个预设（键名 + `(色彩空间, 下界, 上界)`），全部用 **YCrCb** 标定：

| 键 | 下界 | 上界 |
|----|------|------|
| `自定义` | (0,0,0) | (255,255,255) |
| `RED` | (150,150,0) | (255,255,128) |
| `BLUE` | (0,0,128) | (128,128,255) |
| `YELLOW` | (150,100,0) | (255,170,130) |
| `GREEN` | (0,100,0) | (128,170,130) |
| `ARTIFACT_GREEN` | (60,60,60) | (150,140,140) |
| `ARTIFACT_PURPLE` | (90,130,130) | (170,200,200) |

> 关键设计：这些 YCrCb 数值只是**桌面预览的近似值**。生成 Java 代码时，凡非「自定义」
> 的预设一律引用官方常量 `ColorRange.X`，机器人端使用 FTC 官方标定值，保证预览可与
> 真机大体一致但未必逐像素相同（见 §7.2 代码生成）。

### 4.4 数据类

**`FilterRule`**：单条过滤规则。
- `criterion: str = "BY_CONTOUR_AREA"`
- `min_value: float = 0.0` / `max_value: float = 0.0`

**`ProcessorConfig`**：单个「颜色处理器」的全部参数（对应一个 CBLP 实例），字段：

| 字段 | 默认 | 含义 |
|------|------|------|
| `name` | `"Processor 1"` | 显示名 |
| `roi_mode` | `"整帧"` | ROI 模式 |
| `roi_norm` | `[-1.0, 1.0, 1.0, -1.0]` | 归一化 ROI，顺序为 `[uMin, vMax, uMax, vMin]` |
| `blur_size` | `5` | 高斯模糊核大小（强制奇数） |
| `preset` | `"自定义"` | 预定义颜色键 |
| `color_space` | `"YCrCb"` | 色彩空间 |
| `lower` / `upper` | `[0,0,0]` / `[255,255,255]` | 三通道阈值上下界 |
| `erode_size` | `0` | 腐蚀核大小 |
| `dilate_size` | `0` | 膨胀核大小 |
| `morph_type` | `"CLOSING"` | 形态学顺序 |
| `contour_mode` | `"EXTERNAL_ONLY"` | 轮廓模式 |
| `filter_rules` | `[]` | 过滤规则列表（每 Processor 独立） |

- `clone()`：返回深拷贝（列表字段重建）。
- `default_processor(index)`：基于「自定义」预设生成新 Processor 模板。

**`GlobalConfig`**：全局共享配置（跨所有 Processor）。

| 字段 | 默认 | 含义 |
|------|------|------|
| `downsample_rate` | `1` | 降采样率，1~8 |
| `teaching_mode` | `False` | 教学模式（教程面板可见性） |
| `page_index` | `0` | 当前向导页索引 0~6 |
| `sort_criterion` | `"BY_CONTOUR_AREA"` | 全局排序指标 |
| `sort_order` | `"DESCENDING"` | 全局排序方向 |
| `fit_mode` | `"circleFit"` | 全局拟合模式 |

> 划分原则：**每个进程级参数**（ROI、降噪、色范围、后处理、过滤规则）放在
> `ProcessorConfig`；**全局性参数**（降采样率、排序、拟合、教学模式、页码）放在
> `GlobalConfig`。

---

## 5. 视觉管道（pipeline.py）

`run_pipeline(image_bgr, global_cfg, processors) -> PipelineResult` 是唯一对外入口。
内部各阶段：

### 5.1 数据结构

- **`Blob`**：单个色块。`cx, cy, area, density, aspect_ratio, arc_length, circularity,
  rect, circle, contour` + `filtered=False`（是否被过滤）、`rank=0`（合并后排名）、
  `processor_index=0`（来源 Processor）。
  - `rect`：`((cx, cy), (w, h), angle)`，`minAreaRect` 结果（全图降采样坐标）。
  - `circle`：`(cx, cy, r)`，`minEnclosingCircle` 结果。
  - `contour`：已加 ROI 偏移的全图坐标轮廓点。
- **`ProcessorResult`**：单个 Processor 的中间产物（`denoised / roi_rect / roi_bgr /
  mask / post_mask / full_mask / blobs`），主要用于各页面的可视调试。
- **`PipelineResult`**：`original / downsampled / processors / merged_mask / merged_blobs`。

### 5.2 `preprocess`：最长边缩放到 640
保持宽高比，仅当最长边 > 640 时用 `INTER_AREA` 缩小到最长边 640，否则原样返回。

### 5.3 `downsample`：降采样
`factor <= 1` 直接复制；否则 `cv2.resize` 到 `(w // factor, h // factor)`，`INTER_AREA`。
分辨率变为 1/factor²。

### 5.4 `compute_roi_rect`：ROI 计算
- 归一化模式：把 `[uMin, vMax, uMax, vMin]`（Unity 中心坐标，中心原点，±1）换算为像素：
  - `x_left = round((uMin+1)/2*W)`，`x_right = round((uMax+1)/2*W)`
  - `y_top = round((1-vMax)/2*H)`，`y_bottom = round((1-vMin)/2*H)`（**v 轴与图像 y 反向**，上方为正）
  - 各值 clamp 到 `[0, W/H]`；若宽或高 ≤ 0，回退**整帧** `(0, 0, W, H)`。
- 整帧模式：直接 `(0, 0, W, H)`。

### 5.5 `binarize`：二值化
按 `color_space` 选择 `cv2.cvtColor` 转换码（YCrCb/HSV/RGB），再对 3 通道做
`cv2.inRange(conv, low, up)`，其中 `low/up` 为对 `lower/upper` 逐通道取 min/max 的结果
（容忍用户把上下界填反）。

### 5.6 `postprocess`：形态学后处理
- 腐蚀/膨胀核为 `np.ones((size, size))`（方形结构元素），`size==0` 时跳过该步骤。
- `morph_type == "OPENING"`：先腐蚀后膨胀；否则（`CLOSING`）先膨胀后腐蚀。

### 5.7 `extract_blobs`：色块提取
- `EXTERNAL_ONLY` → `cv2.RETR_EXTERNAL`，`ALL_FLATTENED_HIERARCHY` → `cv2.RETR_LIST`，
  均用 `CHAIN_APPROX_SIMPLE`。
- 逐轮廓计算：
  - `area = cv2.contourArea`（<1 的轮廓丢弃）
  - `density = area / 凸包面积`
  - `arc_length = cv2.arcLength`
  - `circularity = 4π·area / arc²`
  - `aspect_ratio = max(w,h)/min(w,h)`（来自 `minAreaRect`，宽高为 0 时取 1）
  - `minAreaRect` → `rect`，`minEnclosingCircle` → `circle`
  - 所有坐标加 ROI 偏移 `(off_x, off_y)` 转成全图坐标
- 完成后**按面积降序**预排序（对应 CBLP 默认返回顺序）。

### 5.8 `apply_filters`：过滤
- 若 `rules` 为空则**不改动** `filtered` 标志（原样返回）。
- 否则先重置所有 blob 的 `filtered=False`，再对每条规则判断指标是否落在
  `[min_value, max_value]`，超出即置 `filtered=True`（任一规则不满足即被过滤）。

### 5.9 `sort_blobs` / `_assign_rank`
- `sort_blobs`：按指定 `criterion` 与 `order`（`DESCENDING` 倒序）排序。
- `_assign_rank`：仅对未被过滤的 blob 依次编号 `rank=1..n`，被过滤者 `rank=0`。

### 5.10 `_process_one`（单 Processor）
流程：`_odd(blur_size)` 高斯模糊（`GaussianBlur`，模糊值 ≤1 跳过）→ `compute_roi_rect`
→ 裁剪 `roi_bgr` → `binarize` → `postprocess` → 把 `post_mask` 放回全图 `full_mask`
→ `extract_blobs` → `apply_filters`。

### 5.11 `run_pipeline`（主流程）
`preprocess` → `downsample` → 逐 Processor `_process_one`（回填 `processor_index`、汇总
blob）→ `merged_mask = bitwise_or(各 full_mask)` → 全局 `sort_blobs` → `_assign_rank`。

### 5.12 `downsample_size`
返回降采样后 `(w, h)`，供 ROI 滑条范围与代码生成中的 `setCameraResolution` 使用。

> 关键设计：**多 Processor 的 mask 用 `bitwise_or` 合并**成一张 `merged_mask`，
> 对应「同时查找多种颜色」的需求（官方单个 CBLP 不支持多色取并）。

---

## 6. 界面结构（main_window.py + pages.py + widgets.py）

### 6.1 主窗口（MainWindow）
- 标题「色块分割调参应用」，尺寸 1200×800。
- 顶部栏：`教学模式`按钮、步骤标签（居中）、`冻结画面`按钮（仅摄像头模式显示）、
  `上一步/下一步`。
- 主体：`QStackedWidget` 承载 7 个页面。
- 持有状态：`global_cfg`、`processors`（至少 1 个）、`image`（BGR）、`result`、
  `camera`、`camera_mode`、`frozen`。

**时序（防抖）**：
- 参数变化统一走 `on_param_changed()` → 启动**单次 30ms** 定时器 → 到点执行
  `_run_pipeline()` → 刷新所有页面的 `refresh()`。
- 摄像头用独立 `30ms` 周期定时器 `_camera_tick`，冻结时跳过抓帧处理。

**导航**：`go_to_page(idx)` 夹取到 0..6；切页时调用该页 `rebuild_params()`（重建设备
参数控件，以同步 Processor 数量）与 `refresh()`；第 0 页（上传页）隐藏上/下步按钮。

**Processor 管理**：
- `add_processor()`：追加默认 Processor 并重建参数页。
- `remove_processor(index)`：至少保留 1 个；删除后重建参数页。
- 重建范围：页面索引 2..6（ROI/降噪/色范围/后处理/过滤排序），因这些页含每个
  Processor 的参数组。

**关闭**：`closeEvent` 先 `_stop_camera()` 释放摄像头。

### 6.2 向导页面（7 页，`PAGE_CLASSES`）

每个页面左侧为参数面板 + 教程面板（教学模式控制可见性），右侧为图像预览。

| 索引 | 页面 | 关键参数/控件 | 预览内容 |
|------|------|--------------|---------|
| 0 | 上传页 | 选择图片 / 打开摄像头、缩略图 | 原图缩略图 |
| 1 | 预处理 | 降采样率滑条 1~8、当前分辨率标签 | 降采样后图像 |
| 2 | ROI | 每 Processor：模式 + 4 个归一化滑条（uMin/vMax/uMax/vMin，-1~1，步长 0.01） | 画 ROI 矩形框的降采样图 |
| 3 | 降噪 | 每 Processor：blurSize 滑条 1~31（强制奇数） | 降噪后图像 |
| 4 | 色范围 | 每 Processor：预定义颜色 + 色空间 + 6 阈值滑条（下限/上限×3 通道） | ROI 内、按 mask 遮罩（非目标黑化）的图像 |
| 5 | 后处理 | 每 Processor：erode/dilate（0~31）、morphType、contourMode | 所有 Processor 合并 mask 的遮罩叠加（目标保色、其余黑化） |
| 6 | 过滤排序+代码 | 每 Processor：过滤规则（指标 + min/max）；全局排序指标/方向/拟合；代码框 | 色块轮廓+拟合框叠加（带排名称色/灰色过滤线） |

页面共同能力：
- `rebuild_params()`：根据当前 `processors` 重建参数组（增删 Processor 后调用）。
- `refresh()`：从 `controller.result` 重新渲染预览。
- `set_tutorial_visible(v)`：控制教程面板显隐。
- 页面 2/3/4 有「显示 Processor」下拉框，切换预览单个 Processor。

### 6.3 交互细节

- **图像坐标取色**（页面 4）：悬停时底部状态栏实时显示该像素的
  `YCrCb / RGB / HSV` 三组数值及当前 mask 判定（是否命中目标），便于抄录阈值。
- **色块悬停高亮**（页面 6）：悬停命中色块放大黄色粗轮廓；否则默认高亮排名第一的色块。
  未过滤色块按排名使用绿→青→蓝→紫渐变，被过滤者用灰线。
- **拟合绘制**：`boxFit` 画 `cv2.boxPoints` 旋转矩形；`circleFit` 画外接圆；均叠加中心点。
- **线条粗细自适应**：轮廓线宽随降采样率等比例缩小，保证与原图视觉比例一致。

### 6.4 复用控件（widgets.py）

- **`SliderSpin`**：标签 + 滑条 + 数值框双向联动。
  - 支持整数/浮点、**奇数模式**（步进 2、偶数自动 +1，但 0 视为「关闭」保留为 0）。
  - `set_range()` 动态改范围/步长（供过滤规则按指标切换）；`set_value_silent()` 仅同步显示不发射信号。
- **`OptionBox`**：标签 + 下拉框，`changed` 信号，`set_value(v, silent)` 静默设置。
- **`ThresholdGroupCard`**：色空间下拉 + 6 个阈值滑条；切换色空间时按
  `COLOR_SPACE_META` 更新通道名与范围；`load(proc)`/`write(proc)` 与 `ProcessorConfig` 互转。
- **`ImageView`**：`contain` 方式居中显示（letterbox 留白），背景深色；`paintEvent`
  绘制；`mouseMoveEvent` 把控件坐标反算回原始图像坐标并发 `hover_moved(x,y)` 信号。

### 6.5 输入源（camera.py + main_window.py）
- 图片：`QFileDialog` + `cv2.imread` 改为 **`cv2.imdecode`** 读取字节流，以兼容 Windows
  上含中文/非 ASCII 的路径。
- 摄像头：`Camera` 用 `cv2.VideoCapture(0, CAP_DSHOW)`（失败回退默认后端），分辨率
  1280×720，**独立 daemon 线程**持续读帧、用锁保护、`latest_frame()` 返回副本。
  支持「冻结画面」冻结当前帧进行精细调参。
- 选图或开摄像头后自动跳转到第 1 页。

### 6.6 教学模式
顶部按钮切换 `GlobalConfig.teaching_mode`；开启后各页教程面板显示，按钮文案/配色变化。
教程内容为对应当前步骤的计算机视觉科普（降采样、ROI、高斯模糊、色空间、形态学、
色块指标、过滤 vs 排序等）。

---

## 7. 代码生成（codegen.py）

`generate_java(global_cfg, processors, ds_size) -> str` 输出两段 Java 代码。

### 7.1 输出结构
- **初始化阶段**（放 `runOpMode` 开头）：为每个 Processor 生成一个
  `ColorBlobLocatorProcessor procN` 的 Builder 链；再用一个 `VisionPortal` 通过
  `.addProcessor(procN)` 挂载全部；`.setCameraResolution(new Size(ds_w, ds_h))` 使用
  **降采样后的分辨率**；`.setCamera(hardwareMap.get(WebcamName.class, "Webcam 1"))`；
  最后 `telemetry.setMsTransmissionInterval(100)`。
- **运行阶段**（放循环体内）：逐个 `getBlobs()`、逐条 `filterByCriteria`、用
  `ArrayList` `addAll` 合并成 `allBlobs`、全局 `sortByCriteria`、取 `allBlobs.get(0)`
  并按 `fit_mode` 输出 `boxFit`（center/size/angle）或 `circleFit`（x/y/radius）。

### 7.2 关键映射规则
- **颜色**：预设非「自定义」→ 直接用 `ColorRange.<PRESET>` 常量；「自定义」→
  `new ColorRange(ColorSpace.<space>, new Scalar(lower…), new Scalar(upper…))`。
- **ROI**：整帧 → `ImageRegion.entireFrame()`；归一化 →
  `ImageRegion.asUnityCenterCoordinates(uMin, vMin, uMax, vMax)`（字段顺序已按官方 API 对齐）。
- `_fmt`：浮点格式化为最简字符串（去尾零，`-0`→`0`），用于 ROI 与过滤阈值。

---

## 8. 关键设计决策（后续改动需注意）

1. **颜色空间内部统一为 BGR，显示前转 RGB**（`_bgr2rgb`）；OpenCV 读图/摄像头均输出 BGR。
2. **预定义颜色的双重来源**：桌面预览用 `PREDEFINED_COLORS` 的 YCrCb 近似值，生成代码用
   `ColorRange.*` 官方常量——两者语义一致但数值不同，调整预设时需同步考虑两端。
3. **HSV H 范围 0~180**（UI 标定）与 OpenCV 实际 0~179 的差异。
4. **归一化 ROI 的 v 轴方向与图像 y 相反**，`roi_norm` 字段顺序固定为 `[uMin, vMax, uMax, vMin]`。
5. **多 Processor（多颜色）靠 `bitwise_or` 合并 mask**；每个 Processor 拥有独立 ROI/降噪/色范围/后处理。
6. **过滤规则按 Processor 独立，排序/拟合为全局**，混合点在 `run_pipeline` 汇总后统一排序。
7. **防抖 30ms 单次定时器**避免滑条拖动时频繁重算；摄像头另用 30ms 周期定时器。
8. **降采样率影响分辨率与线宽**：代码生成的分辨率、轮廓绘制线宽都随其缩放。
9. **blur/erode/dilate 核**：blur 强制奇数（偶数 +1，0 关闭除外）；erode/dilate 为方形核，
   0 表示跳过。
10. **过滤为空时不重置 `filtered`**，避免「无规则」状态意外影响既有标记。
11. **`cv2.imdecode` 读图**兼容非 ASCII 路径（Windows 常见问题）。
12. **代码框去抖**：`_set_code` 在文本不变时跳过 `setPlainText`，避免实时刷新反复重置滚动位置。
13. **最少 1 个 Processor**：删除到只剩 1 个时不可再删。

---

## 9. 已知边界与限制（现状）

- 颜色查找与 CBLP 语义对齐，但不支持 Lab/HSL/HLS 等官方不支持的空间（见 `ColorLocator.md`）。
- 预定义颜色是近似标定，可能与真机官方常量存在细微差异。
- 程序当前仅实现「描述现有功能」的规格；无持久化（参数不保存到文件）、无历史/撤销。
- 摄像头固定为默认摄像头（index 0），无设备选择、无分辨率选择 UI（代码写死 1280×720）。