# 交给 Agent 的初始化指南

本页供能够读写本地文件、执行命令的 AI 助手使用。用户把 README 中的初始化指令发给你后，请完成下载、依赖安装、启动和验证，最后用通俗语言交接结果。

基础工作台需要 Python 3.11+ 和 `requirements.txt` 中的依赖。基础流程通过 CLI 操作；MCP 是可选连接方式。安装环境时无需创建作品、生成正文或读取已有会话。

## 1. 定位或获取项目

先检查当前工作目录。`README.md`、`AGENTS.md`、`requirements.txt`、`workbench/server.py` 和 `.agents/skills/story-bible-studio/scripts/story_studio.py` 应属于同一个项目根目录。

已有项目时使用它，保留现有文件。尚未下载时，在当前可写目录中创建一个新的 `story-bible-studio` 文件夹；同名目录非空且无法确认用途时，选择新的目录名。

- 已安装 Git，可以克隆 `https://github.com/demilich1117/story-bible-studio.git`。
- 也可以下载 `https://github.com/demilich1117/story-bible-studio/archive/refs/heads/main.zip`，解压后定位包含上述文件的目录。

目标目录尚不存在且 Git 可用时，执行：

```powershell
git clone https://github.com/demilich1117/story-bible-studio.git story-bible-studio
```

确认命令成功后，将后续工具调用的工作目录设为新克隆的 `story-bible-studio`。在持续使用同一终端时，可以运行 `cd story-bible-studio`；每条命令独立运行的 Agent 应显式设置工作目录。使用了其他目录名时，以实际路径为准。

只为下载项目，不要求用户额外安装 Git 或登录 GitHub。后续命令的工作目录统一设为项目根目录。阅读根目录 `AGENTS.md` 和 [工作台说明](../workbench/README.md)；开始构筑或写作前，再按 [项目技能](../.agents/skills/story-bible-studio/SKILL.md) 加载相关流程。

## 2. 找到可用的 Python

检查 `python`、`py` 或 `python3` 的实际版本与可执行文件路径，以 `sys.version_info >= (3, 11)` 为准。若项目已有 `.venv-workbench`，先检查其中的 Python 是否能运行、版本是否满足要求。

如果缺少合适的 Python，按当前系统选择 [Python 官方安装渠道](https://www.python.org/downloads/)，优先使用当前用户范围的安装。安装完成后检查实际解释器路径；当前终端尚未刷新 PATH 时，可以直接使用新解释器的绝对路径。

平台无法安装软件、需要用户点击安装器或系统授权时，明确告诉用户卡在什么步骤、需要怎样完成。取得可用解释器后继续后续操作，不把剩余的命令清单当作已经完成安装。

## 3. 安装到项目专用环境

以下是 Windows PowerShell 示例。第一行的 `python` 可以替换为刚检查过的 `py` 或解释器绝对路径。只有环境不存在时才创建；已有环境先检查并复用。

```powershell
python -m venv .venv-workbench
.\.venv-workbench\Scripts\python.exe -m pip install -r requirements.txt
.\.venv-workbench\Scripts\python.exe -c "import sys, yaml; assert sys.version_info >= (3, 11); print('Python and PyYAML are ready')"
.\.venv-workbench\Scripts\python.exe -B .agents/skills/story-bible-studio/scripts/story_studio.py --help
```

检查每条命令的退出码。只有前一步成功才执行下一步；安装失败时先处理具体错误。已有环境损坏时保留原目录，说明问题并选择可恢复的修复方式。

macOS/Linux 的环境解释器位于 `.venv-workbench/bin/python`，使用检查过的 Python 创建环境，再用该路径执行安装和检查命令。

**之后执行项目命令，始终使用这个虚拟环境里的 Python。** 技能或小票里的 `python -B ...` 是入口示例：只把解释器换为本项目环境的路径，保留其余参数，避免调用未安装依赖的系统 Python。

## 4. 启动并确认可以访问

Windows 可以通过 `启动工作台.cmd` 启动，也可以直接运行：

```powershell
.\.venv-workbench\Scripts\python.exe -B workbench/server.py --open
```

macOS/Linux 使用：

```sh
./.venv-workbench/bin/python -B workbench/server.py --open
```

选择能在交接后继续运行的本地进程方式。Windows 用 `Start-Process` 后台启动辅助服务时，使用 `-WindowStyle Hidden`，并把输出保存在项目的 `.workbench/` 中；向用户交代如何关闭服务。环境只允许短命令、不能保持后台进程时，明确说明，并让用户双击启动脚本完成这一步。

读取启动输出中的 `Story Bible Diner: http://127.0.0.1:端口`。默认端口是 8765，端口占用时程序会选择其他可用端口，因此不要把固定网址当作启动结果。遇到已有服务时，先核对其进程命令、解释器和工作目录是否属于本项目；只有确认是本项目才复用。

使用浏览器或 HTTP 请求确认实际地址的 `/` 和 `/bible` 都能正常返回页面。能够控制浏览器时，再查看页面是否正常显示。基础安装不需要打开任何已有作品或会话，也不需要把已有正文用于测试。

如果没有浏览器控制能力，但 HTTP 已通过，准确说明“页面请求已通过，尚未检查浏览器画面”，并把网址交给用户。实际未启动时，不能声称工作台已经可用。

## 5. MCP 按需接入

基础安装完成即可交接。用户另行要求接入 MCP 时，再按 [工作台的 MCP 说明](../workbench/README.md#可选-mcp) 操作：

1. 安装 `workbench/requirements-mcp.txt` 到同一个环境。
2. 从工作台“MCP 接入”取得当前电脑生成的配置片段。
3. 根据用户指定的平台，把该服务条目合并进配置并保留其他服务；需要改变已有同名条目时，先核对它的用途。
4. 重新加载 MCP，并检查工具发现是否成功。发现工具与完成故事生成是不同结果，应分别描述。

无需用户把账号密码或 API 密钥粘贴进项目文档。所需登录应在对应平台正常完成。

## 6. 向用户交接

完成后用简短中文说明：

- 项目的实际位置。
- 使用的 Python 版本，以及依赖是否安装成功。
- 工作台的实际访问地址，已完成哪些启动检查。
- 下次怎样打开、用完怎样关闭：Windows 通常双击 `启动工作台.cmd`；网页左下角有“关闭本地后台”。
- 下一步可以直接发送 README 中的“创建第一个作品”提示；希望扮演自己的角色时，查看 [用户档案制作指南](user-profiles.md)。

遇到尚未完成的系统授权、安装或进程保持步骤，应明确列出，不能用“已准备好”概括未完成的工作。
