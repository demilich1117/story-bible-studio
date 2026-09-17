# Agent 小票接手指南

## 只加载一次

未加载时读取小票工作区的 `AGENTS.md` 和 [Story Bible Studio 技能](../.agents/skills/story-bible-studio/SKILL.md)（MCP：`studio://skill`），按任务选择必要参考。同一任务不重复读取规范。本文件与 `studio://guide` 内容相同，选一个入口即可。

主模型由平台或工作台选择。Luna/subagents 仅适用于 Codex；非 Codex 由主 Agent 完成整理和召回，压缩阈值仍有效，不调用辅助模型、不改写会话设置。

## 接手与提交

### 无小票的桌面自然语言构筑

目标作品明确但主题未知或上下文刚恢复时，用 studio_bible_continue 获取聊天接续位置；已知主题直接 studio_bible_prepare。没有 MCP 使用 CLI workbench 的 bible_continue／bible_prepare，同样不需要启动服务器或生成小票。接续收据的 ready 只表示目标明确，prepare 返回 ready 并读完分段后才起草。needs_topic 只确认主题；recovery_required 用 bible_operation 恢复原操作，不猜面板选择。

用户明确修改已有设定时，用 bible_revise_prepare 直接准备修订。普通提交中用 next_topic 将 next_prompt 交给新主题或已有开放主题，成功收据给出下一轮目标。必需关联用 required_related；新的事实追踪用 applied_facts 记录正文落实。完整合同见构筑参考。方向已经明确就直接落实，不强制出菜单或按轮数停下来整理。

### 无小票的桌面自然语言 RP

用户明确新建时，先获取指定作品的项目 version 与 Profile ID，再走 new_session；此时还没有会话 version，不能先调用会话 status。完整参数及作品名／项目路径区别见 [新建会话与项目参数](../.agents/skills/story-bible-studio/references/roleplay-v3.md#新建会话与项目参数)。创建成功后再进入下面的 status → prepare 流程。

与小票复用同一核心：目标明确时用 studio_status 获取会话 version（已有有效版本可省略），再 studio_prepare 一次。无 MCP 用结构化 CLI workbench --operation status/prepare，参数相同，默认短收据。不需要用户生成小票或启动服务器。目标不明只确认作品／会话，不枚举目录；面板选择不等于聊天目标。准备后的分段读取、commit_contract、提交与异常恢复完全沿用下面规则。不要退回传统 turn prepare 的整包读取路径。

### 精简交付与普通轮成本

小票和 MCP 默认返回 `context_parts` 清单，不再内联整个上下文。每段不超过 12 KiB UTF-8，按 order 顺序读取所有分段一次，路径和 sha256 均绑定本次 operation_id；原文与重生成使用各自快照。清单足以定位材料，不枚举目录、不检查 JSON 形状、不读旧事务或旧上下文包、不重复 prepare。只有明确诊断才使用恢复接口。旧客户端可显式请求 `context_delivery="inline"`（CLI ticket：`--context-delivery inline`）。

正文按软目标一次完整起草，普通轮不调用计数工具、不反复修改或补写找补、不重读已写草稿来凑字。核心提交自动计数；成功后的 prose_warning 不是重写指令。只有用户明确要求严格字数或改写时才进行针对性校验。普通轮只做意图、人物基线、连续性和停点检查；不要求逐关填表。

motifs 仅填写本轮实际使用的 stable-kebab-case ID，如 old-clock；收据中的 motif_ids 为相关既有 ID，无实际使用时省略，不填写中文概念或占位项。历史证据不足时可使用 studio_recall(project, session, query, operation_id) 定向补查一次，同查询重试返回原结果，未命中不编造。

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
