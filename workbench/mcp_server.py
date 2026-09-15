"""Optional official-SDK stdio MCP transport. No HTTP server or model needed."""
import argparse
from pathlib import Path
from typing import Literal

from bootstrap import ROOT, SCRIPTS
from studio_workbench import StudioService
from studio_delivery import deliver
from mcp.server.fastmcp import FastMCP


def create_mcp(root=ROOT):
    service = StudioService(Path(root))
    mcp = FastMCP("Story Bible Diner", instructions=(
        "Story Bible Studio 本地工具。按 studio://guide 接手（已加载本地 agent-guide.md 则不重读）。"
        "小票调用 studio_ticket 一次即准备或恢复原任务；按 kind/status 分流，主 Agent 审核并经核心提交。"
        "自然语言 RP 无小票时，明确作品和会话后用 studio_status 取版本，再 studio_prepare；已有有效版本可省略 status。"
        "按 context_parts 读取全部分段一次，遵循 commit_contract 提交成功后展示，不查目录或源码。"))

    @mcp.resource("studio://guide")
    def guide() -> str:
        path = ROOT / "workbench/agent-guide.md"
        return f"本地指南：{path}\n\n" + path.read_text(encoding="utf-8")

    @mcp.resource("studio://skill")
    def skill() -> str:
        """Load the literary skill once, separately from the compact dispatch guide."""
        path = SCRIPTS.parent / "SKILL.md"
        return f"本地技能：{path}\n\n" + path.read_text(encoding="utf-8")

    @mcp.tool()
    def studio_projects() -> list[dict]:
        """List project names only; does not retrieve story text."""
        return service.projects()

    @mcp.tool()
    def studio_project(project: str) -> dict:
        """List sessions/profiles, presets and project defaults. No sibling narratives."""
        return service.project(project)

    @mcp.tool()
    def studio_status(project: str, session: str) -> dict:
        """Read only the named session's version/pending/lock status, without story text. For direct natural-language RP without a ticket: get version here, then studio_prepare once. Reuse an already valid version; never scan directories or use studio_session merely to get a version."""
        return service.status(project, session)

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
                       style_override: dict | None = None, mode: str | None = None, query: str = "",
                       context_delivery: Literal['reference', 'inline'] = 'reference') -> dict:
        """Prepare once from original input OR a workbench request. Default returns operation_id and context_parts: read ALL listed parts once, then draft only when ready. No directory scans, repeated prepare or soft-length counting loops. Regeneration excludes the replaced turn; inline is explicit compatibility."""
        return service.prepare(project, session, user_text, expected_version, request_id, regenerate, override, style_override, mode, query, context_delivery)

    @mcp.tool()
    def studio_requirements(project: str, session: str, action: str = "show", expected_version: int | None = None,
                            items: list[dict] | None = None, prose: str | None = None, operation_id: str | None = None) -> dict:
        """Session author requirements: show/save/undo/check. Save exact id/type/text items using requirements version.
        Check prose against the prepared operation snapshot. Changes affect the next preparation, not an active ready reply."""
        return service.requirements(project, session, action, expected_version, items, prose, operation_id)

    @mcp.tool()
    def studio_operation(project: str, session: str, operation_id: str | None = None, action: str = "show",
                         expected_version: str | None = None, reason: str | None = None, context_delivery: Literal['reference', 'inline'] = 'reference') -> dict:
        """Recover the original context, drafts and input. Archive only on explicit user request, with ID/version/reason.
        Finish only repairs views/cleanup for an already committed operation; never appends prose. Keeps archived recovery input."""
        return service.operation(project, session, operation_id, action, expected_version, reason, context_delivery)

    @mcp.tool()
    def studio_commit(project: str, session: str, operation_id: str, prose: str, status: str = "",
                      scene_patch: dict | None = None, state_updates: dict[str, str] | None = None,
                      motifs: list[str] | None = None, regenerate: bool = False) -> dict:
        """Commit BEFORE displaying the final reply. prose: story ONLY, no status bar. status: separate string of configured label:value lines. scene_patch: scene object (time/location/characters/tension/positions/continuity/open_event; characters is a list). state_updates: full Markdown keyed ONLY by 当前局面.md, 累计剧情摘要.md, 人物与关系状态.md, 身体物品与环境连续性.md, 线索与未决问题.md; never keys like 时间 or 地点. Omit unchanged files. On format error retain prose/operation_id, fix only the named parameter; no source/directory exploration. Core counts soft length; successful warnings never require rewriting. motifs: actual kebab-case IDs only, omit if unused. Never auto-select a variant."""
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
                      through_turn: int | None = None, expected_event_id: str | None = None,
                      state_updates: dict[str, str] | None = None, mode: str | None = None,
                      operation_id: str | None = None, stage_summaries: list[dict] | None = None,
                      context_delivery: Literal['reference', 'inline'] = 'reference') -> dict:
        """Prepare compaction input or apply main-agent-reviewed full memory. Follow memory policy; scene switch alone does not trigger compaction."""
        result = service.memory(project, session, action, text, through_turn, expected_event_id,
                                state_updates, mode, operation_id, stage_summaries)
        if action != 'prepare' or context_delivery == 'inline':
            return result
        _, s = service.locate(project, session)
        return deliver(s, {'status': 'prepared', **{k: result[k] for k in ('operation_id', 'from_turn', 'through_turn', 'source_event_id')},
                           'context': result}, context_delivery)

    @mcp.tool()
    def studio_recall(project: str, session: str, query: str, operation_id: str, mode: str | None = None) -> dict:
        """One targeted local evidence lookup per prepared turn, no model call. Never read sibling sessions. Missing evidence is not permission to invent."""
        return service.recall(project, session, query, operation_id, mode)

    @mcp.tool()
    def studio_ticket(ticket_path: str, context_delivery: Literal['reference', 'inline'] = 'reference') -> dict:
        """Consume a local ticket directly. Default returns a short receipt: read ALL context_parts in order once, without opening the ticket again or scanning directories. Follow receipt status and operation_id."""
        return service.ticket(ticket_path, context_delivery)

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
                             budget: int | None = None, context_delivery: Literal['reference', 'inline'] = 'reference') -> dict:
        """Prepare one construction exchange. One context packet; old transcripts only by explicit IDs."""
        p, _ = service.locate(project)
        return deliver(p, service.bible_prepare(project, topic_id, user_text, request_id, prompt_id, related, history_ids, budget), context_delivery)

    @mcp.tool()
    def studio_bible_commit(project: str, payload: dict) -> dict:
        """Commit displayed assistant_text, decision, reviewed edits and next_prompt together. Retry identical payload safely."""
        return service.bible_commit(project, payload)

    @mcp.tool()
    def studio_bible_operation(project: str, action: str = "show", operation_id: str | None = None,
                               reason: str | None = None, context_delivery: Literal['reference', 'inline'] = 'reference') -> dict:
        """Recover original construction context; resume pending commit or explicitly archive prepared work."""
        p, _ = service.locate(project)
        return deliver(p, service.bible_operation(project, action, operation_id, reason), context_delivery)

    return mcp


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=ROOT)
    args = parser.parse_args()
    create_mcp(args.workspace).run(transport="stdio")
