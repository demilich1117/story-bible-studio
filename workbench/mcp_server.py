"""Optional official-SDK stdio MCP transport. No HTTP server or model needed."""
import argparse
from pathlib import Path

from bootstrap import ROOT, SCRIPTS
from studio_workbench import StudioService
from mcp.server.fastmcp import FastMCP


def create_mcp(root=ROOT):
    service = StudioService(Path(root))
    mcp = FastMCP("Story Bible Diner", instructions=(
        "Story Bible Studio 本地工具。首次使用读取 studio://guide 和项目 AGENTS.md。"
        "小票直接用 studio_ticket 接手；kind=bible 时使用构筑主题 prepare/commit，不进入 RP 会话流程。"
        "先指定作品与会话，prepare 返回 ready 才起草；主 Agent 审核并 commit。"
        "不得用兄弟会话补剧情。prepare/commit 是成对事务，不手工伪造事件。"
        "重生成只保存候选。用户面板的请求通过 studio_prepare(request_id=...) 消费。"
        "字数是软目标，成功收据的字数提示不要求重写本轮。"
        "MCP 不代替文学技能；正文生成仍由当前 Agent 负责。"))

    @mcp.resource("studio://guide")
    def guide() -> str:
        return (SCRIPTS.parent / "SKILL.md").read_text(encoding="utf-8") + "\n\n" + (ROOT / "workbench/agent-guide.md").read_text(encoding="utf-8")

    @mcp.tool()
    def studio_projects() -> list[dict]:
        """List project names only; does not retrieve story text."""
        return service.projects()

    @mcp.tool()
    def studio_project(project: str) -> dict:
        """List sessions/profiles, presets and project defaults. No sibling narratives."""
        return service.project(project)

    @mcp.tool()
    def studio_session(project: str, session: str, before: int | None = None, limit: int = 20) -> dict:
        """Inspect the specified session only. Returns a version for guarded edits."""
        return service.snapshot(project, session, before, limit)

    @mcp.tool()
    def studio_openings(project: str, profile: str) -> dict:
        """List opening choices. User must explicitly choose an opening or blank start."""
        return service.openings(project, profile)

    @mcp.tool()
    def studio_configure(project: str, expected_version: str, session: str | None = None,
                         pairs: list[str] | None = None, reset: str | None = None, undo: bool = False,
                         mode: str | None = None, style: str | None = None) -> dict:
        """Persist one requested setting operation; no narrative. Pairs are key=value strings. Missing session changes new-session defaults."""
        return service.configure(project, session, expected_version, pairs, reset, undo, mode, style)

    @mcp.tool()
    def studio_new_session(project: str, session_id: str, expected_version: str,
                           profile: str | None = None, opening: str | None = None, player: str = "由用户指定") -> dict:
        """Create an isolated session with an explicitly chosen profile/opening."""
        return service.new_session(project, session_id, expected_version, profile, opening, player)

    @mcp.tool()
    def studio_prepare(project: str, session: str, user_text: str | None = None, expected_version: str | None = None,
                       request_id: str | None = None, regenerate: bool = False, override: dict | None = None,
                       style_override: dict | None = None, mode: str | None = None, query: str = "") -> dict:
        """Prepare an original input OR a workbench request. Draft only when ready. Returns context and operation_id; regeneration excludes the replaced turn."""
        return service.prepare(project, session, user_text, expected_version, request_id, regenerate, override, style_override, mode, query)

    @mcp.tool()
    def studio_requirements(project: str, session: str, action: str = "show", expected_version: int | None = None,
                            items: list[dict] | None = None, prose: str | None = None, operation_id: str | None = None) -> dict:
        """Session author requirements: show/save/undo/check. Save exact id/type/text items using requirements version.
        Check prose against the prepared operation snapshot. Changes affect the next preparation, not an active ready reply."""
        return service.requirements(project, session, action, expected_version, items, prose, operation_id)

    @mcp.tool()
    def studio_operation(project: str, session: str, operation_id: str | None = None, action: str = "show",
                         expected_version: str | None = None, reason: str | None = None) -> dict:
        """Recover the original context, drafts and input. Archive only on explicit user request, with ID/version/reason.
        Finish only repairs views/cleanup for an already committed operation; never appends prose. Keeps archived recovery input."""
        return service.operation(project, session, operation_id, action, expected_version, reason)

    @mcp.tool()
    def studio_commit(project: str, session: str, operation_id: str, prose: str, status: str = "",
                      scene_patch: dict | None = None, state_updates: dict[str, str] | None = None,
                      motifs: list[str] | None = None, regenerate: bool = False) -> dict:
        """Submit reviewed prose and complete changed-state Markdown. Use prepared operation_id and regenerate flag. Retry identical content safely; never auto-select a variant."""
        return service.commit(project, session, operation_id, prose, status, scene_patch, state_updates, motifs, regenerate)

    @mcp.tool()
    def studio_select_variant(project: str, session: str, turn: int, variant: str, expected_version: str) -> dict:
        """Select latest-turn variant only on user's request; historical changes need a branch."""
        return service.select(project, session, turn, variant, expected_version)

    @mcp.tool()
    def studio_checkpoint(project: str, session: str, checkpoint_id: str, expected_version: str, through_turn: int | None = None) -> str:
        """Create a named checkpoint through a chosen turn without overwriting an existing checkpoint."""
        return service.checkpoint(project, session, checkpoint_id, expected_version, through_turn)

    @mcp.tool()
    def studio_branch(project: str, session: str, checkpoint_id: str, new_session: str, expected_version: str) -> dict:
        """Create a separate session from an explicitly selected checkpoint."""
        return service.branch(project, session, checkpoint_id, new_session, expected_version)

    @mcp.tool()
    def studio_memory(project: str, session: str, action: str = "prepare", text: str | None = None,
                      through_turn: int | None = None, expected_event_id: str | None = None) -> dict:
        """Prepare compaction input or apply main-agent-reviewed full memory. Follow memory policy; scene switch alone does not trigger compaction."""
        return service.memory(project, session, action, text, through_turn, expected_event_id)

    @mcp.tool()
    def studio_ticket(ticket_path: str) -> dict:
        """Directly consume a local workbench ticket. Returns prepared context or recovery status; no extra file read."""
        return service.ticket(ticket_path)

    @mcp.tool()
    def studio_bible_view(project: str, topic_id: str | None = None, event_id: str | None = None,
                          cursor: int = 0, limit: int = 20) -> dict:
        """Inspect one project's construction map, or explicitly requested topic/history. No RP context."""
        return service.bible_view(project, topic_id, event_id, cursor, limit)

    @mcp.tool()
    def studio_bible_edit(project: str, action: str, payload: dict | None = None) -> dict:
        """Save a requested topic/draft or create a revision/continuation ticket. Never edits canon directly."""
        return service.bible_edit(project, action, **(payload or {}))

    @mcp.tool()
    def studio_bible_prepare(project: str, topic_id: str | None = None, user_text: str | None = None,
                             request_id: str | None = None, prompt_id: str | None = None,
                             related: list[str] | None = None, history_ids: list[str] | None = None,
                             budget: int | None = None) -> dict:
        """Prepare one construction exchange. One context packet; old transcripts only by explicit IDs."""
        return service.bible_prepare(project, topic_id, user_text, request_id, prompt_id, related, history_ids, budget)

    @mcp.tool()
    def studio_bible_commit(project: str, payload: dict) -> dict:
        """Commit displayed assistant_text, decision, reviewed edits and next_prompt together. Retry identical payload safely."""
        return service.bible_commit(project, payload)

    @mcp.tool()
    def studio_bible_operation(project: str, action: str = "show", operation_id: str | None = None,
                               reason: str | None = None) -> dict:
        """Recover original construction context; resume pending commit or explicitly archive prepared work."""
        return service.bible_operation(project, action, operation_id, reason)

    return mcp


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=ROOT)
    args = parser.parse_args()
    create_mcp(args.workspace).run(transport="stdio")
