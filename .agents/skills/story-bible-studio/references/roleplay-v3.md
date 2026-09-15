# v3 文件化角色扮演

## 每轮事务

桌面自然语言、工作台与小票统一采用 [低消耗交付协议](low-cost-runtime.md)。不要求用户每轮提供小票或操作命令；由 Agent 把自然语言原文交给同一个核心。

1. 有小票直接调用 ticket，一次即可。无小票时沿用当前对话已经明确的作品和会话；目标缺失或冲突只询问目标，不枚举目录、不猜工作台当前选择。新建会话按用户明确请求走 new_session，不把“继续”当新建。
2. 无小票且没有有效版本时，用 studio_status(project, session) 获取 version；已有有效版本可直接准备。用 studio_prepare(project, session, user_text=用户原文, expected_version=版本) 一次准备。普通“继续”不补人物名或 query；重生成明确传 regenerate=true，不作为新角色行动。若 status 显示待处理事务，先用 studio_operation 检查原任务，不自行覆盖或归档。
3. 只有 ready 才按 context_parts 清单顺序读取全部分段一次；不读取整个 context-packet.md、不探测目录或旧事务。needs_compaction 按报告的 through_turn 调用 memory prepare，读完候选，主 Agent 审核后 memory apply，再以原输入、覆盖与任务类型恢复准备；这是状态要求的恢复，不是普通轮重复准备。budget_blocked/stale/damaged 按恢复协议处理，不猜测或裁掉必需材料。
4. 依据包内有效规则做轻量检查并一次起草。只有纠正、漂移或复杂冲突才加载完整诊断。prose 只放正文，status 独立放显示状态栏，scene_patch 放场景字段，state_updates 按合法文件名提供改变文件的完整 Markdown。遵循收据 commit_contract，不为软字数计数、补写或反复修改。
5. 用 studio_commit 携带原 operation_id、regenerate 和以上内容一次提交，成功后再展示正文与状态栏。格式错误保留正文和操作 ID，只修提示的参数，不查源码或目录；已提交操作只修复视图，不追加剧情。重生成只存候选，不自动选择。

### 无 MCP 的自然语言入口

使用工作区既有 Python 运行环境与 `.agents/skills/story-bible-studio/scripts/story_studio.py`，命令格式：

```text
python -B .agents/skills/story-bible-studio/scripts/story_studio.py workbench --operation status --payload-file <参数.json>
```

status 参数为 `{"project":"实际作品","session":"实际会话"}`；prepare 参数为 `{"project":"实际作品","session":"实际会话","user_text":"用户原文","expected_version":"status 返回版本"}`。后续把 operation 改为 commit、memory、operation 或 recall，参数与对应 MCP 工具一致。不要照抄占位值。参数文件放工作区临时目录，使用 JSON 序列化写入，不拼接转义不可靠的 shell 字符串。

结构化 CLI 的 prepare／operation／memory 候选默认与 MCP 一样返回短收据和 context_parts；不需要先启动工作台服务器。内部 Python 调用与显式 context_delivery=inline 保留兼容。传统 turn prepare/commit 仅供明确要求使用的旧客户端，不作为桌面自然语言 RP 的默认入口。

提交输入必须与 prepare 一致。状态栏时间服从 resolved_time_display，并与场景补丁一致。提交后再展示正文。

## 回合输出规范

新增模块与覆盖层见 [output-settings.md](output-settings.md)：短 RP、人称、双语对白、剧情主动性、推进跨度均进入有效配置。turn prepare --config-file 保存本轮覆盖，成功提交后恢复持续值；仅设置调整不提交剧情。

基础与新增模块共同控制输出；项目 session_defaults 提供创建默认，config --set 即时修改。包首回合规范与包内有效规则包含当前值，与上轮快照对比展示变化。收据 prose_chars 为阅读字数，prose_actual_chars 为含源语言全文字符数。字数是软目标，轻微偏差不提示；明显偏离时的 prose_warning 也不阻塞或要求重写，成功后照常展示正文。具体宽容规则见 output-settings.md。用户本轮明确要求先转为临时覆盖，使起草与统计使用同一范围。

- **agency（扮演权限）**
  - `co-narrative`（默认）：可以合理代写 user 角色的行动、对白与反应来推进剧情；不得违背已确立人设与用户明确禁区，用户最新输入永远优先，纠正后立即以其为准。
  - `user-driven`：只扮演 user 以外的角色、NPC 与世界；不代写 user 角色的决定性言行、内心或长期决定，每轮结尾留给 user 反应空间。
- **prose（正文字数）**：`min_chars`／`max_chars`，默认 1000–1200，只计正文、不含状态栏。用户本轮明确长度要求优先于配置；`prose: off` 或单边 `null` 表示对应方向不限。
- **status_bar（正文后状态栏）**：`enabled` 开关与 `fields` 有序字段列表（默认 `时间/地点/人物/状态/未决`，可增删，如 `着装`、`位置`、`在场`）。每行一个字段，格式 `字段名：内容`，顺序与 `fields` 一致；只写世界内事实，不写元说明与后台措辞；`时间` 服从 resolved_time_display 并与场景补丁一致。内容随剧情即时更新，值发生变化的字段必须重写，未变化字段保持上轮值。

优先级：用户本轮明确指令（转临时覆盖）>短 RP 微调>短 RP 预设>会话常规配置>内置默认。项目 session_defaults 创建时复制，不动态继承；人称、双语、文风独立于预设切换。

## 隔离与版本

动态材料只来自本会话开场、已选回合、场景、有效摘要、状态及梗台账。共享材料是绑定的 Bible revision、固定 Profile revision 和写作配置。不能读取兄弟会话、Profile 工作区、构筑原稿、章节或研究补剧情。

人物全名和明确昵称从正史档案标题、别名字段解析；冲突不猜测。核心声线缺失时检查报告并修订构筑来源，不从剧情归纳人格。临时 NPC 可以只存在于当前场景与状态。

开场必须显式选择 Profile 中的 opening-NN，或空白开始；推荐候选不自动采用。正史与 Profile 后续修订不影响旧会话。显式升级先检查差异及当前剧情，再通过 CLI 记录采用结果。

## 记忆与纠错

质量模式默认 80,000 字符、最近 5 轮原文；节省模式 40,000 字符、最近 3 轮。正文字数由 `prose` 配置决定（默认 1000–1200，见“回合输出规范”），不随质量／节省模式改变。参数与命令见 [runtime.md](runtime.md)。

压缩候选携带旧记忆、当前状态、新增已选回合及源事件 ID。返回完整记忆，明确保留、更新、已解决的事实与来源，保留未决承诺。不要把当前状态倒写成较早回合事实。默认压缩近期窗口以前的回合，相同来源可复用候选，源事件变化必须重做。

转场不单独触发全局压缩。历史变体按原回合位置投影，覆盖范围内的摘要失效。报告中的 history_changes_to_review 提醒检查后续原文冲突；不自动重写后续剧情。

Luna 只在既定阶段节点提供只读候选；输入使用候选文件的必要字段，不复制整段任务聊天。主 Agent 审核并唯一写入。整理正式章节或片段仍须用户明确要求。
