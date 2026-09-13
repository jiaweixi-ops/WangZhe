# EXP-002 — Spike 三值门禁协议

每项输出：`PASS / DEGRADED_SHIPPABLE / FAIL / NOT_RUN`。

## Spike 退出 / 继续条件

当前版本在 S3/S4 尚未提供独立 liveness witness 前，S0 的完整 `PASS` 在构造上不可达。这不是阻塞后续工作的理由。

允许进入 S3/S4 的条件是：

```text
S0 = DEGRADED_SHIPPABLE
且唯一未闭合项是 stale/freeze 无 witness
且 capture/black/sample-density 没有 FAIL
且 S1/S2 没有 FAIL
```

也就是说：

> **S0=DEGRADED_SHIPPABLE（仅因 NO_WITNESS_DEFERRED_TO_S3_S4）视为当前 Spike 阶段可继续。**

S3/S4 接入阶段/倒计时 activity witness 后，再重新签发 S0 的完整 PASS/FAIL。

---

## S0 捕获

阈值真值见 `qijing_spike.gates.SPIKE_THRESHOLDS["s0"]`。

### 关键语义

DXcam one-shot `grab()` 返回 `None` 时，默认解释为：

```text
NO_NEW_PRESENT
```

即当前桌面没有新的呈现帧，而不是捕获失败。

窗口最小化或 monitor 暂不可解析记录为：

```text
WINDOW_UNAVAILABLE
```

它与 backend capture error 分开计数。

Freshness 不能因为“近期画面曾经活动”就推断它之后必须一直变化。只有独立 witness 显式给出 `activity_expected=True` 时，合法静止才可能升级为 `STALE_SUSPECT`。

### stale KPI 必须允许不可测

在 S3/S4 尚未接入 activity witness 时：

```text
stale_rate = null
stale_metric_status = NO_WITNESS_DEFERRED_TO_S3_S4
```

不能把它写成 `0.0`。

无 stale witness 时，S0 即使其它 capture 指标健康，也只能得到 `DEGRADED_SHIPPABLE`，而不是完整 PASS。

### 样本量随时长归一化

当前使用：

```text
new_present_frames_per_minute
```

并输出 `sample_sufficiency`。该值代表实验是否拿到足够多的新呈现样本，不代表合法静止场景必须达到某个动画帧率。

---

## S1 Overlay

S1 的核心原则：

> **不要从“这块画面变了多少”推断 marker 是否存在；直接检测我们自己画的 marker。**

通用区域差分会把游戏运动与 Overlay 像素混在一起，已经证明会在不同场景下产生假 PASS 与假阴性。因此从本版本开始，通用 diff 只保留为诊断证据，完全退出 S1 判词。

### Probe 签名

Probe 仍使用约 72×72 的产品近似小标记，但测试样式是专门的测量签名：

```text
不透明饱和品红方框边缘
+
不透明青色十字
```

颜色、线宽和几何全部由程序自身定义。

探针在 marker 矩形内分别检测：

- 品红边框的几何覆盖率；
- 青色十字的几何覆盖率；
- `marker_score = min(border_coverage, cross_coverage)`。

这意味着普通游戏动画、粒子或血条变化不会仅因为“像素变化很大”而被当成 marker。

### 流程

1. Overlay 隐藏，取得一个非缓存 baseline `F0`；
2. 切换到签名 marker；
3. 显示 marker 到 viewport 内，要求 marker 完整未裁剪；
4. 设置 `WDA_NONE`；
5. 取得新的桌面呈现 `F+`；
6. 直接检测 `F+` 中的 marker 签名；
7. `F+` 必须达到 `min_positive_marker_score`，否则仪器没有看到已知阳性 → `UNMEASURED`；
8. 设置 `WDA_EXCLUDEFROMCAPTURE`；
9. 取得新的桌面呈现 `F−`；
10. 直接检测 `F−` 中的 marker 签名；
11. 根据 marker 是否消失/保留判定 affinity 是否工作。

### 判词

```text
positive marker score 足够
+
excluded marker score 足够低
+
retained fraction 足够低
→ PROVEN_WORKING / PASS
```

```text
positive marker score 足够
+
excluded marker 仍保留大部分签名
→ PROVEN_NOT_WORKING / DEGRADED_SHIPPABLE
```

```text
两者之间的模糊区
→ UNMEASURED / DEGRADED_SHIPPABLE
```

**模糊证据禁止强行判成功或失败。**

### 通用运动证据

以下旧指标仍可写入 evidence：

```text
positive_control_signal_mean
excluded_signal_mean
signal_reduction_mean
control block motion
```

但它们只用于排查游戏动画、采样时距等现象，**不参与 S1 PASS/FAIL**。

因此，即使出现：

```text
F+ 游戏运动很强
F− 游戏运动变弱
```

只要 marker 在 F− 仍然存在，就不能因为 generic diff reduction 很大而错误签发 `PROVEN_WORKING`。

### 正交结果字段

```yaml
exclusion_outcome:
  PROVEN_WORKING
  PROVEN_NOT_WORKING
  UNMEASURED
```

以下任一情况禁止 PASS：

- baseline / positive / excluded 任一帧来自 cache；
- 正对照拿不到新帧；
- marker 被 viewport/client 裁剪；
- `F+` 检测不到已知 marker 签名；
- 开启 exclusion 后拿不到新的阴性测量帧；
- affinity API 失败；
- marker 签名证据落在模糊区。

如果游戏内 Overlay 无法证明干净，但外置面板可用：`DEGRADED_SHIPPABLE`。

Probe 自身的 sleep、affinity 切换和 compositor 更新不计入 S0 gap/freshness 基线，也从 S0 sample-density 观察时长中扣除。

---

## S2 Registration

PASS 不能只看最后一帧，也不能只看 RANSAC inlier 残差。

证据必须包含：

- 全部样本 mode 分布
- accepted/FAILED/SCALE_ONLY 比率
- inlier 数/比例
- inlier error
- all-good-match mean/P90 error
- scale_x / scale_y
- confidence 分布

任意拖边导致宽高比变化时，主模型必须能表达非等比缩放；降级 SCALE_ONLY 只能在宽高比近似一致时使用。
