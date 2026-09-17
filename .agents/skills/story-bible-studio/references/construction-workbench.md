# 构筑记录与工作台

用于新的头脑风暴、构筑和设定修订。只保存用户原文、对外展示的回答和明确表达的取舍；不保存内部推理，不读取 RP 会话或其他作品补造构筑过程。

## 最短路径

- 收到小票：一次 `studio_ticket(ticket_path=绝对路径)` 直接接手；无 MCP 时执行小票自带的 `story_studio.py ticket --workspace <工作区> --file <小票>`。不另读小票再 prepare，不先列出所有作品与会话。
- 已知主题的聊天：一次 `studio_bible_prepare(project, topic_id, user_text)`，随后一次 `studio_bible_commit(project, payload)`。准确保留用户原文。
- 新聊天或压缩后继续指定作品：先 `studio_bible_continue(project)`，返回聊天提交保存的当前主题、题目、短结论和待处理事项；不读取面板选择。`needs_topic` 时仅确认具体主题，`complete` 时决定下一主题或按用户要求修订，`recovery_required` 时先用原操作恢复。这里的 ready 只表示目标可接续，仍需 prepare 后才起草。已知主题与题目时省略此调用；明确浏览导图／历史才用 `studio_bible_view`。
- 新增主题用 `studio_bible_edit(project, action="create", payload={"title": "本次核心问题", "module": "角色/名字.md", "text": "最初的想法"})`，返回稳定 ID。同一份人物文件可以关联多个讨论主题。
- CLI 通用入口：`story_studio.py workbench --operation bible_continue|bible_view|bible_edit|bible_prepare|bible_revise_prepare|bible_commit|bible_operation --payload-file <JSON>`。MCP `studio_bible_edit` 的 payload 在 CLI 展开到顶层，其余参数形状相同。HTTP 仅供查看、草稿与请求管理。

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
    "applied_facts": [],
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
- 下一题属于新主题时，同次提交加入 `next_topic: {"title": "职业", "module": "世界设定.md"}`；程序原子创建主题并把 next_prompt 绑定过去。已有开放且无未回答问题的主题用 `next_topic: {"topic_id": "目标ID", "expected_version": 版本}`。不要把职业问题挂在已收口的外貌主题上。成功收据的 next_topic_id 和 prompt_id 是下一轮目标；未指定 next_topic 时仍属于当前主题。
- `proposal` 比较候选；`open` 继续讨论，可以提交已经确认的局部事实；`complete` 收口且必须提供目标模块完整正文；`parked` 搁置。proposal/parked 不允许 edits。
- edits 只能修改实际准备的模块。需要一起修改关联设定时，在 prepare 的 related 中加入；主 Agent 修正相互依赖的旧推论，命令不自动判断语义。
- 会影响本轮判断或需要同步修改的材料放 `required_related`，缺失返回 materials_blocked，超预算返回 budget_blocked，均不创建准备事务。`related` 仅放可省略参考；omitted 不可作为判断依据。prepare 中 dependency_hints 只提示已登记依赖，不代表其正文已加载；主 Agent 应在准备前依据已知改动范围声明必需材料。
- 可以提交 `choice: "1+2"` 或 `"12"`，使用本次题目快照展开；decision 可补充用户的明确调整。下一题通过 next_prompt 随提交保存，不额外 prepare。下一轮已知题目 ID 时传 prompt_id，旧 ID 不可用于新题。
- open_questions/intentional_blanks 省略时保留，空列表表示主动清理。避免重复问已确定内容，事实归属与冻结前编辑原则见 brainstorm.md。

## 历史与修订

`decision` 只表示本轮取舍。新增 `accepted_facts` 操作列表维护累计事实：`[{"id":"silver-ring","action":"add","text":"祖母留下的银戒"}]`。action 为 add／replace／revoke，replace 需要完整新事实，revoke 只需 id。未提及事实保留，程序记录来源讨论与 active／revoked 状态。开放主题可确认事实，proposal／parked 不得确认；已采用或冻结主题先提出修订。修订继承原累计事实。`rejected_directions` 可保存明确否决的文字方向，省略则保留。不要自动从历史原文或旧 decision 推断事实。

正文是创作所用事实依据，累计事实负责追踪确认与落实。新的自然语言构筑提交 `applied_facts` 启用落实记录：填本轮已审核落实到目标模块的事实 ID，并同时提供该模块完整 edits；只确认、尚未落实时传空列表。启用后新增／替换／撤回事实，以及重写目标模块，会把受影响事实标为待落实；主 Agent 核对后明确列入 applied_facts。撤回也要确认正文已删除对应事实。程序不做语义猜测，pending_fact_ids 未清零不能收口或冻结。旧调用未启用时保持兼容；旧主题在下一次正常构筑中启用，不额外全局迁移。跨主题关联正文修改仍须审核相应主题的事实与依赖。

旧主题仍使用原 decision 和模块；下一次正常构筑才显式补全累计事实，不额外全局整理。prepare 包含累计事实和未决事项，不自动加载全部历史；仍一次准备、一次提交。默认 MCP／小票返回文件分段清单，读取完整后才讨论。

`studio_bible_view(project, topic_id)` 返回当前内容与分页历史；指定 event_id 才返回该条原文。原文默认不进入 prepare，明确需要时传最多三条 history_ids。只能读取当前主题，或当前修订明确关联的来源主题。

`studio_bible_edit` 的 draft/park/request 需要 topic_id 和节点 expected_version。浏览器自动保留输入草稿，点击保存才追加一个版本。

- 已采用主题不能作为草稿覆盖。继续比较只新增讨论和候选，保留原采用结论。
- 修改已采用内容先用 `action="request", kind="revise", text=用户修订要求`，创建修订主题和小票，暂不改正史。一次接手、一次提交即可应用修改并保留旧结论。
- 纯聊天中用户已明确要求修改时，直接 `studio_bible_revise_prepare(project, topic_id, user_text, expected_version, required_related=...)`，不再创建小票或重复征求“是否开始修订”。返回同样的分段上下文，沿用普通 commit；相同来源版本与原文重试恢复原修订，已提交则返回 committed。面板仍使用上述小票流程。
- 原主题若已被替代，修订指向当前版本，并保留点选来源。准备始终使用当前设定，不把旧讨论恢复为正史。
- 从已经过去的问题继续时，面板建立修订主题并带上原题快照。原文中的编号属于那道旧题；在新主题用文字 decision 表达旧选择，不把旧编号当作当前新题的 choice。
- 只有真实影响才记录 `depends_on: [其他主题ID]`，不为每个节点凑依赖。来源被修订后，依赖主题标记待复核；完成核对时在该主题提交 reviewed_dependencies，程序把已复核依赖指向新版本。
- 未处理修订、依赖复核和准备中的任务阻止冻结；明确搁置的方向不阻止冻结。RP 绑定的正史版本不随构筑修改升级。

## 中断与预算

- 相同小票或相同准备返回原操作；已经完成的小票返回完成状态，不追加讨论。
- 新准备不能覆盖旧任务。`studio_bible_operation(project, operation_id)` 返回原上下文；pending 返回原提交 payload，用 action="resume" 恢复。相同提交可原样重试，不重复记录。
- 用户明确重新准备时，action="archive" 携带操作 ID 与原因，完整准备材料保留在本作品运行目录。已有一半提交先恢复，不能归档掉一半正文。
- 无关草稿不影响准备中的任务；被使用的主题或设定变化会使准备过期。保留材料并重新准备，不更换操作 ID 绕过检查。
- 默认构筑预算 20,000 字符，来自项目 construction.context_budget_chars，可用本次 budget 覆盖；required_related 是必需集合，related 是可选参考，均不限制两份。可选材料超限或缺失列为 omitted；必需材料阻塞准备，未加载模块不能提交编辑，必要材料不截断。
- budget_blocked 不创建准备事务。按具体报告调整预算或整理目标主题，不自动压缩、不启动子代理、不做全局检索。字符统计不等于账户额度。
- 轮数只提醒主 Agent 留意漂移，不要求按轮数打断讨论。局部校准并入正常回复；编辑整理在真实冲突、主题收口或冻结时进行。

## 存储与旧接口

构筑/events.jsonl 追加保存历史；schema 2 的 decisions.json 是当前投影，Markdown 台账由它生成。完整题目快照在对应事件中，图只取标题、状态和引用。投影丢失时由历史恢复，浏览本身不写文件。

旧项目第一次写入才迁移，先保留台账与 decisions 备份。旧记录仅使用实际留下的结论，缺失的问答和时间留空。旧 bible prepare/commit/revise 继续兼容，原文缺失不冒充完整问答；新任务优先使用主题接口。
