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

## 捕获观测语义

DXcam one-shot `grab()` 的两种正常结果必须分开记：

```text
numpy frame  -> NEW_PRESENT
None         -> NO_NEW_PRESENT
```

`NO_NEW_PRESENT` 表示自上次采集后没有新的桌面呈现，不等于 capture error。

真正的 capture error 包括：

- backend 抛异常
- monitor/output 几何无法对应
- window/monitor 无法解析
- 其它明确失败

如果实验显式复用上一帧，必须在 `CapturedFrame.reused_cached` 中保留 provenance。缓存帧不能作为 S1/S2 这类“证明新观察”的测量样本。

## 有效新呈现帧

同时满足：

- 非黑
- 新鲜时间戳/单调 sequence
- `reused_cached == False`
- 捕获区域与当前窗口/监视器一致

局部像素长期不变化本身不能证明 capture stale。只有独立 liveness witness 明确说明该区域此刻应变化时，才允许升级为 `STALE_SUSPECT`。

以下都要落时间戳/计数：

- NEW_PRESENT
- NO_NEW_PRESENT
- capture error
- new-present gap（信息性）
- black frame
- stale suspect（仅在有 activity witness 时）

## 多显示器

窗口 HWND 被 pin；只在 HWND 失效时重新枚举。每帧只廉价刷新 ClientRect/Monitor。

DXcam 选择窗口所在监视器对应 output，使用该 output 的全屏帧再以 monitor-local 坐标裁剪，避免把虚拟桌面坐标直接当作 output-local region。

若 Win32 monitor 几何与 DXcam output 几何无法对应，实验必须显式报错/降级，不允许安静抓错屏。
