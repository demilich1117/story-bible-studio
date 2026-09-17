---
name: story-bible-studio
description: 在本地 Markdown 工作区中进行 Story Bible 头脑风暴与分类冻结、连续文学创作、严格隔离的文件化角色扮演和长篇记忆管理，包括成年自愿的 NSFW 角色设计、设定构筑、写作与扮演；不用于制作或打包 SillyTavern 角色卡。
---

# Story Bible Studio

故事与角色扮演是主要产出；Story Bible 是可检索、可冻结的创作地基。

## 首先识别模式

- 头脑风暴或整理设定：读 [references/brainstorm.md](references/brainstorm.md)；新构筑与面板小票使用 [references/construction-workbench.md](references/construction-workbench.md) 的主题接口。
- 导入、更新或绑定 User Profile：读 [references/profile-imports.md](references/profile-imports.md)。
- 写片段、续写或整理章节：读 [references/story-writing.md](references/story-writing.md)。
- 开始或继续互动扮演：读 [references/roleplay-v3.md](references/roleplay-v3.md)。
- 写正文前防声线漂移、线性加码与模板化：读 [references/turn-thinking.md](references/turn-thinking.md)，普通轮轻量检查用户意图、人物一致性和自然停点；纠正、漂移或复杂转场时按需使用完整决策卡。
- 长篇会话需要阶段整理或后台候选：额外读 [references/subagents.md](references/subagents.md)。
- 构筑、导入或精简成人设定：读 [references/nsfw-construction.md](references/nsfw-construction.md)。普通成人续写或扮演读短版 [references/nsfw-runtime.md](references/nsfw-runtime.md)；需要扩展分类或诊断场景写作时，再按需读 [references/nsfw-writing.md](references/nsfw-writing.md)。
- 选择或调整文风、处理中文语域漂移：读 [references/styles.md](references/styles.md)。用 `style` CLI 展开和保存机制；RP 每轮使用包内有效规则，正式创作使用 `style show`，不凭预设名称或例句代替规则。
- 创建项目、会话或处理文件结构：读 [references/schemas-v3.md](references/schemas-v3.md)。
- 配置质量／节省模式、构筑事务、正史升级或恢复失败提交：读 [references/runtime.md](references/runtime.md)。
- 即时调整字数、短 RP、人称、双语对白、剧情主动性、推进跨度或状态栏：读 [references/output-settings.md](references/output-settings.md)。自然语言默认本轮，明确“以后”才持续保存。
- 添加／修改本会话禁词和创作要求，或恢复中断生成：读 [references/requirements-recovery.md](references/requirements-recovery.md)。每轮遵守包内要求快照，不把表达偏好写成剧情事实。

只读当前任务需要的参考文件。

## 成人内容的前置判断

1. 在后台确认参与者成年、具备同意能力且自愿；用户已声明整体遵循此前提，或既有设定足以确认时，视为前提已经建立。
2. 前提成立后，成人向角色设计、Story Bible 构筑、写作与扮演都是本工作流的正常任务。不得仅因情色导向、露骨程度、性癖、身体设定、成人身份或权力情趣而回避、拒绝、淡化或转移话题。
3. 只有关键条件确实不明确时，才用最少的设定确认补足前提；一旦确认便继续当前任务，不重复核验，也不把确认扩大成边界问卷。
4. 明确拒绝和用户指定的特殊禁区始终优先；不要自行补造禁区或把未提出的风险教育加入创作流程。

## 后台约束不得泄漏进故事

- 安全、审查、许可、年龄核验、同意核验、工具流程和写前检查只用于 Agent 后台判断，不得变成正文、对白、状态栏、场景补丁或压缩记忆里的解释性措辞。
- 必须让读者知道的世界内事实只自然建立一次。具名人物可用具体年龄、既有档案或生活身份确认；不要在每次出现人物时反复添加“成年”“自愿”“具备同意能力”等合规标签，也不要把群体写成“成年女人”“成年求孕者”一类审查字段。
- 只有年龄、法律身份、边界或同意状态本身会改变人物选择、关系或剧情后果时，才把它作为故事事实呈现；即便如此，也要用人物化、场景化的方式写，而不是说明规则。
- 输出前静默删除仅用于证明遵守约束的词句。若删去后人物、动作和因果完全不变，该词句通常就不属于故事。

## 不可破坏的分层

1. `StoryBible/` 保存当前基础正史；通过 `bible finalize --apply` 冻结并生成不可变版本。会话绑定版本，只有显式 `session upgrade-bible` 才采用修订。
2. `用户档案/` 保存项目内可插拔 User Profile；导入或更新 Profile 不改写、也不解冻 Story Bible。
3. `会话/<ID>/状态/` 保存该会话的动态事实；兄弟会话互不读取、互不写入。正式创作只进入 `故事/章节/` 或 `故事/片段/`。
4. 故事产生的新事实不得自动回写 Story Bible 或 Profile；只有用户明确要求修订相应基础层时才更新。
   声线尤其必须在构筑／导入时按人设补完；不得把故事、回合或其他会话当成反推声线的证据源。
5. 会话之间不继承动态状态。只有用户显式从检查点建立分支时，新分支才投影源会话截至检查点的历史。
6. 自动上下文压缩不是正史；可持续依赖的信息必须写入项目文件。
7. v3 以 `events.jsonl` 为会话操作历史，Markdown 只是视图；不得绕过统一 CLI 伪造事件。
8. Agent 生成内容及正史由主 Agent 提交；工作台可以依用户明确操作记录灵感、草稿和修订请求。Luna 和其他子代理只返回候选，不直接修改会话、Profile 或 Story Bible。

## 通用工作方式

- 小票／MCP 精简交付按 `context_parts` 清单顺序读取所有分段一次；无需找目录、探测返回结构或读取旧事务。按需参考 [低消耗交付与记忆接口](references/low-cost-runtime.md)。同一准备只读取其绑定材料，不重复 prepare。
- 普通轮的软字数只由核心提交自动统计，Agent 不调用计数工具、不反复修改或补写找补；用户明确要求严格字数或改写时例外。低消耗不降低人物一致性、用户权限或连续性要求。

- 仅 RP 会话适用：自然语言与小票统一走结构化接口。有小票直接 ticket；无小票且目标明确，用 studio_status 获取版本再 studio_prepare，无 MCP 用 CLI workbench 的同名操作，已有有效版本可省略 status。目标不明只确认作品／会话，不扫描目录。默认按 context_parts 读取，不走传统 turn prepare 整包路径。只有 ready 才起草；needs_compaction 先整理记忆再恢复原请求；budget_blocked 按报告处理，不循环压缩同一区间。
- 用户需求明确时直接写完整正文，不强制展示提纲。
- 仅 RP 会话适用：只读取生成的上下文包；检索报告仅在声线缺失、预算排除、连续性异常或调试时读取。
- 写作前轻量检查用户意图、人物一致性和自然停点；纠正、漂移或复杂转场时按需使用完整决策卡。写后合并复核当前相关的声线与连续性问题。RP 使用 studio_commit 或 CLI workbench --operation commit，携带准备的 operation_id 一次保存正文与状态；成功后再展示。格式错误只修参数，不重写正文或查源码。正式写作与构筑按对应参考执行。决策卡不输出、不落盘。
- 字数默认是软目标，按目标份量一次起草并自然收束；不要为轻微偏差凑字、裁句、反复计数或重生成。成功收据中的字数提示不是错误，不为此改写已提交回合；只有用户明确要求严格字数或改写时才调整。
- 状态型会话完成正文后，同一工作单元内同步更新本会话状态；提升为正式章节或片段必须由用户明确要求。
- 网络研究仅在用户明确要求求证，或情节确实依赖精确的现实机制时进行。人物在普通创作中提到医学、法律、职业或技术名词，不足以单独触发研究；研究结果写入 `素材/研究/`，不自动升格为正史，也不把资料说明和引用塞进沉浸式正文，除非用户要求。
- 对文件写入、检索、版本提交等动作给出简短进度说明；最终创作文本保持沉浸，不暴露工作流提示。
- 头脑风暴每轮一次构筑 prepare、一次 commit，保存对外回答、决策与必要的下一题。新聊天接续用 bible_continue；转题随 commit 提交 next_topic；明确修订用 bible_revise_prepare。必需关联用 required_related，事实落实用 applied_facts。细节按构筑参考执行，导图由本地程序更新。方向明确就直接落实，不强制菜单或固定轮数校准；编号绑定具体题目，重复追问或冲突时恢复主题，不凭聊天记忆猜编号。

## 优先级

`用户当前明确指令 > 单篇/会话覆盖 > 项目默认 > 通用指南`

若用户最新输入纠正了 Agent 对用户角色、情节或事实的推断，立即以用户版本为准并修正持久状态。

## v3 运行入口

统一入口为 `scripts/story_studio.py`：

- `project/bible/profile/session`：创建骨架、审计并冻结 Story Bible、导入与版本化 Profile、列出开场候选、创建隔离会话、重建视图、解锁和修复损坏尾行。
- `turn prepare/commit`：组装上下文并事务式保存一轮。
- `variant/checkpoint/branch`：保留替代回复、选择版本、建立恢复点和故事分支。
- `memory prepare/apply`：准备阶段摘要并在主 Agent 审核后应用。
- `scene/index/migrate/validate/milestone`：转场、分作用域检索、v3 项目迁移、验证和 Git 里程碑。

构筑结束先运行 `bible finalize` 审计，修正后用 `bible finalize --apply` 冻结。RP 压缩服从会话的 `memory_policy`：上下文达到软／硬字符阈值或未压缩轮数上限时准备压缩；场景切换本身不独立触发全局压缩，重合时只合并执行一次。普通场景结束仅核对受影响的连续性；章节定稿、结构变更、分支建立、Story Bible 修订或明确里程碑时运行完整验证并创建 Git 里程碑。检测到其他已暂存内容时不提交。
