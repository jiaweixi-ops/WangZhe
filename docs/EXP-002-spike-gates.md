# EXP-002 — Spike 三值门禁协议

每项输出：`PASS / DEGRADED_SHIPPABLE / FAIL / NOT_RUN`。

## S0 捕获

阈值真值见 `qijing_spike.gates.SPIKE_THRESHOLDS["s0"]`。

PASS 要求黑帧、stale、None grab、gap 都处于严格低水平；DEGRADED 允许前台无遮挡等产品约束；超过降级阈值为 FAIL。

Freshness 的 stale 结论必须来自局部变化仪器和近期活动基线，不能来自一个全局 CLI “expected change” 开关。

## S1 Overlay

PASS 必须运行 `--probe-overlay-exclusion`：

1. viewport 内隐藏 Overlay 抓 F0；
2. viewport 内显示 Overlay 抓 F1；
3. 再次隐藏抓 F2；
4. 用 F0↔F2 衡量背景自身变化；
5. 验证 F1 没有额外 Overlay 信号；
6. 同时记录 `SetWindowDisplayAffinity` 成功/错误码。

若客户区内排除未被证明，但外置面板可用：`DEGRADED_SHIPPABLE`。

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
