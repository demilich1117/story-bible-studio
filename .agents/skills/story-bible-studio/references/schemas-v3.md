# v3 文件结构与字段

模块化输出新增 interaction_preset、person、initiative、span、bilingual；短 RP 微调保存在 short_rp_overrides，正式创作默认保存在项目 writing_defaults。CLI 的 _output_undo 仅保存最近一次配置修改前的受管字段，不属于剧情状态。本轮 config_override 固定到事务，output_config 保存有效快照。旧事件继续可读，无须升级 schema。完整语义见 [output-settings.md](output-settings.md)。

## 项目与构筑

项目使用 schema_version: 3，保存 bible_status（building/revising/frozen）、story_scope、文风、日期锚点、会话默认值。构筑原稿保留在构筑/原始StoryBible.md；构筑/events.jsonl 追加保存构筑历史，decisions.json schema 2 保存主题、模块、题目摘要与操作投影，构筑状态.md 是可读台账。首次写入迁移保留原台账及 decisions-v1.backup.json，缺失原文与时间不补造。主题接口见 construction-workbench.md；构筑事件不属于 RP 的会话事件。

StoryBible/ 是当前工作正史；正史版本/<hash>/ 是内容寻址的不可变快照，包含 Bible、基础写作配置及人物别名清单。固定人物 ID 默认沿用角色档案文件名；标题中的明确昵称和“别名／昵称”字段参与解析，冲突不猜测。

新文风独立保存在项目配置的 `writing_style`：`schema_version: 1`、`preset`、`register` 与展开的六维 `rules`，不进入正史哈希。旧 `default_style` 保留原快照语义。会话 `style: {mode: inherit}` 跟随当前项目；`style: {mode: fixed, profile: <完整文风>}` 固定规则；旧字符串仍按旧语义解析。规则请求和兼容路径见 styles.md。

正式写作只进入故事/章节/、故事/片段/。动态扮演位于会话/<ID>/，不得建立旧输入或故事/时间线目录。

## Profile

用户档案/<ID>/ 包含角色、关系接口、NSFW、语料、开场候选及配置。历史、工作区、待确认、导入记录、来源始终归该 Profile。

声线在构筑／导入时完成核心声线、对象覆盖、场景覆盖；不从故事或回合反推稳定人格。开场用 opening-NN 唯一 ID，推荐只作展示，必须显式选择才复制进会话。绑定后固定 Profile revision，配置文件手工改绑不能覆盖开局事件。

## 会话事实源与视图

events.jsonl 是唯一操作历史，session_state.json 与 Markdown 都可从事件重建。上下文直接使用事件投影，避免读取陈旧视图。

事件类型包括 session_started、turn_committed、variant_added、variant_selected、memory_compacted、scene_transition、checkpoint_created、branch_created、bible_bound。旧 v3 事件继续可读。

- session_started 可保存 bible_revision；旧会话通过 bible_bound 记录首次可确认基线或显式升级。
- turn_committed 保存操作 ID、提交 hash、正文、场景补丁及可选 state_updates；源事件与配置检查由 prepare/commit 合同完成。
- 新 turn_committed 可含 `writing_style` 有效规则与指纹、以及 `output_config`（agency/prose/status_bar 解析快照），仅作提交诊断与下一轮变更对比，不投影到剧情或记忆。旧事件无此字段继续可读。
- 变体按原回合位置投影；修改已压缩回合使相关记忆失效，后续原文不自动改写。
- memory_compacted 保存完整记忆及压缩终点，历史摘要可按明确查询召回。状态补丁使用当前有效状态，不能将其冒充较早回合事实。
- 检查点固定事件位置与回合；分支继承该时刻已选版本与正史绑定。

五份会话状态仍是当前局面、累计剧情摘要、人物与关系状态、身体物品与环境连续性、线索与未决问题。状态更新只提交改变的文件，值为完整 Markdown。

## Runtime 与配置

会话配置的回合输出模块：`agency`（`co-narrative`／`user-driven`，扮演权限）、`prose`（`min_chars`／`max_chars` 正文字数，缺省 1000–1200，`off` 不限）、`status_bar`（`enabled` 与有序 `fields`，正文后状态栏）。项目 `session_defaults` 提供新建会话默认；`config --project <项目> [--session <会话>] --set key=value` 即时修改，支持点路径；语义见 [roleplay-v3.md](roleplay-v3.md)。缺省字段在 prepare 时按内置默认解析。

.runtime/current/ 保存 user.md、response.md、可选 status.txt、scene-patch.json、state-updates.json，以及 CLI 生成的 transaction.json。成功后清空，失败保留；.runtime/last-commit.json 保存小型收据。禁止手工伪造事务 ID 或事件。

prepare 返回 ready、needs_compaction 或 budget_blocked；只有 ready 才起草。完整上下文都参与预算计算，诊断单独存 retrieval-report.json。质量／节省预设、优先级与命令合同见 [runtime.md](runtime.md)。

## 索引与隔离

检索索引 schema v6。只遍历 StoryBible 和正式章节／片段；canon 只返回正史与声线，story 可以查询正式作品。RP 在绑定的正史快照上查询 canon，然后加载固定 Profile；动态查询只访问本会话有效历史。

构筑、Profile 工作区、兄弟会话、研究、导出及其他正史版本不进入当前检索。旧索引自动重建；旧视图用 session render 重建，不执行删除式迁移。
