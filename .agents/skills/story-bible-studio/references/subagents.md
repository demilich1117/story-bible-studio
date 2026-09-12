# Luna 后台协作

只在会话配置启用、当前环境允许子代理且任务达到阶段节点时使用。默认模型为 `gpt-5.6-luna`、`medium`，触发点为上下文达到会话压缩阈值、章节整理和复杂召回。场景切换本身不调用；若转场与压缩阈值重合，只提供一次候选。普通回合不调用，以保持响应流畅。

## 可委托任务

- 从已选回合抽取因果、关系变化、知情范围、物品和连续性。
- 检查正文中的时间、地点、姿态、衣物、伤势、性器、道具和体液连续性。
- 为复杂召回给出相关 Story Bible 分块候选及理由。
- 章节整理后检查遗漏、矛盾和未兑现伏笔。
- Story Bible 导入时做分类与去冗余候选：合并重复条款、从具体性癖归纳关系逻辑，但不改写或冻结正史。

## 禁止委托决定

子代理不得决定用户角色意图、采用哪个变体、关键人物死亡、秘密揭示时机、主线分支、Story Bible 修订或性癖升级。不得自行补充同意宣言、隐私条款和禁区，也不得直接写任何项目文件。

## 输入合同

按阶段读取候选文件中的必要材料，不继承整段任务聊天。压缩输入必须包括 previous_memory、current_state、scene、turns、source_event_id；候选仅在来源版本和回合范围一致时复用。主 Agent 对照旧记忆检查未决事实是否保留，源事件改变后丢弃陈旧候选。普通回合不因“可用额度”充足额外调用子代理。

从 `.runtime/memory-candidate.json` 或上下文报告构造任务，明确：作品、会话、回合范围、已选变体、当前场景、相关状态、召回条目、用户原文、待审正文，以及 `advisory_only` 和 `do_not_write_files`。

成人会话可在 `allow_nsfw_context: true` 时提供完成连续性检查所必需的露骨原文。输出只抽取会改变后续的事实，不重复后台成年自愿前提。

## 输出合同

要求返回单个结构化对象，字段为：

```yaml
status: "ok | warnings | blocking"
facts_added: []
state_changes:
  relationships: []
  continuity: []
  items: []
  location_time: []
open_threads: []
contradictions:
  - severity: "warning | blocking"
    claim: ""
    evidence: ""
recall_recommendations: []
proposed_memory: ""
approval_required: []
```

主 Agent 必须对照原文审核。格式错误、超时或越权建议直接丢弃，由主 Agent同步完成；不向用户暴露未经审核的后台结论。多个子代理可以并行只读分析，但所有文件写入必须串行交回主 Agent。
