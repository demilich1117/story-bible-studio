# Agent 接入与交接

MCP 是可选工具入口，CLI 保留。主 Agent 负责正文、人物意图、状态补丁、记忆审核及版本选择；面板只执行用户明确发出的管理操作。

收到新短小票时直接用 `studio_ticket(ticket_path)` 或小票自带的 `ticket --file` 命令接手，不另读参数文件。返回 kind=bible/bible-recovery 时使用 [构筑接口](../.agents/skills/story-bible-studio/references/construction-workbench.md)，不进入下面的 RP 流程；返回 session/recovery 时继续按本指南操作。完整指令作为备用，旧小票仍可使用。同一任务已经加载的规范不重复读取。

1. 首次读取项目 `AGENTS.md` 和技能 `SKILL.md`，按任务读取必要参考。MCP 可先读取 `studio://guide`。
2. `studio_projects` 只列作品；`studio_project` 只列会话和设置元信息。只对用户指定的会话调用 `studio_session`，不检索兄弟会话正文。
3. 收到小票时调用 `studio_prepare(project, session, request_id)`。直接 RP 时先获取会话 `version`，调用 `studio_prepare(project, session, user_text, expected_version, override)`。用户原文不要改写。
4. `ready` 才读返回的 `context` 起草。`needs_compaction` 通过 `studio_memory` 准备并审核候选，再应用完整记忆，之后重新 prepare。不要把原始工具结果当成正文。
5. 正文冷复核后调用 `studio_commit`，传准备返回的 `operation_id`、`regenerate` 和完整的 changed-state Markdown。`scene_patch` 是 JSON 对象，`state_updates` 的键只能为现有五个状态文件名。状态栏关闭时后台仍应维护状态。
6. 提交中断时原样重试；同一 operation_id 改变正文会被拒绝。成功提交后再向用户展示正文。
   字数是软目标；差几十字不返工，收据中的 `prose_warning` 只供后续调整，不是提交失败，不为此重跑或增加变体。只有用户明确要求严格字数或改写时才调整。
7. 重生成用 `regenerate=true`，只能针对最新回合。包从该回合之前的事件投影生成，不包含被替换回复。附加要求是作者指令，不替换原用户角色行为。提交只追加候选，由用户决定是否选中。
8. 已存在 ready 事务时不得偷偷丢弃或改写。相同直接 prepare 可用原 expected_version 重试；响应丢失或恢复任务时用 `studio_operation(project, session)` 读取原操作和仍有效的上下文。若用户明确要求放弃或重新准备，先调用 `studio_operation(action="archive", operation_id=原ID, expected_version=当前版本, reason=原因)`，草稿和事务完整保存在同会话 `.runtime/archived/<ID>`；再以原输入及需保留的本轮覆盖重新 prepare。CLI 使用 `workbench --operation operation --payload-file <JSON>`，参数相同。
9. `budget_blocked` 或 `needs_compaction` 且没有草稿时可调整模式/持续配置，再重试同一请求；仅压缩记忆或配置变化允许这种恢复，剧情发生变化仍需显式归档。非 ready 返回分节预算诊断及下一步，不得起草。

## CLI 兼容

会话创作要求使用 `studio_requirements`／`workbench --operation requirements`，见技能的 `references/requirements-recovery.md`。保存只影响下一次准备，ready 回复和恢复小票继续使用原快照。正文提交可返回非阻塞 `requirements_check`，不据此自动重写已提交内容。

工作台支持复制恢复指令、保留草稿后重新开始、完成已提交操作的收尾。恢复查询返回原输入、任务类型、覆盖及本地草稿。以事件记录判定是否已提交；`committed` 且 `needs_finish` 时用 `studio_operation(action="finish")`，不追加正文。归档后迟到的原操作不得改用新操作 ID。

普通 `turn commit` 现在必须携带 `--operation-id <prepare返回ID>`；成功收据丢失时原样重试，不能读取后来任务的 ID 代替原 ID。

统一入口：`python -B .agents/skills/story-bible-studio/scripts/story_studio.py workbench --operation <操作> --payload-file <JSON 文件>`。

JSON 参数与 MCP 参数一致，操作名为 `projects/project/snapshot/configure/prepare/commit/memory` 等（MCP 前缀和少数描述性工具名称除外）。小票自带可运行的准备命令。

准备之后用 `--operation commit --payload-file <提交文件>`，示例：

```json
{
  "project": "作品名",
  "session": "会话名",
  "operation_id": "prepare 返回的 ID",
  "prose": "复核后的完整正文",
  "status": "",
  "scene_patch": {},
  "state_updates": {},
  "regenerate": false
}
```

`variant prepare --project <作品目录> --session <ID> --instructions <重生成要求>` 也可准备最新回合变体；提交仍需携带返回的操作 ID，不能用普通 `turn commit` 追加一轮。

面板中切换会话不会切换平台的聊天上下文。平台若保留其他会话历史，应创建干净任务并用指定会话上下文包恢复。
