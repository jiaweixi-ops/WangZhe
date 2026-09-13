# 棋镜 AI Coach — Spike

这是《王者万象棋》实时教练项目的 **Spike（风险验证工程）**。当前代码只验证 V1.3 的前置技术风险，不实现策略、AI、英雄数据库或自动操作。

## 当前范围

- S−1：运行形态识别（原生 PC / 模拟器 / 云串流候选）
- S0：DXGI Desktop Duplication 捕获（DXcam）
- S0：监视器感知、多显示器路由、窗口越界裁剪
- S0：ROI/网格级局部变化、新鲜帧、黑帧检查
- S0：区分“无新桌面呈现”与真正 capture error
- S1：透明 Overlay、点击穿透、`WDA_EXCLUDEFROMCAPTURE`
- S1：`--probe-overlay-exclusion` 正对照污染实验
- S2：真实游戏 viewport（黑边/letterbox + 可选宽高比先验）检测
- S2：ORB + RANSAC **全仿射**参考坐标注册，安全时才允许 SCALE_ONLY 降级
- S0/S1/S2 三值门禁：`PASS / DEGRADED_SHIPPABLE / FAIL`
- 完整证据 JSON（环境、present witness、全部 registration 样本与聚合）
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
├─ no_new_presents.json
└─ capture_gaps.json
```

### DXGI / DXcam 语义

DXcam 的 one-shot `grab()` 在没有新的桌面呈现时可以返回 `None`。本 Spike 将它记录成：

```text
NO_NEW_PRESENT
```

而不是 capture failure。因此合法静止画面不会因为大量 `None` 被 S0 判坏。

如果某个实验显式允许复用上一帧，`CapturedFrame.reused_cached=True` 会保留该 provenance。**缓存帧不得用于证明 S1 排除成功。**

Freshness 仍会记录局部活动和静止时长，但仅在未来 S3/S4 或其它独立 witness 明确给出 `activity_expected=True` 时，才允许升级为 `STALE_SUSPECT`。单纯“刚才动过、现在静止 3 秒”不会再被当作冻结。

## 3. 真正验证 S1 Overlay 污染

请在相对稳定的准备画面运行：

```powershell
qijing-spike --title 王者 --duration 30 --probe-overlay-exclusion
```

S1 不再用“没看到 Overlay”直接证明成功，而是先证明仪器能看到已知阳性信号：

```text
F0：Overlay 隐藏，取得非缓存 baseline
 ↓
关闭排除（WDA_NONE）
 ↓
viewport 内显示 Overlay
 ↓
F+：必须取得新的桌面帧，并显著看到 Overlay        ← 正对照
 ↓
重新开启 WDA_EXCLUDEFROMCAPTURE
 ↓
F−：必须取得新的桌面帧
 ↓
确认 Overlay 信号显著下降并落到阈值内          ← 阴性验证
```

任一测量帧来自缓存、拿不到新的桌面帧、正对照看不到 Overlay，都会得到 **inconclusive**，S1 不允许 PASS。

若客户区内排除无法被证明，但外置面板可用，S1 为 `DEGRADED_SHIPPABLE`。

探针运行期间会重置主循环 gap/freshness 基线，不把 probe 自身的 sleep/affinity 切换计成 S0 捕获 gap。

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
- 合法静止不因历史活动被误报 stale
- 外部 activity witness 可触发 stale
- S1 正对照通过后排除成功
- S1 排除失败降级
- **缓存帧不能产生 S1 假 PASS**
- 正对照不可见时不能 PASS
- DXGI `NO_NEW_PRESENT` 不作为 capture error
- viewport 显式失败/降级
- Rect 数学
- registration 导入与 ORB 兼容
- 非等比缩放全仿射
- 不安全 SCALE_ONLY 拒绝

## 7. 协议

- `docs/EXP-001-runtime-capture.md`
- `docs/EXP-002-spike-gates.md`

阈值的代码真值在 `qijing_spike.gates.SPIKE_THRESHOLDS`，协议文件解释这些阈值的含义与人工验收步骤。
