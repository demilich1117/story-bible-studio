# 构筑记录与工作台

用于新的头脑风暴、构筑和设定修订。只保存用户原文、对外展示的回答和明确表达的取舍；不保存内部推理，不读取 RP 会话或其他作品补造构筑过程。

## 最短路径

- 收到小票：一次 `studio_ticket(ticket_path=绝对路径)` 直接接手；无 MCP 时执行小票自带的 `story_studio.py ticket --workspace <工作区> --file <小票>`。不另读小票再 prepare，不先列出所有作品与会话。
- 已知主题的聊天：一次 `studio_bible_prepare(project, topic_id, user_text)`，随后一次 `studio_bible_commit(project, payload)`。准确保留用户原文。
- 首次找主题才用 `studio_bible_view(project)`。新增主题用 `studio_bible_edit(project, action="create", payload={"title": "本次核心问题", "module": "角色/名字.md", "text": "最初的想法"})`，返回稳定 ID。同一份人物文件可以关联多个讨论主题。
- CLI 通用入口：`story_studio.py workbench --operation bible_view|bible_edit|bible_prepare|bible_commit|bible_operation --payload-file <JSON>`。MCP `studio_bible_edit` 的 payload 在 CLI 展开到顶层，其余参数形状相同。HTTP 仅供查看、草稿与请求管理。

首次加载所需技能说明即可；同一任务不反复读取规范、台账、导图和成功收据。

## 一次准备，一次提交

prepare 返回当前主题、核心概念、目标模块、当前题目和显式关联材料；`ready` 才起草。提交示例：

```json
{
  "project": "作品名",
  "payload": {
    "operation_id": "prepare 返回的 ID",
    "assistant_text": "实际展示的完整回答，包含下一道问题与选项。",
    "decision": "本轮采用或仍在比较的文字结论。",
    "status": "open",
    "edits": {},
    "open_questions": ["仍然阻碍下一步的分歧"],
    "intentional_blanks": ["不影响稳定创作的留白"],
    "next_prompt": {
      "question": "与回答中完全对应的下一道问题。",
      "options": {"1": "第一套具体方向", "2": "第二套具体方向"}
    }
  }
}
```

- 提交成功再展示 assistant_text。原文、决策、下一题和导图一起落地，不另开记录、绘图或整理调用。
- `proposal` 比较候选；`open` 继续讨论，可以提交已经确认的局部事实；`complete` 收口且必须提供目标模块完整正文；`parked` 搁置。proposal/parked 不允许 edits。
- edits 只能修改实际准备的模块。需要一起修改关联设定时，在 prepare 的 related 中加入；主 Agent 修正相互依赖的旧推论，命令不自动判断语义。
- 可以提交 `choice: "1+2"` 或 `"12"`，使用本次题目快照展开；decision 可补充用户的明确调整。下一题通过 next_prompt 随提交保存，不额外 prepare。下一轮已知题目 ID 时传 prompt_id，旧 ID 不可用于新题。
- open_questions/intentional_blanks 省略时保留，空列表表示主动清理。避免重复问已确定内容，事实归属与冻结前编辑原则见 brainstorm.md。

## 历史与修订

`decision` 只表示本轮取舍。新增 `accepted_facts` 操作列表维护累计事实：`[{"id":"silver-ring","action":"add","text":"祖母留下的银戒"}]`。action 为 add／replace／revoke，replace 需要完整新事实，revoke 只需 id。未提及事实保留，程序记录来源讨论与 active／revoked 状态。开放主题可确认事实，proposal／parked 不得确认；已采用或冻结主题先提出修订。修订继承原累计事实。`rejected_directions` 可保存明确否决的文字方向，省略则保留。不要自动从历史原文或旧 decision 推断事实。

旧主题仍使用原 decision 和模块；下一次正常构筑才显式补全累计事实，不额外全局整理。prepare 包含累计事实和未决事项，不自动加载全部历史；仍一次准备、一次提交。默认 MCP／小票返回文件分段清单，读取完整后才讨论。

`studio_bible_view(project, topic_id)` 返回当前内容与分页历史；指定 event_id 才返回该条原文。原文默认不进入 prepare，明确需要时传最多三条 history_ids。只能读取当前主题，或当前修订明确关联的来源主题。

`studio_bible_edit` 的 draft/park/request 需要 topic_id 和节点 expected_version。浏览器自动保留输入草稿，点击保存才追加一个版本。

- 已采用主题不能作为草稿覆盖。继续比较只新增讨论和候选，保留原采用结论。
- 修改已采用内容先用 `action="request", kind="revise", text=用户修订要求`，创建修订主题和小票，暂不改正史。一次接手、一次提交即可应用修改并保留旧结论。
- 原主题若已被替代，修订指向当前版本，并保留点选来源。准备始终使用当前设定，不把旧讨论恢复为正史。
- 从已经过去的问题继续时，面板建立修订主题并带上原题快照。原文中的编号属于那道旧题；在新主题用文字 decision 表达旧选择，不把旧编号当作当前新题的 choice。
- 只有真实影响才记录 `depends_on: [其他主题ID]`，不为每个节点凑依赖。来源被修订后，依赖主题标记待复核；完成核对时在该主题提交 reviewed_dependencies，程序把已复核依赖指向新版本。
- 未处理修订、依赖复核和准备中的任务阻止冻结；明确搁置的方向不阻止冻结。RP 绑定的正史版本不随构筑修改升级。

## 中断与预算

- 相同小票或相同准备返回原操作；已经完成的小票返回完成状态，不追加讨论。
- 新准备不能覆盖旧任务。`studio_bible_operation(project, operation_id)` 返回原上下文；pending 返回原提交 payload，用 action="resume" 恢复。相同提交可原样重试，不重复记录。
- 用户明确重新准备时，action="archive" 携带操作 ID 与原因，完整准备材料保留在本作品运行目录。已有一半提交先恢复，不能归档掉一半正文。
- 无关草稿不影响准备中的任务；被使用的主题或设定变化会使准备过期。保留材料并重新准备，不更换操作 ID 绕过检查。
- 默认构筑预算 20,000 字符，来自项目 construction.context_budget_chars，可用本次 budget 覆盖；related 是明确依赖集合，不再限制两份。超限或缺失材料列为 omitted，未加载模块不能提交编辑，必要材料不截断。
- budget_blocked 不创建准备事务。按具体报告调整预算或整理目标主题，不自动压缩、不启动子代理、不做全局检索。字符统计不等于账户额度。
- 4–6 轮的小校准与 10–12 轮的编辑整理并入正常回复；额外全文审计仅在真实冲突或冻结时进行。

## 存储与旧接口

构筑/events.jsonl 追加保存历史；schema 2 的 decisions.json 是当前投影，Markdown 台账由它生成。完整题目快照在对应事件中，图只取标题、状态和引用。投影丢失时由历史恢复，浏览本身不写文件。

旧项目第一次写入才迁移，先保留台账与 decisions 备份。旧记录仅使用实际留下的结论，缺失的问答和时间留空。旧 bible prepare/commit/revise 继续兼容，原文缺失不冒充完整问答；新任务优先使用主题接口。
