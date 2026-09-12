# 模块化输出设置

用于 RP、正式创作的自然语言调整和手动配置。文风仍使用 styles.md，不重写人物、正史或已提交剧情。

## 自然语言与有效期

主 Agent 将用户明确要求转换成结构化设置，只改点名模块，不靠程序关键词匹配推断意图。

- “本轮／这次”或未说明有效期：RP 使用 `turn prepare --config-file <文件>`；正式创作使用 `config --writing --config-file <文件>` 查看单篇有效规则。覆盖文件是 YAML/JSON 映射，保存在当前会话临时工作区或明确指定的作者态素材中。
- “以后／一直／这个会话”：使用 `config --session <ID> --set ...`；正式创作使用 `config --writing --set ...`。
- “项目默认”：不带 session/writing 的 config，只影响新建 RP 会话；正式创作旧项目字数仍回退这里，其他模块不借用 RP 设置。
- “切短 RP”：interaction_preset=short-rp；“退出短 RP”：interaction_preset=regular。未说明有效期仍只作用本轮。
- “以后用第二人称”：person=second；“本轮开双语”：临时 `{bilingual: {enabled: true}}`。
- “恢复状态栏”：status_bar.enabled=true；“恢复默认状态栏”：--reset status_bar。开启不等于重置。
- “撤销刚才设置”：--undo 撤销最近一次持续修改；本轮覆盖则重新 prepare 替代覆盖，不回滚剧情。

仅调整设置不调用 turn commit、不生成剧情，简短反馈生效范围。设置与续写同条输入时先配置再 prepare，用户原文不改写。明确要求短文时设置相容的字数区间，不只降低上限却留下更高的旧下限。

## 接口与保存

统一入口 `python -B .agents/skills/story-bible-studio/scripts/story_studio.py`。

```text
config --project X --session Y --show
config --project X --session Y --set interaction_preset=short-rp --set person=second
config --project X --session Y --set prose.min_chars=200 --set prose.max_chars=400
config --project X --session Y --set bilingual.enabled=true
config --project X --session Y --set bilingual.languages.米拉=英语
config --project X --session Y --reset prose
config --project X --session Y --reset short-rp
config --project X --session Y --undo
config --project X --writing --show --config-file 单篇设置.yaml
turn prepare --project X --session Y --input-file user.md --config-file 本轮设置.yaml
```

show 返回 effective、sources、output_config、rules；来源按模块显示，局部覆盖与下层合并。会话原始值在会话配置.yaml；项目新 RP 默认在 session_defaults，正式创作默认在 writing_defaults。项目 RP 默认按创建时复制，不动态传入旧会话。会话重置单模块显式采用当前项目默认；项目重置删除该字段。

正式创作默认不显示状态栏；明确设置 writing_defaults.status_bar 或单篇覆盖才改变这一默认。

手动编辑会话配置时，可参考以下局部结构（保留文件中其他字段）；在短 RP 内微调请改 short_rp_overrides，顶层 prose 等仍是退出预设后恢复的常规值：

```yaml
interaction_preset: short-rp
person: second
prose:
  min_chars: 1000
  max_chars: 1200
short_rp_overrides:
  prose:
    min_chars: 200
    max_chars: 400
  status_bar:
    enabled: true
    fields: [时间, 地点, 着装]
```

本轮覆盖 > 短 RP 自定义 > 短 RP 预设 > 常规持续配置 > 内置默认。人称、双语、文风独立于预设切换。本轮覆盖固定到事务，同一输入重新 prepare 时保留，成功提交后失效；空映射 `{}` 显式清除覆盖，不同输入不继承旧覆盖。已有正文时先移存草稿，再重新准备并复核。

未知字段、非法选项、字符串布尔值和不相容字数区间报错，批量修改不部分写入。手动编辑 YAML 不产生撤销记录；`_output_undo` 保存一次 CLI 配置历史，撤销后消耗，不属于剧情记忆。

## 短 RP

interaction_preset 可选 regular／short-rp，缺省 regular，与质量／节省 mode 无关，旧会话不迁移。

短 RP 默认：150–350 字、agency=short-rp、initiative=balanced、span=beat、状态栏关闭。仅这五个模块进入 short_rp_overrides 层。持续切回 regular 恢复原配置，重入保留微调；--reset short-rp 清除全部微调，--reset prose 等只重置该模块。

agency=short-rp 不新增 user 动作、对白、内心和决定，只承接用户明确写出的行为及外部结果。允许本轮只有对白、观察或等待，不要求每轮局势变化；在需 user 回应前自然停笔，不强制问句，不凑字数。允许明确覆盖具体模块，起草以解析后的有效值为准。旧 co-narrative/user-driven 原义保留，不实施三档迁移。

## 独立模块

- person：first（旁白以我指代 user）／second（你）／third（他、她或姓名）。不改 NPC 对白称呼、不扩大 POV 与 agency；第一人称不授权代写内心。旧自定义字符串继续读取，新设置只能选规范值。正式创作未指定 user 角色则不应用，不自动改整篇叙述者。
- initiative：inherit 沿用／follow 回应眼前事件／balanced 按动机自然推进／active 可引入有因果依据的事件、阻碍或机会。主动不等于替 user 决定。
- span：inherit 沿用／beat 一次行动或回应／scene 多个连续节拍／timeskip 概述重复过程、推进后续时点。不得跨过需 user 决定的节点，不自动触发转场 CLI 或压缩。
- prose：min_chars/max_chars 是写作软目标，单边 null 不限。enabled=false 保留区间；开启恢复。CLI prose=off 也保留区间。旧 off 无历史范围，重新开启默认 1000–1200。计数去空白、含标点、不含状态栏；轻微偏差不提示，明显偏离也不阻止提交或要求重写。
- status_bar：enabled 与 fields 有序列表。关闭只影响显示，后台继续维护；重开从最新场景和状态生成，不复用关闭前的旧值。提交层忽略关闭时遗留 status.txt。

## 字数采用近似目标，不触发自动返工

起草仍瞄准用户设置的原区间，不主动把宽容区间当成新目标；优先完成当前自然节拍，不为满足数字追加剧情、裁断句子或重复生成。无需在提交前后额外逐字计数。

收据始终保留实际计数。每个非空边界向外容许 `max(50 字, 该边界的 10% 向上取整)`：例如 1000–1200 的目标，900–1320 均不产生字数提示；150–350 的目标，100–400 均不提示。配置及上下文中的原目标不改变，不迁移会话。

只有明显超出宽容区间才保留非阻塞 `prose_warning`，文案明确“仅供后续调整，不要据此重写本轮”。成功提交后正常展示正文，不回滚、不建立凑字变体、不自动重跑 prepare/commit，也不向用户插入字数合规解释。用户明确要求严格字数或要求改写时，才按其要求处理；严格意图由 Agent 理解，不靠程序关键词猜测。

## 双语对白

```yaml
bilingual:
  enabled: true
  strategy: world
  languages:
    米拉: 英语
```

默认关闭。开启后仅直接对白采用 `“Wait here.”（在这里等。）`。旁白、动作、内心、状态栏保持中文；中文对白保持单份，不重复翻译。

主 Agent 按每个说话角色自动判断世界内实际用语：用户明确指定（含 languages）>已确立场景交谈语言>角色档案。只使用当前任务允许读取的材料；不按姓名、作品产地或资料书写语言猜测，不为判定语言读取兄弟会话或联网。未知或虚构语言无可靠表达依据则中文。必要事实不在包内时使用已授权的绑定档案检索流程，不猜测。

译文保留原意与声线，不加解释；只是读者呈现，不使听者获得理解能力，不改档案或正史。多语言角色由场景确定实际用语，用户可逐角色临时或持续指定。

字数按中文阅读内容计：旁白＋译文，外语原句不重复占用目标。程序仅识别规范的 `“原句”（译文）`；非规范内容保守计入全文，统计不代表语言已验证。收据 prose_chars 是阅读计数，prose_actual_chars 是含源语言的全文计数。双语开关和语言覆盖进入配置快照和变更比较。

## 验收边界

程序保证解析、保存、到期、撤销、计数与提交一致性；语言识别、翻译、代写边界和文学效果由主 Agent 起草与冷复核负责。规则加载不代表语义验收已经通过。不要把设置来源、诊断或规则写进正文和剧情记忆。
