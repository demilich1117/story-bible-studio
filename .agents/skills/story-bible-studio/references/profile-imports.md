# User Profile 导入与绑定

Story Bible 固定 Char／世界；User Profile 是项目内可插拔的用户角色。换 Profile 不修订冻结正史。

新建项目时用 `project new --mode character-hub --anchor <Char>`，或为群像世界选择 `--mode world-hub` 并按需重复 `--anchor`。

## 领域归属

- Story Bible 拥有 Char、世界、固定 NPC 与公共规则。
- Profile 拥有用户角色自身事实、与正史角色的起始关系、成人偏好、声线设计和开场候选。
- 各会话独立拥有已经发生的关系变化、身体连续性与剧情后果。
- Profile 声称的 Char／世界事实若与正史冲突，必须进入 `conflicts` 和待确认报告，不能自动覆盖。

## 标准化 payload

主 Agent 先读原始附件并生成 UTF-8 JSON。一次性、内容很短的导入可以内嵌 `sections`：

```json
{
  "schema_version": 1,
  "profile_id": "稳定名称",
  "display_name": "显示名",
  "aliases": [],
  "anchors": ["所连接的正史角色"],
  "sections": {
    "角色": "# ...",
    "关系接口": "# ...",
    "NSFW": "# ...",
    "语料": "# ...",
    "开场候选": "# ..."
  },
  "conflicts": []
}
```

持续头脑风暴、预计还会多次更新的 Profile，优先使用 `section_files`，不要把五篇长 Markdown 塞进单行 JSON 字符串：

```json
{
  "schema_version": 1,
  "profile_id": "稳定名称",
  "display_name": "显示名",
  "aliases": [],
  "anchors": ["所连接的正史角色"],
  "section_files": {
    "角色": "草稿/角色.md",
    "关系接口": "草稿/关系接口.md",
    "NSFW": "草稿/NSFW.md",
    "语料": "草稿/语料.md",
    "开场候选": "草稿/开场候选.md"
  },
  "conflicts": []
}
```

路径相对 payload 文件解析；CLI 会读取并固化内容，revision 不依赖草稿路径。这样小改动可以直接审阅对应 Markdown，也更适合 `apply_patch`。

`NSFW` 按 [成人设定构筑指南](nsfw-construction.md) 整理情色身体、亲密取向与具体偏好。该节不得只留标题；没有已确认内容时也要明确写成有意留白，自定义栏目名称不受限制。

“语料”是构筑期设计事实，不是等待正文验证的观察记录。导入材料缺少声线时，主 Agent 必须依据角色人格、关系接口、社会位置与成人设定补成完整声线；校准例句是人设推演示例，不冒充已经发生的正史。不得写“候选／待采样／待会话校准”，也不得从故事正文、回合编号或其他会话反推。CLI 会拒绝缺少“核心声线、对象覆盖、场景覆盖”任一层或仍含上述临时标记的 payload。开场候选只有在创建会话时才复制进该会话。

## CLI 工作流

- 首次导入：`profile import --project <项目> --payload <标准化.json> --source <原始文件>`。
- 同 ID 重导只生成 `用户档案/<ID>/待确认/<revision>/` 和差异报告；用 `profile apply --id <ID> --revision <revision>` 审核后应用。
- 查看与验证：`profile list/show/validate`。
- 构筑期只修订声线：先完成语料文件，再用 `profile voice-set --project <项目> --id <ID> --file <语料.md>`；命令会保留旧 revision 并生成新快照。
- 查看开场候选：`profile openings --project <项目> --id <ID>`。
- 创建会话：`session new --project <项目> --id <会话> --profile <ID> [--opening opening-01]`。
- Profile 更新不改变已有会话；每个会话使用创建时固定的 revision，新会话才采用当前 revision。

## 上下文隔离

普通全文检索排除整个 `用户档案/`。`turn prepare` 只直装会话固定的 revision：

- `character-hub`：加载 Profile 核心及其与锚点 Char 的关系接口。
- `world-hub`：加载 Profile 核心，并只装配当前在场正史角色对应的关系块。
- 未绑定 Profile 的旧项目继续使用 `player_role` 兼容路径。

其他 Profile 不得进入普通召回、声线选择、NSFW 或压缩记忆。
