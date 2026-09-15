# Agent 小票接手指南

## 只加载一次

未加载时读取小票工作区的 `AGENTS.md` 和 [Story Bible Studio 技能](../.agents/skills/story-bible-studio/SKILL.md)（MCP：`studio://skill`），按任务选择必要参考。同一任务不重复读取规范。本文件与 `studio://guide` 内容相同，选一个入口即可。

主模型由平台或工作台选择。Luna/subagents 仅适用于 Codex；非 Codex 由主 Agent 完成整理和召回，压缩阈值仍有效，不调用辅助模型、不改写会话设置。

## 接手与提交

1. 有小票时只调用一次 `studio_ticket(ticket_path)`，或执行小票自带的 `ticket --file` 命令。入口已准备或恢复原任务，**不要再读参数文件或重复 prepare**，不先列作品、会话。MCP 工作区不匹配时使用小票的 CLI。
2. 按返回 `kind` 分流：`bible/bible-recovery` 使用 [构筑接口](../.agents/skills/story-bible-studio/references/construction-workbench.md)，不加载 RP 流程；`session/recovery` 使用下面的 RP 提交规则。只读取小票指定的主题或会话，其他平台聊天和兄弟会话不能补上下文。
3. 只有 `ready` 才依据返回的上下文起草并复核。主 Agent 经核心提交成功后展示正文或构筑回答，不能只返回文本。沿用原 `operation_id`、目标和输入；重试原提交，不借用后来任务的 ID。非 ready 按下一节处理。

小票授权执行原请求，不授权直接改事件、删锁、归档、选择变体、冻结正史、运行 Git 或改平台配置。阻塞时保留原任务并说明。

## RP 提交规则

`studio_commit(project, session, operation_id, prose, status, scene_patch, state_updates, regenerate)`：沿用返回的 ID 和 regenerate；scene_patch 为 JSON 对象，state_updates 仅填写改变的五类既有状态文件，值为完整 Markdown。状态栏关闭仍维护动态状态。重生成只存候选，由用户选择。

本轮按上下文中的文风、输出配置和创作要求快照写作。字数为软目标，成功收据中的 prose_warning/requirements_check 不触发自动重写。

无 MCP 时，沿用小票的脚本路径和 `--workspace`，调用 `workbench --operation commit --payload-file <JSON>`；JSON 字段与 MCP 一致。直接聊天没有小票才使用 `studio_prepare`；参数与恢复细节按需查下节。

## 仅在需要时读取

- RP 恢复、创作要求变更、`needs_compaction/budget_blocked/stale/archived/damaged` 或锁占用：读 [创作要求与恢复](../.agents/skills/story-bible-studio/references/requirements-recovery.md)。压缩后按原请求重试，不增加用户回合；预算不足不能裁掉必需材料。
- `committed`：不重复正文；RP 的 `needs_finish=true` 用 `studio_operation(action="finish", operation_id=原ID, expected_version=返回版本)` 收尾。构筑 `pending` 按构筑接口恢复原 payload。
- 无小票的直接 RP、配置与记忆操作：读 [RP 工作流](../.agents/skills/story-bible-studio/references/roleplay-v3.md) 及其所需参考。面板切会话不会切换平台上下文，先确定目标，不自动复用其他会话历史。
