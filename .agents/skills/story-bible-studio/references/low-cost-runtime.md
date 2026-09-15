# 低消耗交付与记忆接口

本地分段、检索、索引和统计不调用模型。普通轮一次准备、一次提交，不新增全局总结或辅助审核。

## 上下文

MCP `studio_prepare`、`studio_ticket`、`studio_operation`、构筑准备／恢复默认 `context_delivery="reference"`。收据中的 `context_parts` 为顺序、路径、字符数、UTF-8 字节数与 sha256 清单，每段至多 12 KiB；完整材料保存在当前会话或构筑作品 `.runtime/delivery/<operation_id>/<内容哈希>/`。相同文件不覆盖，损坏时拒绝交付。恢复草稿通过分段一起交付，不再次在收据内回显。

按清单一次读完，不枚举目录或猜文件。`context_delivery="inline"` 保留旧客户端完整结果；服务内部调用默认仍为 inline。CLI `ticket --context-delivery inline` 可显式使用旧格式；结构化 `workbench --operation prepare|operation|memory|bible_prepare|bible_operation --payload-file` 默认 reference，JSON 中可显式 context_delivery=inline。memory apply 仍返回成功收据。桌面自然语言无小票时沿用目标，会话 status 获取版本后 prepare；已有有效版本可省略 status，不通过扫描目录找目标。

软字数由提交自动计数；普通轮不单独计数、补写、重复编辑或重读草稿找补。只有用户明确要求严格字数或改写时例外。

### 提交内容分开

会话收据的 `commit_contract` 给出完整字段分工和合法状态文件名。`prose` 只放故事正文；`status` 单独放显示状态栏字符串；`scene_patch` 用 time/location/characters/tension/positions/continuity/open_event 等当前场景英文字段；`state_updates` 是文件名到完整 Markdown 的映射，不能把时间、地点等显示标签当文件名。未变化的状态文件省略，不需要每轮抄写全份状态。

必须先调用 `studio_commit` 成功，再向用户展示已提交正文及状态栏。格式错误沿用正文和 operation_id，只修正错误指出的参数；不改写正文、不探测目录或源码。非法状态文件名会直接列出合法名字和场景字段提示，并在写入本轮草稿文件之前拒绝。核心不猜测或自动移动叙事中的状态内容，避免误改故事语义。

## 原文证据召回

本轮明确追问旧事或显式 query 时，prepare 自动检索当前会话已选历史。普通“继续”不泛化召回。quality 至多 4 块／6,000 字符，economy 至多 2 块／3,000 字符，仍受总包预算限制。来源标记 turn、through_turn、variant_id、event_id 与 raw／summary。按事件时间解释，较早记录不能推翻当前状态或后续纠正。

明确需要补查时：`studio_recall(project, session, query, operation_id, mode?)`；CLI 使用 `workbench --operation recall --payload-file <JSON>`，参数同名。必须绑定 ready 操作，每轮自动补查最多一次；完全相同查询幂等重试，第二种查询被拒绝。未命中或别名歧义不可猜测。派生索引可重建，变体、分支与重生成按有效事件隔离，不读取其他会话。

## 记忆

`studio_memory(project, session, action="prepare", through_turn?, mode?, context_delivery?)` 默认分段交付。候选含 operation_id、source_event_id、from_turn、through_turn、policy_snapshot。准备中的模式覆盖优先于持久默认；显式 through_turn 沿用原报告。

应用：`studio_memory(project, session, action="apply", text, through_turn, operation_id, expected_event_id?, state_updates?, stage_summaries?)`。text 为完整活动记忆；state_updates 是现有允许状态文件到完整 Markdown 的映射。

stage_summaries 是可选列表，每项 `{from_turn, through_turn, text}`，区间必须落在本次已审核覆盖范围内。来源事件由核心记录，不由 Agent 伪造。当前因果、关系承诺、知情范围及未决事项留在活动记忆；已解决旧事可进入阶段摘要并按需召回。旧事件／Markdown 仍可读，不自动结构化或重压缩。

字符目标仅作编辑提示，不截断事实、不触发额外整理调用。应用收据包含源原文、旧／新活动记忆字符数与 saved_chars，仅表示这一部分的字符收益，不等于账户 token 节省。事件已写入后重试会先修复视图；真正来源变化则拒绝新提交。

## 离线用量

`python -B workbench/analyze_usage.py <OpenCode导出.json> [更多导出.json] [--output <报告.json>]`。

按 Assistant 消息累计用量，忽略重复的 step-finish；区分输入、缓存、输出、推理和记录费用，并与会话汇总核对。输出只含聚合数字、步骤号、工具名和长度，不含原文、内部推理或本机路径。缓存读取是累计处理量的一部分；记录费用不是订阅额度扣除。
