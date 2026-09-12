# Story Bible Studio

本地可视工作台：双击 `启动工作台.cmd`，打开美式 Diner 风格的会话阅读与设置面板。可选 MCP 接入 Codex、Antigravity、OpenCode；详细说明见 [工作台指南](workbench/README.md)。

顶部「设定构筑」打开 Story Bible 导图：查看主题、候选与讨论原文，保存灵感，提出修订，复制指令继续头脑风暴。浏览与绘图完全在本地完成；Agent 每轮一次准备、一次提交同步记录，不额外调用模型整理历史。

用于 Story Bible 构筑、连续文学创作和隔离角色扮演的本地 Markdown 工作区。需要 Python 3.11+ 与 PyYAML 6.x。

在本目录打开 Codex，直接描述要构筑的人物、世界或要继续的会话。默认由你选择关键方向，其余细节成组补全；模块进度文件化保存，避免重复问卷。

## 常用命令

```powershell
python -B .agents/skills/story-bible-studio/scripts/story_studio.py project new --name "作品名"
python -B .agents/skills/story-bible-studio/scripts/story_studio.py session new --project "作品/作品名" --id "序章"
python -B .agents/skills/story-bible-studio/scripts/story_studio.py turn prepare --project "作品/作品名" --session "序章" --input-file "本轮输入.md"
python -B .agents/skills/story-bible-studio/scripts/story_studio.py turn commit --project "作品/作品名" --session "序章"
python -B .agents/skills/story-bible-studio/scripts/story_studio.py validate --project "作品/作品名"
```

准备返回 ready 后才起草；正文及补丁放在该会话 .runtime/current/。提交失败可重试，成功清理临时输入并保留收据。

## 模式与长期记忆

质量模式预设保留最近 5 轮原文，预算 80,000 字符；节省模式保留 3 轮，预算 40,000 字符。已有会话保留自定义配置。预设以 `studio_policy.preset()` 和 [运行指南](.agents/skills/story-bible-studio/references/runtime.md) 为准。节省模式不自动缩短正文或更换模型。

```powershell
python -B .agents/skills/story-bible-studio/scripts/story_studio.py mode --project "作品/作品名" --session "序章" --set economy
```

将 economy 换成 quality 恢复质量预设；省略 session 设置新会话默认值。临时覆盖可在 turn prepare 使用 --mode。

Story Bible 与 User Profile 固定版本，动态事实只属于当前会话。正史修订不影响旧会话，显式升级才采用。事件日志是事实源，Markdown 是可重建视图。压缩保留旧记忆与未决后果；转场不单独触发压缩，Luna 只在需要的阶段提供只读候选。

详细流程见 [运行配置与接口](.agents/skills/story-bible-studio/references/runtime.md) 和 [RP 流程](.agents/skills/story-bible-studio/references/roleplay-v3.md)。

## 文风

新项目保存完整文风机制，新会话默认跟随项目。可用 `style list` 选套装，`style show --project <项目> [--session <会话>]` 查看有效规则，`style set --project <项目> --preset 西式奇幻` 设置项目默认。文风修改不升级正史；旧会话需显式 `style set --project <项目> --session <会话> --inherit` 才切换新继承。自然语言微调及本轮覆盖见 [文风指南](.agents/skills/story-bible-studio/references/styles.md)。

## 验证

```powershell
python -B -m unittest discover -s tests -v
```

测试使用全新的“雾港钟楼”中性场景，在临时项目运行，不读写真实作品。覆盖构筑恢复、版本隔离、昵称召回、压缩、变体、预算及故障重试。

## 公开版与私人工作区

公开仓库只包含程序、技能、模板、文档和中性测试，不附带现有作品或会话。首次使用可通过上面的 `project new` 命令创建自己的作品。

私人工作区可以保存自己的作品、会话及版本历史。两个仓库保持独立 Git 历史，公开更新通过文件清单导出。配置和更新方法见 [GitHub 发布指南](docs/github.md)。
