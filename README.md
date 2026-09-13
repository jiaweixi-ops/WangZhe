# 棋镜 AI Coach — Spike

这是《王者万象棋》实时教练项目的 **Spike（风险验证工程）**。当前代码只验证 V1.3 的前置技术风险，不实现策略、AI、英雄数据库或自动操作。

## 当前范围

- S−1：运行形态识别（原生 PC / 模拟器 / 云串流候选）
- S0：DXGI Desktop Duplication 捕获（DXcam）
- S0：监视器感知、多显示器路由、窗口越界裁剪
- S0：ROI/网格级局部变化、新鲜帧、黑帧、冻结嫌疑检查
- S1：透明 Overlay、点击穿透、`WDA_EXCLUDEFROMCAPTURE`
- S1：`--probe-overlay-exclusion` 客户区内 hide/show/hide 污染实验
- S2：真实游戏 viewport（黑边/letterbox + 可选宽高比先验）检测
- S2：ORB + RANSAC **全仿射**参考坐标注册，安全时才允许 SCALE_ONLY 降级
- S0/S1/S2 三值门禁：`PASS / DEGRADED_SHIPPABLE / FAIL`
- 完整证据 JSON（环境、帧 gap、全部 registration 样本与聚合）
- Linux + Windows CI 单元/导入测试

**尚未实现**：S3 阶段识别、S4 倒计时、S5 时间冻结假计划，以及 Product 层的英雄 OCR、GameState、策略评分、数据库、LLM、自动点击/拖拽。

## 环境

- Windows 10/11
- Python 3.11+
- 建议游戏使用窗口/无边框窗口模式

安装：

```powershell
py -3.11 -m venv .venv
.venv\Scripts\activate
python -m pip install -U pip
pip install -e ".[dev]"
```

## 1. 先做 S−1：检查运行形态

```powershell
qijing-inspect --title 王者
```

输出 HWND / PID / EXE / Window Class、物理像素客户区、进程父子树、监视器信息与运行形态候选。

> `UNKNOWN` 是允许结果；不要为了“有答案”强猜原生 PC。

## 2. 跑 S0 基础捕获

```powershell
qijing-spike --title 王者 --duration 60
```

输出目录：

```text
artifacts/spike-YYYYMMDD-HHMMSS/
├─ evidence.json
├─ first_frame.jpg
├─ viewport.jpg
├─ health.json
├─ viewport.json
├─ registration.json
├─ none_grabs.json
└─ capture_gaps.json
```

Freshness 不再依赖 `--expected-change`；旧参数仍可接受但会被忽略。监视器会先从近期真实局部变化学习“这是动态画面”，之后长时间平坦才升级为 `STALE_SUSPECT`。

## 3. 真正验证 S1 Overlay 污染

只把面板放外面不能证明捕获排除生效。请在相对稳定的准备画面运行：

```powershell
qijing-spike --title 王者 --duration 30 --probe-overlay-exclusion
```

程序会执行：

```text
隐藏 Overlay → F0
显示 Overlay（viewport 内）→ F1
再次隐藏 → F2
```

并比较重叠区域：`F1` 的额外变化是否显著高于 `F0↔F2` 背景变化，同时记录 `SetWindowDisplayAffinity` 的返回值和 Win32 last-error。

若客户区内 Overlay 无法证明干净，S1 会降级为 `DEGRADED_SHIPPABLE`，产品仍可使用外置面板。

## 4. 参考坐标注册

先保存基准图：

```powershell
qijing-spike --title 王者 --duration 5 --save-reference reference.jpg
```

再验证缩放/拖边：

```powershell
qijing-spike --title 王者 --duration 30 --reference reference.jpg
```

注册使用 ORB + `estimateAffine2D`，支持非等比 X/Y 缩放。证据同时记录：

- inlier 数/比例
- inlier 残差
- **全部 good matches** 的均值/P90 残差
- X/Y 尺度
- 几何一致性后的 confidence
- `ORB_AFFINE / SCALE_ONLY / FAILED`

`SCALE_ONLY` 只有在参考图与当前 viewport 宽高比近似一致时才允许；宽高比明显变化时会明确 `FAILED`，不会安静返回错误矩阵。

## 5. 模拟器/串流 viewport

若已经知道游戏内部视口是 16:9，可临时提供先验：

```powershell
qijing-spike --title 王者 --duration 30 --viewport-aspect 1.7777778
```

该路线会明确标记为低置信 `ASPECT_PRIOR`，不会冒充黑边检测成功。

## 6. 测试

```powershell
pytest
```

当前单元测试覆盖：

- 小范围真实 UI 变化
- 动态画面冻结
- 静态画面不被无条件误报 stale
- viewport 显式失败/降级
- Rect 数学
- registration 导入与 ORB 兼容
- 非等比缩放全仿射
- 不安全 SCALE_ONLY 拒绝

## 7. 协议

- `docs/EXP-001-runtime-capture.md`
- `docs/EXP-002-spike-gates.md`

阈值的代码真值在 `qijing_spike.gates.SPIKE_THRESHOLDS`，协议文件解释这些阈值的含义与人工验收步骤。
