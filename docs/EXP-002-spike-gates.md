# EXP-002 — Spike 三值门禁协议

每项输出：`PASS / DEGRADED_SHIPPABLE / FAIL / NOT_RUN`。

## S0 捕获

阈值真值见 `qijing_spike.gates.SPIKE_THRESHOLDS["s0"]`。

### 关键语义

DXcam one-shot `grab()` 返回 `None` 时，默认解释为：

```text
NO_NEW_PRESENT
```

即当前桌面没有新的呈现帧，而不是捕获失败。

因此 S0 不再使用 `none_grab_rate` 或“新帧之间的 gap”直接判 FAIL。它们只作为证据保留；真正的 capture error 是异常、monitor/geometry 错误等明确失败。

Freshness 也不能因为“近期画面曾经活动”就推断它之后必须一直变化。只有独立 witness 显式给出 `activity_expected=True` 时，合法静止才可能升级为 `STALE_SUSPECT`。

PASS 当前要求：

- 有足够数量的真实 new-present 样本；
- 黑帧率低；
- 显式 stale 率低；
- capture error 率低。

## S1 Overlay

S1 的原则是：

> 一个探测不到已知阳性信号的方法，不能用来证明阴性。

因此 PASS 必须运行 `--probe-overlay-exclusion`，且严格执行正对照：

1. Overlay 隐藏，取得一个 **非缓存** baseline `F0`；
2. 显示 Overlay 到 viewport 内；
3. 显式设置 `WDA_NONE` 关闭排除；
4. 必须取得一个新的桌面呈现 `F+`；
5. `F+` 相对 `F0` 必须超过正对照信号阈值，证明测量链能看到 Overlay；
6. 设置 `WDA_EXCLUDEFROMCAPTURE`；
7. 必须再次取得一个新的桌面呈现 `F−`；
8. `F−` 相对 `F0` 的 Overlay 信号必须下降到阈值内，并达到最小 signal reduction；
9. 同时记录两次 `SetWindowDisplayAffinity` 的返回值和 last-error。

以下任一情况禁止 PASS：

- baseline / positive / excluded 任一帧来自 cache；
- 正对照拿不到新帧；
- 正对照看不到 Overlay；
- 开启排除后拿不到新的阴性测量帧；
- affinity API 失败；
- 排除后的信号仍超过阈值。

如果游戏内 Overlay 无法证明干净，但外置面板可用：`DEGRADED_SHIPPABLE`。

Probe 自身的 sleep、affinity 切换和 compositor 更新不计入 S0 的 gap/freshness 基线。

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
