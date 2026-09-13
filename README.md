# 棋镜 AI Coach — Spike

这是《王者万象棋》实时教练项目的 **Spike（风险验证工程）**。当前代码只验证 V1.3 的前置技术风险，不实现策略、AI、英雄数据库或自动操作。

## 当前范围

- S−1：运行形态识别（原生 PC / 模拟器 / 云串流候选）
- S0：DXGI Desktop Duplication 捕获（DXcam）
- S0：监视器感知、多显示器路由、窗口越界裁剪
- S0：ROI/网格级局部变化、新鲜帧、黑帧检查
- S0：区分 `NO_NEW_PRESENT`、`WINDOW_UNAVAILABLE` 与真正 capture error
- S0：没有独立 liveness witness 时，stale KPI 显式为 `null`，不会伪报 0
- S1：透明 Overlay、点击穿透、`WDA_EXCLUDEFROMCAPTURE`
- S1：`--probe-overlay-exclusion` 使用**已知 marker 签名**做正对照与排除验证
- S1：探针是 72×72 小型标记，签名为**品红边框 + 青色十字**
- S1：通用画面差分只作诊断，不再参与 PASS/FAIL 判词
- S2：真实游戏 viewport（黑边/letterbox + 可选宽高比先验）检测
- S2：ORB + RANSAC **全仿射**参考坐标注册，安全时才允许 SCALE_ONLY 降级
- S0/S1/S2 三值门禁：`PASS / DEGRADED_SHIPPABLE / FAIL`
- 完整证据 JSON（环境、present witness、全部 registration 样本与聚合）
- Linux + Windows CI 单元/导入测试

**尚未实现**：S3 阶段识别、S4 倒计时、S5 时间冻结假计划，以及 Product 层的英雄 OCR、GameState、策略评分、数据库、LLM、自动点击/拖拽。

## 当前 Spike 继续条件

在 S3/S4 尚未提供 liveness witness 前，S0 的完整 `PASS` 本来就不可达。因此当前阶段允许继续到 S3/S4 的条件是：

```text
S0 = DEGRADED_SHIPPABLE
且唯一未闭合项是 stale/freeze 无 witness
且 capture / black / sample-density 没有 FAIL
且 S1 / S2 没有 FAIL
```

也就是说，**不会为了等一个构造上不可达的 S0 PASS 而阻塞 S3/S4**；等阶段识别和倒计时 witness 接入后，再重新签发 S0 的完整 PASS/FAIL。

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
├─ window_unavailable.json
└─ capture_gaps.json
```

### DXGI / DXcam 语义

DXcam 的 one-shot `grab()` 在没有新的桌面呈现时可以返回 `None`。本 Spike 将它记录成：

```text
NO_NEW_PRESENT
```

而不是 capture failure。

窗口最小化或暂时无法解析监视器时记录：

```text
WINDOW_UNAVAILABLE
```

它与 backend 异常分开计，不进入 `capture_error_rate`。

如果实验显式允许复用上一帧，`CapturedFrame.reused_cached=True` 会保留该 provenance。**缓存帧不得用于证明 S1 排除成功。**

### stale / freeze 目前是“不可测”，不是“0”

Freshness 会记录局部活动与静止，但当前主循环还没有 S3/S4 提供的独立 activity witness。因此：

```json
{
  "stale_rate": null,
  "stale_metric_status": "NO_WITNESS_DEFERRED_TO_S3_S4"
}
```

这是有意设计：没有仪器时不允许把“0 次 stale”解释成“没有冻结”。在 S3/S4 接入阶段/倒计时 liveness witness 前，S0 最多只能得到 `DEGRADED_SHIPPABLE`，不能因为 stale=0 获得完整 PASS。

### new-present 样本量按时间归一化

证据里记录：

```text
new_present_frames_per_minute
sample_sufficiency
```

用于判断本次 Spike 是否拿到了足够测量样本；该指标是**测量充分性**，不是“游戏必须一直动”的产品规则。

## 3. 真正验证 S1 Overlay 污染

运行：

```powershell
qijing-spike --title 王者 --duration 30 --probe-overlay-exclusion
```

S1 不再根据“marker 区域比 baseline 变了多少”来猜 Overlay 是否存在。Probe 会绘制一套程序自己完全知道的签名：

```text
72×72 小标记
不透明饱和品红边框
+
不透明青色十字
```

流程：

```text
F0：隐藏 Overlay，取得非缓存 baseline
 ↓
WDA_NONE + viewport 内显示签名 marker
 ↓
F+：取得新的桌面帧
 ↓
直接检测品红边框 + 青色十字的几何覆盖率
 ↓
正对照必须确认 marker 确实可见
 ↓
WDA_EXCLUDEFROMCAPTURE
 ↓
F−：取得新的桌面帧
 ↓
再次检测同一个 marker 签名
```

判词基于**marker 自身是否存在**：

- `F+` 能稳定看到签名，`F−` 签名消失 → `PROVEN_WORKING / PASS`
- `F+` 能看到签名，`F−` 仍保留大部分签名 → `PROVEN_NOT_WORKING / DEGRADED_SHIPPABLE`
- 签名只部分残留、证据处于模糊区 → `UNMEASURED / DEGRADED_SHIPPABLE`

通用 diff 指标（例如 `excluded_signal_mean`、`signal_reduction_mean`、控制块 motion）仍保存到 evidence 中，但**只用于诊断游戏运动，不拥有判决权**。

因此：

```text
F+ 时游戏运动很强
F− 时游戏运动变弱
但 marker 在 F− 仍然可见
```

也不能再因为 generic diff reduction 很大而误签 `PROVEN_WORKING`。

任一测量帧来自缓存、拿不到新帧、marker 被裁剪、正对照检测不到签名、affinity API 失败或签名证据落在模糊区，都不能 PASS。

如果游戏内 Overlay 无法证明干净，但外置面板可用，S1 为 `DEGRADED_SHIPPABLE`。

探针运行时间从 S0 的 sample-density 观察时长中扣除，并重置 gap/freshness 基线，不污染 S0。

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

当前测试重点覆盖：

- 小范围真实 UI 变化
- 合法静止不因历史活动被误报 stale
- 外部 activity witness 可触发 stale
- 无 witness 时 stale KPI 为 `null`
- new-present 样本密度随实验时长缩放
- `WINDOW_UNAVAILABLE` 不作为 capture error
- S1 正对照必须真的检测到 marker 签名
- marker 区局部运动存在时，排除成功仍可 PASS
- **F+ 运动强 / F− 运动弱但 marker 仍在时，不得假认证 PASS**
- marker 保留时输出 `PROVEN_NOT_WORKING`
- 部分残留的模糊签名不能被强行认证
- 缓存帧不能产生 S1 假 PASS
- 正对照不可见时产生 `UNMEASURED`
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
