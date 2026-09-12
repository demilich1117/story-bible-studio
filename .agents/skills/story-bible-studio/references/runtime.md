# 运行配置与新接口

统一入口：`python -B .agents/skills/story-bible-studio/scripts/story_studio.py`。以下省略入口。

## 质量与节省

`mode --project <项目> [--session <会话>] --set quality|economy` 显式应用整套预设。项目级只影响新建会话；已有会话直接指定 session。`turn prepare --mode` 是本次覆盖，`session new --mode` 是开局覆盖；正文长度与主模型不随模式改变。

| 参数 | quality（默认） | economy |
|---|---:|---:|
| context_budget_chars | 80000 | 40000 |
| recent_turns | 5 | 3 |
| retrieval_top_k | 5 | 2 |
| memory_policy.max_uncompressed_turns | 16 | 10 |
| memory_policy.min_uncompressed_turns | 6 | 5 |
| soft_context_ratio / hard_context_ratio | .75 / .90 | .75 / .90 |

优先级：本次覆盖 > 会话自定义 > 项目创建默认。旧配置中的自定义值不被后台覆盖。只去除完全相同的块；报告记录各节字符数、预算排除、缺失声线、未解析人物和耗时。字符统计不等于实际账户额度。

## 回合输出配置

完整新增接口见 [output-settings.md](output-settings.md)：config --show/--reset/--undo、正式创作 --writing、本轮 turn prepare --config-file。新增 interaction_preset/person/bilingual/initiative/span，统一校验、查看和保存。短 RP 与质量／节省 mode 独立。

`config --project <项目> [--session <会话>] --set key=value`（可重复 `--set`）即时开关或修改扮演权限、正文字数与状态栏；不带 `--session` 时写入项目 `session_defaults`，只影响新建会话。支持点路径与 YAML 值。示例：

- `config --project X --session Y --set agency=user-driven`
- `config --project X --session Y --set status_bar.enabled=false`
- `config --project X --session Y --set "status_bar.fields=[时间,地点,人物,着装,状态,未决]"`
- `config --project X --session Y --set prose.min_chars=800 --set prose.max_chars=1500`
- `config --project X --set agency=user-driven`（项目默认）

可配置键包括 agency（兼容旧两档及短 RP）、prose（enabled/min_chars/max_chars）、status_bar（enabled/fields）、interaction_preset、bilingual、initiative、span，以及 pov/person/tense/time_display/nsfw_overlay。修改会话配置会使未提交事务失效；已有就绪事务或草稿时，先通过统一接口显式归档整个准备事务，再重新 prepare、复核。尚未起草的预算或压缩阻塞事务可调整配置后用原请求重新 prepare。语义见 output-settings.md。

变更可见性：每次 `turn commit` 把当时的 agency/prose/status_bar 解析快照存进事件（`output_config`，仅诊断）；下一次 `turn prepare` 的包首 `回合规范` 行会标出与上轮快照的差异（如 `变更：prose 1000-1200 → 800-1200`）。`turn commit` 收据包含 `prose_chars`；只有超出字数宽容区间时才附加非阻塞 `prose_warning`，仅供后续调整，不回退提交、不自动重写本轮。宽容规则见 output-settings.md，用户明确的严格字数要求仍优先。

## 构筑事务

新构筑和可视工作台使用 [construction-workbench.md](construction-workbench.md) 的主题接口，每轮一次准备、一次提交，同时保存问答和下一题。以下模块命令保留兼容；准备不可覆盖，相同请求返回原 ID，显式归档使用 `bible operation --action archive --operation-id <ID> --reason <原因>`。

`bible prepare --project <项目> --module 角色/名字.md [--options options.json] [--related 基础关系.md] [--mode economy]`。模块名是 StoryBible 内的 Markdown 路径；options 是编号到文字方案的 JSON 映射。输出核心概念、模块、显式相关材料与有效台账，在 `.runtime/bible/prepare.json` 保留操作 ID。

`bible commit --project <项目> --payload decision.json`，payload 示例：

```json
{
  "operation_id": "复制本次 prepare 的 ID",
  "choice": "1+2",
  "decision": "可选：用户对组合的明确修订",
  "status": "complete",
  "edits": {"角色/名字.md": "# 名字\n\n完整的新正文。"},
  "open_questions": [],
  "intentional_blanks": ["允许故事中发现的未知项"]
}
```

不用编号时省略 choice，直接给 decision。组合数字如 `12` 仅在映射能够展开时有效。`proposal` 只存候选，不允许 edits；`open` 表示仍在讨论；`complete` 必须提交对应正文并关闭模块。采用替换语义，主 Agent 负责一起修正旧推论及依赖；命令不假装自动理解语义矛盾。

`构筑/events.jsonl` 保存追加式历史，schema 2 的 `构筑/decisions.json` 保存当前主题与模块投影，Markdown 台账由它生成。中断后用完全相同 payload 重试。旧 CLI 完成模块必须 `bible revise --project <项目> --module <模块>` 才重开，主题接口使用明确修订请求；日常不重新全文审计。

`bible finalize` 做完整审计；errors 阻止冻结，warnings 是编辑提醒，由主 Agent 判断并按需记录理由。`--apply` 预检结构、冻结并生成不可变版本。Profile 使用自己的 validate，不混进 Bible 冻结。

## 正史版本

`正史版本/<hash>/` 保存 Bible 与基础写作配置快照，内容修改会被检测。新会话固定开局版本；旧会话第一次 prepare 追加 `bible_bound`，注明升级时可确认的基线，不冒充历史版本。

`session upgrade-bible --project <项目> --session <会话>` 输出版本差异及当前场景、记忆、状态。主 Agent 核对影响后准备 resolution：`{"from":"旧 hash","to":"新 hash","reviewed":true,"conflicts":[],"notes":"审查结论"}`。明确要求升级时，使用 `--apply --resolution <json>`；未解决冲突保留在 conflicts，不能应用。分支按检查点时刻的事件继承版本。

## 独立文风

`style list/show/set` 管理文风，接口与 YAML 请求见 [styles.md](styles.md)。项目 `writing_style` 不参与正史冻结；新会话对象式 inherit 跟随当前项目，旧字符串 inherit 仍绑定旧正史默认。`turn prepare --style-file <YAML/JSON>` 只覆盖本轮；正式写作用 `style show --project ... --style-file ...` 读取同一解析器的规则。

事务保存完整有效规则、指纹与实际依赖。commit 检查持久规则的被使用部分；完整本轮预设不依赖项目文风，被覆盖的维度不作为依赖。外部请求文件和规则库不在提交时重新展开。事件写入后的重试仅恢复原提交，不因后来的配置修改失效。诊断不会注入故事状态。

## 提交与压缩恢复

`turn commit --operation-id <prepare返回ID>` 默认读取 current 的 user.md、response.md、可选 status.txt、scene-patch.json、state-updates.json。状态更新是五份允许的状态文件到完整 Markdown 的映射。提交必须显式携带原操作 ID；操作 ID 去重，源事件或配置改变时拒绝陈旧提交。恢复与归档流程见 requirements-recovery.md。

`memory prepare [--through-turn N] [--mode economy]` 默认排除近期原文窗口。候选包含旧记忆与当前状态，但不能拿当前状态冒充历史。`memory apply --memory-file <md> --through-turn N [--state-updates <json>]` 校验候选源事件。失效后重新准备；没有较早回合可压缩时，按预算诊断显式决定范围，不循环重试。

保持 schema v3 的旧事件可读，增加 `bible_bound` 及可选提交字段；索引为 v6，旧索引自动重建。升级后用 `session render` 重建旧视图，不删除或改写旧事件。测试统一运行 `python -B -m unittest discover -s tests -v`。

准备事务恢复：相同、仍有效的请求返回原操作；已有 ready 事务时，更换输入、本轮覆盖或重新读取已变化的配置前，必须显式归档原事务。CLI 使用 `workbench --operation operation --payload-file <JSON>`：查询参数为 `{"project":"作品名","session":"会话名"}`；归档另带 `action="archive"`、`operation_id`、`expected_version` 和 `reason`。MCP 对应 `studio_operation`。归档完整保留草稿、上下文和事务至同会话 `.runtime/archived/<ID>`，之后显式传入需要保留的本轮覆盖重新准备。blocked 且无草稿的事务允许调整模式/配置或应用审核后的记忆，再用原请求恢复；剧情变化不能按此路径重用。
