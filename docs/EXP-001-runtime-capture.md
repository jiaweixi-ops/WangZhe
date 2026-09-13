# EXP-001 — 运行形态与捕获验证协议

## 目标

确认真实运行形态、游戏 viewport、DXGI 输出映射和生命周期恢复能力。

## 必测矩阵

- 稳态 30 分钟
- Alt-Tab ×20
- 窗口拖动 ×20
- 跨显示器 ×10
- 100% / 125% / 150% DPI
- 游戏重启 ×5
- 最大化/恢复 ×10
- 睡眠/唤醒 ×3
- HDR 开/关（设备支持时）

## 有效帧

同时满足：

- 非黑
- 新鲜时间戳/单调 sequence
- 不是长期陈旧重复内容
- 捕获区域与当前窗口/监视器一致

`None` 抓帧、capture gap、黑帧、stale suspect 都要落时间戳，不只计数。

## 多显示器

窗口 HWND 被 pin；只在 HWND 失效时重新枚举。每帧只廉价刷新 ClientRect/Monitor。

DXcam 选择窗口所在监视器对应 output，使用该 output 的全屏帧再以 monitor-local 坐标裁剪，避免把虚拟桌面坐标直接当作 output-local region。

若 Win32 monitor 几何与 DXcam output 几何无法对应，实验必须显式报错/降级，不允许安静抓错屏。
