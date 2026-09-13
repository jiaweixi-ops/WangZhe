# 棋镜 AI Coach — Spike

这是《王者万象棋》实时教练项目的 **Spike（风险验证工程）**。当前代码只验证 V1.3 的前置技术风险，不实现策略、AI、英雄数据库或自动操作。

## 当前范围

- S−1：运行形态识别（原生 PC / 模拟器 / 云串流候选）
- S0：DXGI Desktop Duplication 捕获（DXcam）
- S0：监视器感知、多显示器路由、窗口越界裁剪
- S0：ROI/网格级局部变化、新鲜帧、黑帧检查
- S0：区分 `NO_NEW_PRESENT`、`WINDOW_UNAVAILABLE` 与真正 capture error
- S0：没有独立 liveness witness 时，stale KPI 显式为 `null`
- S0：S4 timer witness 激活后，连续 `NO_NEW_PRESENT` 也可以成为冻结证据
- S1：透明 Overlay、点击穿透、`WDA_EXCLUDEFROMCAPTURE`
- S1：签名式 probe（品红边框 + 青色十字），generic diff 只作诊断
- S2：真实游戏 viewport 检测
- S2：ORB + RANSAC 全仿射参考坐标注册
- S3：版本化模板信号 → `PREPARATION / COMBAT / UNKNOWN`
- S3：防抖状态机，不允许一帧切阶段
- S4：数字模板 OCR + 可选弧形进度双通道
- S4：timer temporal validation + preparation liveness witness
- S0~S4 证据与门禁
- Linux + Windows CI

**尚未实现**：S5 时间冻结假计划，以及 Product 层的英雄 OCR、GameState、策略评分、数据库、LLM、自动点击/拖拽。

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

## 1. S−1：检查运行形态

```powershell
qijing-inspect --title 王者
```

输出 HWND / PID / EXE / Window Class、物理像素客户区、进程父子树、监视器信息与运行形态候选。

`UNKNOWN` 是允许结果；不要为了“有答案”强猜原生 PC。

## 2. S0 基础捕获

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
├─ phase.json
├─ timer.json
├─ liveness.json
├─ no_new_presents.json
├─ window_unavailable.json
└─ capture_gaps.json
```

DXcam one-shot `grab()` 没有新呈现时可能返回 `None`，本项目把它记录为 `NO_NEW_PRESENT`，不是 capture error。

窗口最小化或 monitor 暂不可解析记录为 `WINDOW_UNAVAILABLE`，与 backend 异常分开。

### S0 stale 语义

不带 S3/S4 profile 时：

```json
{
  "stale_rate": null,
  "stale_metric_status": "NO_WITNESS_DEFERRED_TO_S3_S4"
}
```

启用可信 preparation timer witness 后：

- 有新帧但超过 stale 时间没有有意义变化 → `STALE_SUSPECT`
- timer 明确应继续走，但 DXGI 持续没有新 present → `liveness.json` 记录 `stale_suspect=true`

没有 witness 时永远不能把 stale=0 当“没有冻结”。

## 3. S1 Overlay 排除

```powershell
qijing-spike --title 王者 --duration 30 --probe-overlay-exclusion
```

Probe 绘制程序完全知道的 72×72 签名：

```text
不透明饱和品红边框 + 不透明青色十字
```

判词只看 marker 自身签名：

- `WDA_NONE` 能看到，`WDA_EXCLUDEFROMCAPTURE` 后消失 → `PROVEN_WORKING / PASS`
- 排除后仍保留大部分 marker → `PROVEN_NOT_WORKING / DEGRADED_SHIPPABLE`
- 模糊残留 / 缓存帧 / 无新帧 / 正对照不可见 → `UNMEASURED`

游戏运动差分仍写入 evidence，但没有 S1 判决权。

## 4. S2 参考坐标注册

保存基准：

```powershell
qijing-spike --title 王者 --duration 5 --save-reference reference.jpg
```

验证拖动/缩放：

```powershell
qijing-spike --title 王者 --duration 30 --reference reference.jpg
```

注册使用 ORB + `estimateAffine2D`，支持 X/Y 非等比缩放；不安全时明确 FAILED，而不是安静返回错误矩阵。

## 5. S3/S4 Stage Profile

S3/S4 不把游戏 UI 硬编码进 Python。必须提供版本化 JSON profile：

```powershell
qijing-spike --title 王者 --duration 30 --stage-profile profiles/wangzhe-current.json
```

Profile 中定义：

- PREPARATION 模板信号 + ROI
- COMBAT 模板信号 + ROI
- timer ROI
- 可选 `0.png ... 9.png` 数字模板
- 可选弧形进度 HSV/几何参数

所有 ROI 都相对**真实游戏 viewport**，不是客户区。

完整 schema 和协议见：

```text
docs/EXP-003-phase-timer.md
```

## 6. S3 受控验收

完整 S3 PASS 必须带 ground truth，分两个受控片段跑：

```powershell
qijing-spike --title 王者 --duration 30 `
  --stage-profile profiles/wangzhe-current.json `
  --s3-ground-truth PREPARATION
```

以及：

```powershell
qijing-spike --title 王者 --duration 30 `
  --stage-profile profiles/wangzhe-current.json `
  --s3-ground-truth COMBAT
```

`--s3-ground-truth` **只用于评分**，不会输入分类器。

没有 ground truth 时，即使阶段输出看起来稳定，S3 最多 `DEGRADED_SHIPPABLE`，不能自证 PASS。

## 7. S4 Timer

数字通道：

```text
准备阶段 timer ROI
→ 二值化
→ 连通域
→ 单字归一化
→ 0~9 游戏内模板匹配
```

弧形通道：

```text
HSV active color + 中心/半径/环宽/方向
→ angular sampling
→ active fraction
→ remaining seconds
```

两通道都有效但差异超过 profile 的 `disagreement_seconds`：

```text
source = DISAGREE
valid = false
```

不会为了“持续有输出”强选一个。

只有以下条件同时成立，S4 才能给 S0 liveness witness：

```text
stable_phase == PREPARATION
TimerReading.valid == true
confidence >= witness_min_confidence
valid_streak >= witness_min_streak
remaining_seconds > 1
```

witness 有 TTL，旧 timer 读数不会无限延长活动预期。

## 8. S3/S4 证据

启用 `--stage-profile` 后重点查看：

```text
phase.json
timer.json
liveness.json
```

以及：

```text
evidence.json -> gates.S3
evidence.json -> gates.S4
evidence.json -> gates.S0.metrics.stale_metric_status
```

如果 S3/S4 没有受控 ground truth，门禁会明确保守降级。

## 9. 模拟器/串流 viewport

若已经知道内部游戏视口宽高比，例如 16:9：

```powershell
qijing-spike --title 王者 --duration 30 --viewport-aspect 1.7777778
```

该路线会明确标成低置信 `ASPECT_PRIOR`，不会冒充黑边检测成功。

## 10. 测试

```powershell
pytest
```

测试覆盖：

- freshness / black / legal static
- S0 witness/no-witness 语义
- S1 marker signature 正反向与运动混淆场景
- viewport / Rect
- ORB registration / 非等比缩放
- S3 模板分类 / ambiguous UNKNOWN / debounce
- S4 digit OCR / arc reader / timer witness
- S3/S4 gate ground-truth discipline
- no-present + active timer witness 的 stale 路径

## 11. 协议

- `docs/EXP-001-runtime-capture.md`
- `docs/EXP-002-spike-gates.md`
- `docs/EXP-003-phase-timer.md`

阈值的代码真值在 `qijing_spike.gates.SPIKE_THRESHOLDS`。
