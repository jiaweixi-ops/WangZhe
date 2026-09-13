# EXP-003 — S3/S4 阶段识别、倒计时与 liveness witness 协议

## 1. 目的

验证三个独立结论：

1. S3 能否在受控场景下稳定区分 `PREPARATION / COMBAT / UNKNOWN`；
2. S4 能否在准备阶段读取一个时间上自洽的倒计时；
3. 只有当 S3/S4 同时提供足够可信的证据时，才能把 `activity_expected=True` 交给 S0。

本协议不涉及英雄、商店、阵容或策略。

---

## 2. 不允许的捷径

禁止：

- 用“整屏很动”直接推断 `COMBAT`；
- 用“整屏很静”直接推断 `PREPARATION`；
- 没有 ground truth 就给 S3 签完整 PASS；
- 没有稳定准备阶段 + 有效 timer 就给 S0 生成 activity witness；
- timer 两通道明显冲突时仍强行选一个结果；
- 把 `NO_NEW_PRESENT` 单独当冻结。只有已有有效 witness 时，持续 no-present 才是 stale 证据。

---

## 3. Stage Profile

S3/S4 使用一个版本化 JSON profile，不把游戏 UI 坐标和模板写死在代码里。

示例：

```json
{
  "version": 1,
  "phase": {
    "min_support": 0.55,
    "min_margin": 0.20,
    "confirm_frames": 3,
    "strong_confirm_frames": 2,
    "strong_confidence": 0.90,
    "unknown_reset_frames": 5,
    "signals": [
      {
        "name": "prep_shop_anchor",
        "phase": "PREPARATION",
        "roi": [0.70, 0.70, 0.99, 0.99],
        "template": "templates/prep_shop_anchor.png",
        "match_threshold": 0.86,
        "weight": 1.0,
        "expected_present": true
      },
      {
        "name": "combat_indicator",
        "phase": "COMBAT",
        "roi": [0.40, 0.00, 0.60, 0.18],
        "template": "templates/combat_indicator.png",
        "match_threshold": 0.86,
        "weight": 1.0,
        "expected_present": true
      }
    ]
  },
  "timer": {
    "roi": [0.44, 0.01, 0.56, 0.15],
    "min_confidence": 0.70,
    "witness_min_confidence": 0.82,
    "witness_min_streak": 2,
    "disagreement_seconds": 1.5,
    "digit": {
      "templates_dir": "templates/digits",
      "min_digit_score": 0.52,
      "max_digits": 3
    },
    "arc": {
      "center": [0.5, 0.5],
      "radius": 0.40,
      "thickness": 0.08,
      "start_angle_deg": -90,
      "sweep_angle_deg": 360,
      "clockwise": true,
      "hsv_min": [45, 120, 120],
      "hsv_max": [80, 255, 255],
      "max_seconds": 30,
      "active_represents_remaining": true
    }
  }
}
```

所有 ROI 都相对**游戏 viewport**，不是整个窗口客户区。

---

## 4. S3 阶段识别

### 4.1 信号

第一版只允许使用明确的固定 UI 信号：

- 准备阶段商店/刷新区域稳定图标；
- 准备阶段固定标题或装饰；
- 战斗阶段固定状态标识；
- 其它经过实机确认的稳定 UI anchor。

每个信号独立返回 template match score。

### 4.2 原始分类

分别计算：

```text
PreparationSupport
CombatSupport
```

只有：

```text
best_support >= min_support
且
abs(prep-combat) >= min_margin
```

才允许产生原始 `PREPARATION` 或 `COMBAT`；否则必须是 `UNKNOWN`。

### 4.3 状态机

原始分类不能直接成为稳定 phase。

使用：

- 普通信号连续 N 帧确认；
- 极高置信度仍至少连续 2 帧；
- 连续 UNKNOWN/低置信超过阈值后才重置稳定状态。

禁止“一帧切状态”。

---

## 5. S3 受控验收

S3 完整 PASS 必须分开跑两个受控片段：

```powershell
qijing-spike --title 王者 --duration 30 --stage-profile <profile.json> --s3-ground-truth PREPARATION
qijing-spike --title 王者 --duration 30 --stage-profile <profile.json> --s3-ground-truth COMBAT
```

`--s3-ground-truth` 只用于实验评分，不参与分类器输入。

没有 ground truth 的正常运行即使看起来稳定，也最多 `DEGRADED_SHIPPABLE`，不能自证 PASS。

当前代码阈值真值在：

```text
SPIKE_THRESHOLDS["s3"]
```

---

## 6. S4 数字通道

数字通道不用通用 OCR 大模型。

输入是准备阶段 timer ROI，模板目录包含：

```text
0.png ... 9.png
```

流程：

```text
ROI
→ Otsu 二值化
→ 连通域
→ 单字归一化
→ 与 0~9 模板比较
→ 秒数
```

每一位必须达到 `min_digit_score`。

模板应来自**目标游戏当前版本自身 UI**，不要使用随便的字体生成图作为生产模板。

---

## 7. S4 弧形通道

若准备计时器还有环形/弧形进度，可配置第二通道：

```text
HSV active color
中心
半径
环宽
起始角
扫过角度
方向
最大秒数
```

沿圆环采样 active fraction，并换算成剩余秒数。

该通道必须通过目标游戏截图校准 HSV 与几何参数。

---

## 8. 两通道融合

如果 digit + arc 都有效：

```text
abs(digit_seconds - arc_seconds) <= disagreement_seconds
```

才允许融合。

超过容差：

```text
source = DISAGREE
valid = false
```

不能为了持续输出而选一个“看起来更像”的结果。

只有一个通道有效时可以降级使用，但证据里必须保留 source。

---

## 9. 时间一致性

同一个 PREPARATION 内：

- timer 不应无理由大幅增加；
- timer 不应以远超真实时间的速度暴跌；
- phase 退出 PREPARATION 时清空 timer temporal state；
- 再次进入 PREPARATION 时建立新 baseline。

这些检查用于拒绝 OCR 跳字，不用于宣称 timer 绝对准确。

---

## 10. Activity Witness

S0 只有在以下条件全部成立时才收到 `activity_expected=True`：

```text
stable_phase == PREPARATION
TimerReading.valid == true
TimerReading.confidence >= witness_min_confidence
连续有效次数 >= witness_min_streak
remaining_seconds > 1
```

输出 witness 有 TTL，不会因为一次旧读数无限延长。

### 10.1 有新 present

如果 witness 有效但新帧连续超过 stale 阈值没有任何有意义变化：

```text
STALE_SUSPECT
```

### 10.2 无新 present

如果 witness 仍有效，但 DXGI 连续 `NO_NEW_PRESENT` 超过 stale 阈值：

```text
stale_suspect = true
reason = no new present while preparation timer witness is active
```

因此 S3/S4 接入后，S0 的冻结能力不再只依赖“下一帧终于回来”。

---

## 11. S4 验收

S4 PASS 只允许在受控 `PREPARATION` 运行中签发。

至少统计：

- preparation_samples
- valid_timer_rate
- monotonic_violation_rate
- activity_witness_rate
- source_counts（DIGIT / ARC / FUSED / DISAGREE）

没有 ground truth 的正常运行即使 timer 自洽，也最多 `DEGRADED_SHIPPABLE`。

阈值真值：

```text
SPIKE_THRESHOLDS["s4"]
```

---

## 12. 证据文件

启用 Stage Profile 后新增：

```text
phase.json
timer.json
liveness.json
```

`evidence.json` 新增：

```text
gates.S3
gates.S4
stage_profile
s3_ground_truth
phase_samples
timer_samples
liveness_samples
```

---

## 13. 当前范围边界

S3/S4 通过后才考虑 S5 假计划冻结。

本阶段禁止顺手加入：

- 英雄 OCR
- 商店识别
- GameState
- 买卖建议
- LLM
- 自动操作
