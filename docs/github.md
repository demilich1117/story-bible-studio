# GitHub 公开版与私人工作区

使用两个独立仓库：`story-bible-studio` 设为 Public，保存程序、技能、模板、文档和中性测试；`story-bible-workspace` 设为 Private，另保存个人作品、会话和已有 Git 历史。

日常创作与开发都在私人工作区进行，其 `origin` 应始终指向私人仓库。作品和会话继续由统一事件引擎管理，Git 只备份文件，不生成或改写事件。

## 私人备份

确认 `gh repo view --json visibility` 显示 `PRIVATE`，再提交和推送工作区：

```powershell
git status --short
git add -A
git commit -m "保存工作区更新"
git push
```

`.gitignore` 排除虚拟环境、缓存、临时回复、发布用 checkout、`output/` 和本机 `opencode.json`。作品的正式正文、事件、状态和版本资料纳入私人仓库；未提交的运行草稿以及被忽略的导出文件不在备份范围内。

## 更新公开版

`public-files.json` 是逐文件审核清单。新增公开文件须先检查内容，再把相对路径加入清单。`tools/export_public.py` 只复制清单文件，另外生成公开版 `.gitignore` 和导出清单；不会复制原仓库 `.git`、提交记录或远程设置。

首次发布，从私人工作区导出到空目录：

```powershell
python -B tools/export_public.py --destination .github-publish/story-bible-studio
git -C .github-publish/story-bible-studio init -b main
git -C .github-publish/story-bible-studio add -A
git -C .github-publish/story-bible-studio commit -m "Initial public release"
```

此目录应单独连接公开仓库。首次上传完成后，后续每次运行同一个导出命令即可更新文件：

```powershell
python -B tools/export_public.py --destination .github-publish/story-bible-studio
git -C .github-publish/story-bible-studio diff --stat
git -C .github-publish/story-bible-studio status --short
git -C .github-publish/story-bible-studio add -A
git -C .github-publish/story-bible-studio commit -m "Update public release"
git -C .github-publish/story-bible-studio push
```

公开目录只用于发布，不在其中创作或直接开发。重复导出会覆盖清单中的文件，并移除上次导出后已从清单删除的文件；遇到陌生文件或符号链接会停止。换电脑时可把公开仓库克隆到上述目录后继续导出。

公开版 `.gitignore` 排除整个 `作品/`、`会话/`、`发布/`、`验收/`、`output/` 及本机运行配置。发布前仍应检查文件内容，清单与忽略规则不能识别被粘贴到源代码或文档里的私人文字。

不要把私人仓库分支合并到公开仓库，也不要从私人目录向公开地址推送；即使当前文件已经删除，历史提交仍可能保留私人内容。两个仓库的代码通过文件导出同步，不共享创作历史。
