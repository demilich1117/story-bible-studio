# 会话创作要求与中断恢复

## 自然语言维护

用户说“本次会话新增要求”“以后别用这个词”等明确持续要求时，通过 `studio_requirements` 或统一 CLI `workbench --operation requirements --payload-file <JSON>` 保存。不用把修改要求当作剧情回合；与续写同条输入时先保存，再按原用户输入 prepare。普通抱怨不自动持久化，本轮要求只按本轮指令处理。

参数示例（project/session 使用实际指定会话）：

```json
{"project":"作品","session":"会话","action":"show"}
```

读取后以返回的独立 `version` 保存完整列表；保留未修改条目的 ID，不覆盖并发修改。条目类型 `banned` 为正文含对白按字面避开的禁词／短语；`guidance` 为直接保存的自然语言要求，不把“少用”改成“禁止”。

```json
{"project":"作品","session":"会话","action":"save","expected_version":0,"items":[{"id":"word-01","type":"banned","text":"不容置疑"},{"id":"note-01","type":"guidance","text":"保留她的克制，不把自信不断写成控制欲。"}]}
```

修改保留 ID；删除从列表移除。`action: undo` 配合当前 `expected_version` 撤销最近一次保存。写入走统一核心，不手工改 `创作要求.json`。新会话默认空，显式分支复制分支创建当时的要求，随后独立维护。

## 写作与复核

prepare 的“本会话创作要求”是必需材料，已固定到该操作。用户本轮明确指令优先。文字表现由主 Agent 判断；不把要求、禁词检查或来源写进正文、状态、压缩记忆和人设。

起草后可调用 `studio_requirements(action="check", operation_id=原ID, prose=正文)`，CLI 使用同名操作和 JSON 参数。检查只用原操作的快照，只匹配正文，不检查状态栏或用户输入。命中时局部复核，必要时修改；语义偏差合并到现有冷复核，不根据禁词数量判断文学质量。

保存新要求不影响已经 ready 的回复。恢复同一 ready 操作仍使用旧快照；下一次续写、新重生成以及未 ready 的准备重试使用最新要求。提交收据 `requirements_check` 是非阻塞诊断；成功后不自动重写或替换已提交文本。

## 恢复中断

工作台的恢复小票授权继续原任务；先查询指定 `operation_id`，不得猜当前任务就是原任务。查询返回 `recovery`（原输入、任务类型及覆盖）、`drafts`（已落盘的文本与补丁）和原上下文。

- `ready`：用原上下文复核已有草稿与完整状态补丁，缺少草稿则按原请求起草；以原 ID 提交。
- `needs_compaction`／`budget_blocked`：按报告处理。重试 `studio_prepare` 时使用 `recovery.user_text/override/style_override/mode/query` 和 `regenerate=(kind=="regenerate")`；若原操作有 `request_id`，优先以它重试。不能把重生成要求转成用户角色行动。
- `stale`：原上下文不可继续提交，提示使用工作台“保留草稿并重新开始”，或按用户明确要求归档后重建。
- `committed`：事件已保存，不重复生成；`needs_finish` 为真时使用 `studio_operation(action="finish", operation_id=原ID, expected_version=返回版本)`，只恢复视图和收尾。
- `archived`：停止旧任务，不能借用后来生成的新操作 ID 提交旧正文。
- `damaged` 或锁占用：保留现场并诊断；不能仅因等待较久就删锁。确认写入者停止后使用已有解锁／修复流程。

归档仅在用户明确要求或点击工作台按钮时执行，包含原 ID、当前会话版本和原因。原输入与草稿保存在同会话归档目录，面板可重新找回；面板不会终止外部 Agent。只存在于外部平台、没有落盘的正文无法由工作台恢复。

生成提交优先使用 `studio_commit` 或 `workbench --operation commit`，必须携带 prepare 返回的 `operation_id` 和 `regenerate`。普通 CLI 同样必须 `turn commit --operation-id <原ID>`；旧无 ID 命令不再接受。文件式草稿应保存为原操作专属临时文件，再通过 `--response-file` 等参数提交；不要让旧任务直接覆盖新任务的 current 草稿。
