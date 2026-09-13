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

不再使用“总共至少 5 个 NEW_PRESENT”作为 PASS 条件。

当前使用：

```text
new_present_frames_per_minute
```

并输出 `sample_sufficiency`。该值代表实验是否拿到足够多的新呈现样本，不代表合法静止场景必须达到某个动画帧率。

---

## S1 Overlay

S1 的原则是：

> 一个探测不到已知阳性信号的方法，不能用来证明阴性。

同时：

> 游戏自身运动不能被直接当成 Overlay 污染。

因此 PASS 必须运行 `--probe-overlay-exclusion`，并执行**正对照 + 空间对照 + affinity 差分**：

1. Overlay 隐藏，取得一个非缓存 baseline `F0`；
2. Probe 切换成产品近似几何的小型透明 marker，而不是 260×120 面板；
3. 显示 marker 到 viewport 内；
4. 显式设置 `WDA_NONE`；
5. 取得新的桌面呈现 `F+`；
6. 在 marker 重叠区域旁选择一个或多个同尺寸控制块；
7. 对 `F+` 计算空间归一化 marker signal；
8. 正对照必须超过阈值，证明 marker 本身可被测量；
9. 设置 `WDA_EXCLUDEFROMCAPTURE`；
10. 取得新的桌面呈现 `F−`；
11. 对 `F−` 做同样空间归一化；
12. 判词只依据 **`F+ → F−` 的 marker-signal reduction**；
13. `F−` 的绝对残留只保留作诊断证据，不能单独证明 exclusion 失败；
14. 同时记录两次 `SetWindowDisplayAffinity` 返回值和 last-error。

使用多个可用同尺寸控制块时，控制运动取中位值，以减少单一局部动画对结论的影响。

### 为什么不用 F− 的绝对残留判失败

marker 所在棋盘区域可能发生局部动画，而邻近控制块完全静止。此时即使 exclusion 已正确移除 marker：

```text
F− vs F0
```

仍可能有很大的绝对残留。

因此：

```text
excluded_signal 大
!=
exclusion 失败
```

只有在正对照成立后，**开启 affinity 没有产生足够的 marker-signal reduction**，才允许输出 `PROVEN_NOT_WORKING`。

### S1 正交结果字段

三值门禁之外必须保留：

```yaml
exclusion_outcome:
  PROVEN_WORKING
  PROVEN_NOT_WORKING
  UNMEASURED
```

含义：

- `PROVEN_WORKING`：正对照成立，开启 exclusion 后 marker-signal reduction 达到阈值；
- `PROVEN_NOT_WORKING`：正对照成立，但 affinity API 失败，或 affinity 切换没有产生足够差分；
- `UNMEASURED`：没有拿到新帧、缓存帧、正对照不成立、没有控制块等，无法证明正/负。

这样“已证明失败”和“本次没测到”即使都因外置面板 fallback 得到 `DEGRADED_SHIPPABLE`，证据语义仍然不同。

以下任一情况禁止 PASS：

- baseline / positive / excluded 任一帧来自 cache；
- 正对照拿不到新帧；
- 空间归一化后看不到 marker-specific 正对照；
- viewport 内无法放置同尺寸控制块；
- 开启排除后拿不到新的阴性测量帧；
- affinity API 失败；
- affinity 切换后的 marker-signal reduction 低于阈值。

注意：

> **排除后仍存在较大的绝对游戏运动，不再是失败条件。**

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
