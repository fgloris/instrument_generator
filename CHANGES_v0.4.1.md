# v0.4.1 变更说明

## 1. `minor` 不再进入有效评审

- 新评审只允许 `moderate`、`major`、`critical` 三档可执行问题。
- prompt 明确要求省略 minor 观察和纯审美偏好。
- 解析层会丢弃模型偶发返回的 minor issue，不写入 `review.json`、`manifest.json` 或
  `issue_history.json`。
- 若模型以 `revise` 结论只返回 minor，解析会拒绝该无有效问题的修订结果。
- 旧 manifest 中的 minor 仍可读取，保证 `resume` 兼容，但不会再次传入模型。

## 2. 删除重复提示词

- `AGENT_RULES.md` 只保留跨阶段共享的 Blender 脚本合同。
- 三轴评审、`retake_views`、光照忽略、评分和 severity 规则只保留在
  `REVIEW_SYSTEM_PROMPT`，不再在规则文件和用户提示中重复。
- 初始生成 user prompt 删除了环境变量、多视角和输出格式的重复说明。
- render-failure repair 不再携带参考烧杯脚本和完整项目文档，只发送脚本合同、工具库、当前脚本和错误日志。
- 迭代评审不再重复发送参考烧杯脚本；当前精确脚本和工具库已经足够用于直接修订。
- issue history 改为紧凑 JSON，减少历史轮次增长造成的 token 浪费。

## 3. 文件职责

- `CLAUDE.md`：仅供仓库维护工具阅读，不发送给运行时 API，因此不产生调用 token。
- `AGENT_RULES.md`：发送给运行时模型，但只描述生成脚本的共同约束。
- `vision_coding_agent.py` 的 system prompt：唯一的视觉评审协议来源。
