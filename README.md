# 棋镜 AI Coach — Spike

这是《王者万象棋》实时教练项目的 **Spike（风险验证工程）**。当前代码只验证 V1.3 的前置技术风险，不实现策略、AI、英雄数据库或自动操作。

## 当前范围

- S−1：运行形态识别（原生 PC / 模拟器 / 云串流候选）
- S0：DXGI Desktop Duplication 捕获（DXcam）
- S0：新鲜帧、黑帧、重复帧健康检查
- S1：外置透明 Overlay、点击穿透、`WDA_EXCLUDEFROMCAPTURE`
- S2：真实游戏 viewport（黑边/letterbox）检测
- S2：AKAZE + RANSAC 参考坐标注册，失败时 SCALE_ONLY 降级
- 基础证据 JSON 输出
- 纯函数单元测试

**当前明确不做**：英雄 OCR、GameState、策略评分、数据库、LLM、自动点击/拖拽。

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

列出可见窗口：

```powershell
qijing-inspect
```

按标题筛选：

```powershell
qijing-inspect --title 王者
```

它会输出：

- HWND / PID / EXE / Window Class
- 客户区物理像素坐标
- 进程父子树
- `NATIVE_PC / ANDROID_EMULATOR / CLOUD_STREAM / UNKNOWN` 候选
- 判定理由

> 运行形态是启发式分类，`UNKNOWN` 是正常结果；不要为了“有答案”强猜。

## 2. 跑 S0/S1/S2 Spike

```powershell
qijing-spike --title 王者 --duration 60 --overlay
```

输出目录：

```text
artifacts/spike-YYYYMMDD-HHMMSS/
├─ evidence.json
├─ first_frame.jpg
├─ viewport.jpg
└─ health.json
```

默认捕获 **游戏客户区**，Overlay 放在客户区外侧，因此即使系统不支持捕获排除，主捕获区域也不应包含面板。

## 3. 参考坐标注册

先保存一张基准图：

```powershell
qijing-spike --title 王者 --duration 5 --save-reference reference.jpg
```

再验证窗口缩放后的注册：

```powershell
qijing-spike --title 王者 --duration 30 --reference reference.jpg
```

注册优先使用 AKAZE + RANSAC；匹配不足时自动退化为 `SCALE_ONLY`，并把降级写进证据。

## 4. 测试

```powershell
pytest
```

## 重要限制

1. 当前主捕获后端是 DXcam（DXGI Desktop Duplication）；WGC 后端尚未加入。
2. `WDA_EXCLUDEFROMCAPTURE` 是否对实际机器/显示模式生效，必须实机验证。
3. Viewport 检测当前主要解决黑边/letterbox；模拟器复杂工具栏需要后续按真实运行形态补规则。
4. 这批代码允许被后续 Product 工程替换，不要在 Spike 阶段提前建设策略与数据平台。
