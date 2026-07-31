# Lab Asset Agent v0.3.2

一个在本地驱动 Blender 5.2、通过 OpenAI-compatible API 迭代生成实验室仪器 3D 资产的轻量 agent。

新版不再采用“GPT 看图给建议 → DeepSeek 再改代码”的两段式流程。后续每一轮由 GPT-4o 同时读取：

- 仪器规格；
- 共享 Blender 工具库；
- 烧杯参考脚本；
- Blender/项目文档；
- **产生本轮渲染图的精确仪器脚本快照**；
- 多个 Blender 渲染视角。

GPT 在同一次请求中完成视觉评审，并在需要修改时直接输出下一版完整 Blender Python 脚本。

```text
仪器 YAML
   │
   ├── 初始脚本：DeepSeek（默认，可选）或 GPT
   ▼
本地静态检查 → Blender 后台建模与多视角渲染
   ▼
GPT-4o：规格 + 工具代码 + 当前代码 + 多视角图片
   │
   ├── pass：保存 final
   └── revise：同一响应直接返回完整下一版脚本
                         │
                         └────────→ 下一轮 Blender
```

运行时不依赖 Claude Code、Claude Agent SDK、Qwen3-VL 或 vLLM。

## 设计原则

- **初始模型可比较。** `initial_generator: deepseek` 使用 DeepSeek 生成第一版；改为 `gpt` 时，第一版也由 GPT 生成。
- **后续只有一个决策模型。** 首轮渲染之后，不再把 GPT 的建议转交给 DeepSeek；GPT 自己看代码、看图并修改代码。
- **精确代码—图像配对。** GPT 读取 `iteration_N/instrument.py`，即真正产生该轮图片的脚本，不会误用工作区中的其他版本。
- **跨轮 issue 记忆。** 每次评审产生的 minor / moderate / major / critical issue 都会按时间顺序写入
  `issue_history.json`，后续看图修订和 Blender 报错修复都会收到完整历史，用于防止旧问题回归。
- **只评几何，不评环境光。** 黑暗、反光不明显、透明感弱、曝光和阴影等被视为环境光照现象；只要几何仍可辨认，
  不会因此降分或修改灯光/材质。
- **无结构化输出兼容问题。** 合并响应使用 `<REVIEW_JSON>` 和 `<BLENDER_SCRIPT>` 标签，不发送 `response_format`。
- **实时流式输出。** 初始模型和 GPT 迭代模型都使用 `stream=True`；最终响应边生成边显示并同步写入 partial 文件。
- **可续跑。** 已完成的初始模型调用、Blender 渲染和模型响应都会持久化，不必因中断重新生成第一版。
- **工具库受保护。** 模型只修改仪器脚本，工具库和参考脚本运行后会校验并恢复。

## 目录结构

```text
lab_asset_agent/
├── config.example.yaml
├── AGENT_RULES.md
├── CLAUDE.md
├── pyproject.toml
├── src/lab_asset_agent/
│   ├── cli.py
│   ├── config.py
│   ├── models.py
│   ├── openai_compatible.py
│   ├── code_writer.py          # 只负责第一版脚本
│   ├── vision_coding_agent.py  # GPT 看代码+图片并直接修改
│   ├── blender_runner.py
│   ├── validator.py
│   └── orchestrator.py
├── workspace/
│   ├── toolkit/lab_blender_toolkit.py
│   ├── references/beaker_low_250ml_reference.py
│   ├── docs/blender_context.md
│   ├── specs/
│   └── generated/
├── runs/
└── tests/
```

## 1. 安装

建议使用 Python 3.11 或 3.12：

```powershell
cd lab_asset_agent
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e ".[dev]"
```

设置密钥：

```powershell
$env:DEEPSEEK_API_KEY="sk-..."
$env:VECTOR_ENGINE_API_KEY="sk-..."
```

当 `initial_generator: gpt` 时，不需要 `DEEPSEEK_API_KEY`。

## 2. 配置

config.yaml

### 对比 DeepSeek 和 GPT 的第一版

使用 DeepSeek：

```yaml
models:
  initial_generator: deepseek
```

使用 GPT：

```yaml
models:
  initial_generator: gpt
```

其余后续循环完全相同，便于公平比较第一版脚本质量。

## 3. 检查配置

不调用付费 API：

```powershell
lab-asset-agent check-config -c config.yaml
```

它会显示：

- Blender 路径；
- 工具库、参考代码、文档路径；
- 第一版脚本路由和模型；
- GPT iteration agent；
- 所需环境变量是否存在。

## 4. 生成一个仪器

```powershell
lab-asset-agent generate workspace/specs/beaker_low_250ml.yaml -c config.yaml
```

锥形瓶示例：

```powershell
lab-asset-agent generate workspace/specs/erlenmeyer_250ml.yaml -c config.yaml
```

## 5. 运行时流式输出

初始模型与 GPT 迭代模型默认都使用 OpenAI-compatible streaming。最终响应会边生成边打印：

```text
Run created: runs/...
Calling initial generator: deepseek-reasoner (route=deepseek)...
[lab-asset-agent] Streaming model response: initial generator deepseek-reasoner
[lab-asset-agent] initial generator deepseek-reasoner: reasoning received 3,072 chars
[lab-asset-agent] --- response stream ---
<BLENDER_SCRIPT>
...脚本逐步出现...
</BLENDER_SCRIPT>
[lab-asset-agent] Stream complete: 8,421 response chars, 12,804 reasoning chars
Initial script saved: workspace/generated/...
──────────────── Iteration 1 ────────────────
Starting Blender...
Blender finished in 42.1s; images=3, success=True
Calling GPT review+coder: gpt-4o with exact code and 3 image(s)...
[lab-asset-agent] Streaming model response: GPT review+coder iteration 1 (gpt-4o)
<REVIEW_JSON>...逐步出现...</REVIEW_JSON>
<BLENDER_SCRIPT>...下一版完整代码...</BLENDER_SCRIPT>
Visual score: 7.80/10, verdict=revise
```

流式配置：

```yaml
stream: true               # 使用 API streaming
stream_to_terminal: true   # 将最终响应实时打印到终端
stream_reasoning: progress # hidden | progress | full
```

`progress` 是默认值：DeepSeek Reasoner 的隐藏推理不会原样刷屏，但终端持续显示已接收字符数。设为 `full` 会打印 API 返回的完整 `reasoning_content`；设为 `hidden` 则只打印最终脚本。

流式内容还会同步写入文件。若连接中断，可以检查：

```text
workspace/generated/<id>.initial_response.partial.txt
runs/<run>/iteration_N/gpt_review_and_code_response.partial.txt
runs/<run>/iteration_N/repair_agent_response.partial.txt
```

请求成功后，对应完整响应会保存为不带 `.partial` 的文件。

在 GPT 调用期间不会再出现：

```text
Endpoint rejected json_schema
Endpoint rejected json_object
```

因为合并响应根本不发送 `response_format`。

## 6. GPT 实际收到什么

每轮的文本内容包含：

```text
TARGET SPECIFICATION
PRIOR MODERATE-OR-HIGHER ISSUE HISTORY (REGRESSION MEMORY)
AGENT RULES
BLENDER/PROJECT DOCUMENTATION
REFERENCE INSTRUMENT SCRIPT
SHARED TOOLKIT
CURRENT EXACT INSTRUMENT SCRIPT THAT PRODUCED THESE IMAGES
```

之后追加 JPEG base64 多视角图片：

```python
{
    "type": "image_url",
    "image_url": {
        "url": "data:image/jpeg;base64,..."
    }
}
```

响应协议：

```text
<REVIEW_JSON>
{
  "verdict": "revise",
  "overall_score": 7.8,
  "issues": [...],
  "preserve": [...],
  "summary": "增大倒液嘴径向延伸，并保持器身比例。"
}
</REVIEW_JSON>
<BLENDER_SCRIPT>
完整的下一版 Python 文件
</BLENDER_SCRIPT>
```

若通过，则 `verdict=pass`，并省略 `<BLENDER_SCRIPT>`。

## 7. 每轮保存内容

```text
runs/<run-id>/
├── spec.json
├── manifest.json
├── issue_history.json                  # 所有历史 minor 以上问题
├── iteration_01/
│   ├── instrument.py                     # 产生本轮图片的精确脚本
│   ├── render/
│   │   ├── *.png
│   │   ├── *.blend
│   │   └── blender.log
│   ├── review.json
│   ├── gpt_review_and_code_response.txt  # GPT 原始合并响应
│   └── next_instrument.py                 # verdict=revise 时的下一版
├── iteration_02/
└── final/
```

渲染失败时会保存：

```text
repair_agent_response.txt
```

## 8. 中断后续跑

不要重新执行 `generate`。续跑最近一次任务：

```powershell
lab-asset-agent resume -c config.yaml
```

指定任务目录：

```powershell
lab-asset-agent resume `
  runs\20260731TxxxxxxxxxxxxZ_beaker_low_form_250ml `
  -c config.yaml
```

恢复规则：

- 已生成第一版、未渲染：直接运行 Blender；
- 已渲染但 GPT 调用失败：复用脚本和图片，调用新的 GPT review+coder；
- GPT 已写出下一版、尚未渲染：直接渲染，不重复 GPT；
- 旧 v0.2 run 已有独立 review、但没有新脚本：GPT 会重新读取旧图片和精确代码，在一次请求中完成评审和修改；
- 上轮已经通过：直接整理 `final/`。

## 9. Blender 失败时

如果静态检查或 Blender 执行失败，后续不再调用 DeepSeek。GPT iteration agent 会收到：

- 目标规格；
- 完整工具与文档；
- 精确失败脚本；
- Blender 错误日志。

然后直接返回完整修复脚本。

## 10. 新增工具：平滑轮廓与外轮廓线

平滑旋转体剖面：

```python
OUTER_PROFILE = lab.smooth_profile_from_mm(
    [(34, 0), (35, 4), (43, 55), (18, 92), (18, 110)],
    samples_per_segment=10,
    sharp_indices={0, 1, 4},
)
```

该工具使用形状保持的 PCHIP 插值，适合瓶腹、肩部、颈部等连续曲面。用于内壁时，容量和刻度计算也必须使用同一份
平滑后的 `INNER_PROFILE`。

可选的诊断外轮廓：

```python
lab.enable_freestyle_outline(
    thickness_px=1.25,
    include_open_borders=True,
    include_creases=False,
)
```

它只在渲染结果上叠加 Freestyle 轮廓，不会修改 mesh，也不能替代几何修复。

## 11. 批量运行

```powershell
lab-asset-agent batch workspace/specs -c config.yaml
```

每个 YAML 独立创建 run，顺序执行。

## 11. 安全边界

生成脚本在启动 Blender 前会进行 AST 检查，拒绝：

- `subprocess`、`socket`、`requests`、`httpx` 等；
- `eval`、`exec`、`compile`；
- `os.system`、`os.remove`、`shutil.rmtree` 等；
- 不满足 `build_asset()`、主入口、输出目录和渲染契约的脚本。

Blender 使用：

```text
--background --factory-startup --offline-mode --python-exit-code 1
```

这属于防御性限制，不等同于操作系统级沙箱。大规模运行建议使用独立用户、虚拟机或容器。

## 12. 测试

```
cd lab_asset_agent
& "blender.exe" `
  --background `
  --factory-startup `
  --offline-mode `
  --python-use-system-env `
  --python-exit-code 1 `
  --python "$project\workspace\references\beaker_low_250ml_reference.py"
```
