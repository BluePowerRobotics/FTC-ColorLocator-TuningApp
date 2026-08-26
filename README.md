# ColorSegmentation

面向 FTC（FIRST Tech Challenge）机器人队伍的色块分割调参桌面应用。

在电脑上**可视化地调节** `ColorBlobLocatorProcessor` 的各项参数（降采样、ROI、高斯降噪、色范围、形态学、过滤排序、拟合形状），实时预览分割结果，并**一键生成**可直接复制到机器人 OpMode 的 Java 代码。

## 特性

- 7 步向导式调参流程，参数改动实时刷新预览（支持防抖）
- 图片 / 摄像头两种输入源，摄像头支持冻结画面
- 三种色彩空间（YCrCb / HSV / RGB）+ FTC 官方预设颜色
- 色块指标（面积、密度、长宽比、周长、圆形度）过滤与排序
- 拟合旋转矩形 / 最小外接圆，并输出中心、宽高、半径等参数
- 生成 `ColorBlobLocatorProcessor` 初始化 + 运行阶段的核心 Java 代码
- 内置教学模式，边操作边讲解相关计算机视觉知识

## 运行

```bash
pip install -r requirements.txt
python main.py
```

## 打包

```bash
python -m PyInstaller --noconfirm --clean --onefile --windowed --name ColorSegmentation --collect-all PySide6 main.py
```

输出见 `dist/ColorSegmentation.exe`。

## 技术栈

Python 3.13 · PySide6（Qt6）· OpenCV · NumPy · PyInstaller

详细功能规划见 [Plan.md](Plan.md)。