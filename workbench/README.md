# Story Bible Diner

美式 Diner 风格的本地辅助工作台。SVG 双层芝士汉堡、薯条、可乐和芝士，奶油餐桌与深夜卡座两套主题。正文阅读区不堆放装饰。

## 启动

需要已有 Python 3.11+ 和 PyYAML。Windows 双击根目录 `启动工作台.cmd`，或运行：

```powershell
python -B workbench/server.py --open
```

默认访问 `http://127.0.0.1:8765`；端口占用时使用空闲端口并在终端显示。网页左下角可关闭后台。服务仅限本机；直接生成使用你已安装并登录的平台 CLI。

## 使用

- 顶部「设定构筑」进入同一后台的 `/bible` 构筑桌。按主题浏览导图或大纲，展开旧方向，点击节点查看当前设定与讨论原文。
- 「新主题」和「保存草稿」记录灵感；「提出修订」保留旧结论并创建修订请求；「继续头脑风暴」按所选生成方式直接发送给本地 Agent，或生成可复制的小票。已采用设定不能作为草稿直接覆盖。
- 导图支持缩放、平移、折叠、搜索，窄屏可用大纲。旧作品只显示已有记录，没有保存的历史问答不会自动补造。
- 小票默认短指令，可切换「完整指令」。构筑正文、决策与下一题在一次提交中保存，普通构筑仍是一次准备、一次提交，不另开模型整理。

- 左侧选择作品、管理会话；新会话显式选择 Profile 与开场，也可空白开始。
- 中央阅读已提交正文，切换版本可预览。仅最新回合可直接采用候选，历史修改请先建立检查点并创建分支。
- 桌面端两侧菜单常驻，内容较长时各自滚动；窄屏通过顶部常驻入口展开会话和设置抽屉。
- 阅读区底部可输入回合编号跳转（较早内容会自动补载），也可一键回到顶部；重新打开和更新时按回合锚点恢复阅读位置，并补回已加载的旧文范围。
- 右侧修改字数、人称、扮演权限、双语和状态栏。默认持续保存到本会话；项目默认只影响新会话。本轮设置随下一张小票交接。
- 字数是近似目标：边界外容许 10% 且至少 50 字的偏差，轻微偏差不提示；明显偏离也不阻止提交，不要求重写本轮。只有明确要求严格字数或改写时，Agent 才调整。
- 输入回应，选择生成方式，发送续玩或重生成；选择「复制小票」时仍可交给 Codex、Antigravity、OpenCode 等 Agent。只有核心确认提交，直接生成任务才显示完成。
- Agent 通过统一入口提交后，网页每两秒检测更新；正在看旧文时显示新内容按钮，不强制跳到底部。
- 右侧“本会话创作要求”可直接添加、编辑、删除禁词和自然语言要求，保存后下次准备回复生效；已经 ready 的回复沿用原要求。支持撤销上次保存，与聊天中的明确持续要求共用存储。
- 有 ready 事务时暂停普通设置修改，创作要求仍可保存。平台生成中断后，可在待完成提示下复制恢复指令，或点击“保留草稿并重新开始”回填原输入、修改要求后再发起。已提交但收尾中断时点击“完成收尾”，不会重复追加正文。
- 归档保留原输入、覆盖和本地草稿，刷新后可“找回原输入”；已有不同输入先备份并让你选择。重生成要求仍按重生成处理。停止按钮仅终止本工作台启动的生成进程；不终止其他平台任务，也不自动移除故事锁。上下文预算或压缩阻塞且没有草稿时，仍允许调整模式/配置并重试原请求。
- 输入区附近显示上张小票的待接手、准备、阻塞、完成或过期状态，也可重新查看指令。提交后仅清空仍与该小票相同的输入，不覆盖新写的草稿。外部新增的会话会同步到左栏。
- 字号、字体、主题、阅读位置和草稿只存浏览器 localStorage。会话草稿按工作区、作品、会话隔离；默认工作区首次升级兼容原有本地草稿。清理浏览器数据会清除这些偏好与草稿，已提交故事不受影响。

## 直接生成：构筑与续玩共用

1. 安装并登录所选平台的本地 CLI。先在终端确认 `codex --version` / `opencode --version` 可运行。Codex 可以用 `codex login status` 检查、`codex login` 登录；OpenCode 的账号在该平台配置。桌面聊天已登录不一定代表 CLI 已登录。
2. 重启工作台。在构筑详情或续玩输入框下，将「生成方式」从「复制小票」改为「Codex · 本地生成」或「OpenCode · 本地生成」。
3. 输入后点击「继续头脑风暴」「提出修订」「发送续玩」或「重生成」。小票自动保存在后台并交给 Agent，无需复制。提交后导图、讨论历史和故事正文按原流程更新。

工作台沿用本机平台的默认模型和账号，不接收 API 密钥。Windows 需要本机 `.exe` / `.com`，不执行 `.cmd` / `.ps1` 包装脚本；命令不在 PATH 时，可在启动工作台的终端设置 `STORY_STUDIO_CODEX` 或 `STORY_STUDIO_OPENCODE` 为该可执行文件的绝对路径。不要把这些本机路径写进公开仓库。安装后需重启工作台以取得新的 PATH。

OpenCode 桌面版与 CLI 分开检测。Windows 还会检查桌面安装目录中的 `resources/opencode-cli.exe` 和桌面版的版本化 CLI 缓存。部分桌面版本使用内嵌后台，未提供独立 CLI，此时显示「仅检测到桌面版」，仍可复制小票到桌面版；不能把 `OpenCode.exe` 图形程序当成 `opencode run` 使用。

Codex 使用 `codex exec --json`，OpenCode 本地生成使用 `opencode run --format json`；「OpenCode · 共享后台」使用下节的 `run --attach` 通道。生成过程由平台 Agent 执行工具和提交，并非直接调用裸模型 API。独立 CLI 不保证自动显示到已经打开的桌面聊天窗口。每次创建干净的平台上下文，按指定小票恢复文件化上下文，不自动复用上一次或其他故事的聊天历史。

Codex 使用 `workspace-write` 沙箱并将需额外审批的操作拒绝；OpenCode 沿用本机权限配置，不开启自动批准。若登录、权限或上下文预算阻塞，保留原小票并显示未完成。可调整环境后点击「重试原任务」，也可查看小票转交交互式 Agent。重试可切换已安装的平台，仍使用原请求；构筑与 RP 都不能绕过原事务改用新的操作 ID。

一个工作区同时只运行一个直接生成任务。支持查看 Agent 回复、停止以及刷新页面后继续查询状态。进程退出码为 0 也不等于故事已提交。关闭网页不终止后台；关闭本地后台会停止它启动的任务。单次生成最多运行 30 分钟；超时按停止处理。停止可能留下待恢复事务，使用原小票或恢复入口继续；调度锁或故事锁的遗留情况需要先检查原进程，程序不自动删锁。

任务状态存入工作区 `.workbench/agent-jobs/`，不进入故事事件或 Git；这里只保存任务元信息和对外回复，不保存工具输出或内部推理。页面偏好与最近任务索引在浏览器本地保存。清空浏览器数据会失去该索引，原小票和文件化事务仍保留。

接口参考：[Codex 非交互模式](https://developers.openai.com/codex/noninteractive)、[OpenCode CLI](https://opencode.ai/docs/cli/)。本机 CLI 版本与登录状态需单独验证；模拟测试通过不代表真实模型已连通。

## OpenCode · 共享后台

此通道保留 CLI 调度，用 `opencode run --attach <服务地址> --session <本次新建ID>` 连接常驻 OpenCode 1.x 服务。桌面端连接同一服务并打开同一工作区后，可以在该服务器中查找工作台创建的 session。会话标题以 `Story Bible · 作品名` 开头；工作台显示服务地址并可复制 session ID。不会自动切换桌面端当前服务器或复用已有聊天。

### 首次使用

1. 确认本机 `opencode run --help` 提供 `--attach`、`--session`、`--password`、`--username`。只安装了不附带 CLI 的桌面版仍需单独安装 CLI。
2. 双击根目录 `启动OpenCode共享后台.cmd`，保持这个服务窗口打开。首次默认使用 `http://127.0.0.1:4096`，生成随机密码并保存到本机 `.workbench/opencode-server.json`；以后启动复用该配置。该目录已被 Git 忽略。端口占用时程序不会停止其他服务。
3. 在 OpenCode 桌面端的服务器选择入口添加这个地址，填写配置文件中的 `username` 和 `password`，并打开本工作区的绝对路径。Windows 工作区连接同机 Windows 服务；本通道不做 WSL 路径映射。
4. 重启工作台后台以加载新代码。在生成方式选择「OpenCode · 共享后台」，点击「检查生成连接」。以后仅修改连接文件时无需重启，重新检查即可。
5. 正常发送构筑或续玩。每次分配新的平台 session，重试仍使用原工作台小票和原事务。生成中请等待工作台确认完成后，再在桌面里续接该 session。

共享服务上的模型、登录、工具和权限取决于服务启动时的环境及工作区配置。启动助手把工作台虚拟环境 Python 加入服务 PATH。连接共享服务不会改变模型质量，也不会自动批准权限请求。

### 已有服务或自定义端口

可以直接连接已经配置好认证的服务，无需再启动一份：

```powershell
python -B workbench/opencode_server.py configure --url http://127.0.0.1:4096 --ask-password
python -B workbench/opencode_server.py check
```

`--ask-password` 使用隐藏输入；自定义用户名加 `--username <用户名>`。不加该选项时密码来自 `OPENCODE_SERVER_PASSWORD`，未设置则为空。`configure` 只保存工作台连接，不修改服务或桌面设置；`check` 校验健康状态、1.x 版本和工作目录，不创建 session 或调用模型。自定义工作区给这些命令添加 `--workspace <绝对路径>`。

也可在启动工作台的环境中设置 `STORY_STUDIO_OPENCODE_SERVER_URL`、`OPENCODE_SERVER_USERNAME` 和 `OPENCODE_SERVER_PASSWORD`，分别覆盖连接文件中的值。只接受带明确端口的回环 HTTP 地址，不接受内嵌用户名/密码、代理路径或远程地址。密码通过子进程环境传递，不进入命令参数、网页 localStorage 或生成任务记录。

### 停止与恢复

停止会先结束本次 CLI，再调用该 session 的取消接口并核验空闲；不会停止整个共享服务或其他 session。CLI 正常退出也会核验服务器任务结束，然后以核心提交结果判定完成。

如果取消时服务断线，显示「未确认」并保留 session ID。所有新的直接生成会被阻止，恢复原服务连接后点击「停止并确认后台任务」；确认后才能重试。迟到的核心提交会被识别为完成，不重复生成。若工作台进程崩溃并留下调度锁，仍须先检查原进程，不能自动删锁或盲目重发。

已验证：本机 OpenCode CLI/服务 1.18.30 的真实 `--attach` 简短生成、session ID 一致性和取消确认；另有真实核心配合模拟服务的提交、重试、断线恢复测试。桌面界面展示尚未自动化实测，需要连接上述同一服务后查看。桌面版本 1.18.29 的内嵌服务不是默认共享地址，不自动提取其临时密码。

## 可选 MCP

MCP 让 Agent 直接调用具名工具，减少命令拼接。它不会提供模型、提升平台权限或绕过平台自身限制。各平台独立启动 stdio 服务，共用文件锁；网页关闭时 MCP 仍能工作。

运行根目录 `安装MCP.ps1`，或手动安装到独立虚拟环境：

```powershell
python -m venv .venv-workbench
.venv-workbench/Scripts/python.exe -m pip install -r workbench/requirements-mcp.txt
```

网页“MCP 接入”提供按本机绝对路径生成的配置片段：

- Codex：合并到 `config.toml` 的 `mcp_servers.story_bible`。
- Antigravity：合并到 `mcp_config.json` 的 `mcpServers`。
- OpenCode：合并到 `opencode.json` 的 `mcp`。

不要用片段覆盖整份配置。重新加载平台 MCP 后确认能发现 `studio_projects` 等工具。项目不会自动修改任何平台全局配置。

官方接口参考：[MCP Python SDK v1](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)、[Codex MCP](https://developers.openai.com/codex/mcp)、[Antigravity MCP](https://antigravity.google/docs/mcp)、[OpenCode MCP](https://opencode.ai/docs/mcp-servers/)。

接入后按 [Agent 指南](agent-guide.md) 执行。主 Agent 仍需读取技能；MCP 不能替代文风、记忆、人物意图的判断。

## 开发与边界

前端是本地 HTML/CSS/JavaScript，无 npm 构建或 CDN。后台使用 Python 标准库，HTTP、MCP 与 CLI 都调用 `studio_workbench.py`，再复用现有核心事件操作。

管理写入在核心锁内核对版本和待提交事务。HTTP 只开放管理操作与固定平台的小票调度；`agent_start` 只接受已保存的小票路径、平台名和显式重试标记，不接受命令或正文提交。`agent_stop` 只接受任务 ID。两者复用来源检查与令牌验证；生成提交工具仍仅由 Agent 经 CLI/MCP 调用。不提供任意文件读写、任意 shell、删除会话、自动修复或远程监听接口。

小票保存在目标会话 `.workbench/requests`，运行候选位于该会话 `.runtime`，均不进入正史。

```powershell
python -B -m unittest discover -s tests -v
```

MCP 协议集成测试在安装可选依赖后执行；未安装时明确跳过，不能据此声称平台已连通。
