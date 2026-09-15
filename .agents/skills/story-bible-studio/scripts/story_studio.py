from __future__ import annotations

import argparse
import json
import hashlib
import sys
from pathlib import Path

from new_story_project import create_project
from studio_bible import audit_bible, finalize_bible
from studio_core import (
    StudioError,
    add_variant,
    apply_memory,
    branch_session,
    commit_turn,
    create_checkpoint,
    create_session,
    git_milestone,
    prepare_memory,
    rebuild_views,
    repair_trailing_event,
    select_variant,
    session_path,
    transition_scene,
    unlock_session,
    validate_session,
    read_events,
)
from studio_retrieval import save_index, search_chunks, write_context
from studio_profiles import (
    apply_profile,
    import_profile,
    list_openings,
    list_profiles,
    show_profile,
    set_profile_voice,
    validate_profile,
)
from studio_migration import migrate_project_v3
from studio_construction import prepare_bible, commit_bible, revise_bible
from studio_policy import set_config, set_mode, config_view
from studio_versions import upgrade_bible
from studio_validate_v3 import validate
from studio_styles import list_styles, show_style, set_style, style_dependency
from studio_core import load_yaml


def text_file(path: str) -> str:
    return Path(path).resolve().read_text(encoding="utf-8")


def json_file(path: str | None, default: dict | None = None) -> dict:
    if not path:
        return default or {}
    loaded = json.loads(text_file(path))
    if not isinstance(loaded, dict):
        raise StudioError(f"JSON 文件必须是对象: {path}")
    return loaded


def locate(args: argparse.Namespace) -> tuple[Path, Path]:
    project = Path(args.project).resolve()
    return project, session_path(project, args.session)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Story Bible Studio v3 统一命令行")
    groups = parser.add_subparsers(dest="group", required=True)

    workbench = groups.add_parser("workbench", help="本地工作台/MCP 共用的结构化接口")
    workbench.add_argument("--operation", required=True)
    workbench.add_argument("--payload-file", help="JSON 参数文件；省略时使用空对象")
    workbench.add_argument("--workspace", default=str(Path(__file__).resolve().parents[4]))

    ticket = groups.add_parser("ticket", help="直接接手工作台小票，无需另读参数文件")
    ticket.add_argument("--file", required=True)
    ticket.add_argument('--context-delivery', choices=('reference', 'inline'), default='reference')
    ticket.add_argument("--workspace", default=str(Path(__file__).resolve().parents[4]))

    style = groups.add_parser("style", help="独立文风配置与有效规则")
    style_sub = style.add_subparsers(dest="action", required=True)
    style_sub.add_parser("list")
    for action in ("show", "set"):
        command = style_sub.add_parser(action)
        command.add_argument("--project", required=True)
        command.add_argument("--session")
        if action == "show":
            command.add_argument("--style-file", help="本次/单篇覆盖 YAML 或 JSON")
        else:
            source = command.add_mutually_exclusive_group(required=True)
            source.add_argument("--preset")
            source.add_argument("--profile-file", help="预设或维度覆盖请求 YAML 或 JSON")
            source.add_argument("--inherit", action="store_true")

    project = groups.add_parser("project", help="作品项目")
    project_sub = project.add_subparsers(dest="action", required=True)
    project_new = project_sub.add_parser("new")
    project_new.add_argument("--name", required=True)
    project_new.add_argument("--root", default="作品")
    project_new.add_argument("--mode", choices=("character-hub", "world-hub"), default="character-hub")
    project_new.add_argument("--anchor", action="append", default=[])
    project_new.add_argument("--date-anchor")

    bible = groups.add_parser("bible", help="Story Bible 构筑审计与冻结")
    bible_sub = bible.add_subparsers(dest="action", required=True)
    bible_audit = bible_sub.add_parser("audit")
    bible_audit.add_argument("--project", required=True)
    bible_audit.add_argument("--for-finalize", action="store_true")
    bible_finalize = bible_sub.add_parser("finalize")
    bible_finalize.add_argument("--project", required=True)
    bible_finalize.add_argument("--apply", action="store_true")
    bible_prepare = bible_sub.add_parser("prepare")
    bible_prepare.add_argument("--project", required=True)
    bible_prepare.add_argument("--module", required=True, help="相对 StoryBible 的 Markdown 路径")
    bible_prepare.add_argument("--options", help="编号到文字结论的 JSON 映射")
    bible_prepare.add_argument("--related", action="append", default=[])
    bible_prepare.add_argument("--mode", choices=("quality", "economy"))
    bible_commit = bible_sub.add_parser("commit")
    bible_commit.add_argument("--project", required=True)
    bible_commit.add_argument("--payload", required=True)
    bible_revise = bible_sub.add_parser("revise")
    bible_revise.add_argument("--project", required=True)
    bible_revise.add_argument("--module")

    bible_operation = bible_sub.add_parser("operation")
    bible_operation.add_argument("--project", required=True)
    bible_operation.add_argument("--action", choices=("show", "resume", "archive"), default="show")
    bible_operation.add_argument("--operation-id")
    bible_operation.add_argument("--reason")

    profile = groups.add_parser("profile", help="项目内 User Profile")
    profile_sub = profile.add_subparsers(dest="action", required=True)
    profile_import = profile_sub.add_parser("import")
    profile_import.add_argument("--project", required=True)
    profile_import.add_argument("--payload", required=True)
    profile_import.add_argument("--source")
    profile_apply = profile_sub.add_parser("apply")
    profile_apply.add_argument("--project", required=True)
    profile_apply.add_argument("--id", required=True)
    profile_apply.add_argument("--revision", required=True)
    profile_list = profile_sub.add_parser("list")
    profile_list.add_argument("--project", required=True)
    profile_show = profile_sub.add_parser("show")
    profile_show.add_argument("--project", required=True)
    profile_show.add_argument("--id", required=True)
    profile_show.add_argument("--revision")
    profile_validate = profile_sub.add_parser("validate")
    profile_validate.add_argument("--project", required=True)
    profile_validate.add_argument("--id", required=True)
    profile_voice_set = profile_sub.add_parser("voice-set")
    profile_voice_set.add_argument("--project", required=True)
    profile_voice_set.add_argument("--id", required=True)
    profile_voice_set.add_argument("--file", required=True)
    profile_openings = profile_sub.add_parser("openings")
    profile_openings.add_argument("--project", required=True)
    profile_openings.add_argument("--id", required=True)
    profile_openings.add_argument("--revision")

    session = groups.add_parser("session", help="会话生命周期")
    session_sub = session.add_subparsers(dest="action", required=True)
    session_new = session_sub.add_parser("new")
    add_locator(session_new, include_session=False)
    session_new.add_argument("--id", required=True)
    session_new.add_argument("--player", default="由用户指定")
    session_new.add_argument("--profile")
    session_new.add_argument("--opening")
    session_new.add_argument("--mode", choices=("quality", "economy"))
    session_upgrade = session_sub.add_parser("upgrade-bible")
    add_locator(session_upgrade)
    session_upgrade.add_argument("--apply", action="store_true")
    session_upgrade.add_argument("--resolution")
    mode_set = groups.add_parser("mode", help="显式应用整套质量/节省预设")
    mode_set.add_argument("--project", required=True)
    mode_set.add_argument("--session")
    mode_set.add_argument("--set", required=True, choices=("quality", "economy"))
    config_set = groups.add_parser("config", help="即时修改写作配置；项目级写入 session_defaults")
    config_set.add_argument("--project", required=True)
    config_set.add_argument("--session")
    config_set.add_argument("--writing", action="store_true", help="正式创作默认，不读取 RP 会话")
    config_set.add_argument("--config-file", help="仅查看本次/单篇覆盖 YAML 或 JSON")
    config_actions = config_set.add_mutually_exclusive_group()
    config_actions.add_argument("--show", action="store_true", help="查看有效值、来源和规则（默认）")
    config_actions.add_argument("--reset", help="重置一个模块；short-rp 清除短 RP 微调")
    config_actions.add_argument("--undo", action="store_true", help="撤销最近一次持续修改")
    config_actions.add_argument("--set", dest="pairs", action="append",
                            metavar="key=value", help="支持点路径，如 agency=user-driven、status_bar.enabled=false")
    session_render = session_sub.add_parser("render")
    add_locator(session_render)
    session_unlock = session_sub.add_parser("unlock")
    add_locator(session_unlock)
    session_unlock.add_argument("--force", action="store_true")
    session_repair = session_sub.add_parser("repair")
    add_locator(session_repair)

    turn = groups.add_parser("turn", help="回合写入与上下文")
    turn_sub = turn.add_subparsers(dest="action", required=True)
    turn_prepare = turn_sub.add_parser("prepare")
    add_locator(turn_prepare)
    turn_prepare.add_argument("--input-file", required=True)
    turn_prepare.add_argument("--query", default="")
    turn_prepare.add_argument("--mode", choices=("quality", "economy"))
    turn_prepare.add_argument("--style-file", help="仅本轮文风覆盖 YAML 或 JSON")
    turn_prepare.add_argument("--config-file", help="仅本轮输出配置覆盖 YAML 或 JSON；空映射清除旧覆盖")
    turn_commit = turn_sub.add_parser("commit")
    add_locator(turn_commit)
    turn_commit.add_argument("--input-file")
    turn_commit.add_argument("--operation-id", required=True, help="prepare 返回的操作 ID，不能沿用其他任务")
    turn_commit.add_argument("--response-file")
    turn_commit.add_argument("--status-file")
    turn_commit.add_argument("--scene-patch")
    turn_commit.add_argument("--state-updates")
    turn_commit.add_argument("--motif-used", action="append", default=[])
    turn_commit.add_argument("--verbose", action="store_true", help="输出完整提交事件")

    variant = groups.add_parser("variant", help="回复变体")
    variant_sub = variant.add_subparsers(dest="action", required=True)
    variant_prepare = variant_sub.add_parser("prepare")
    add_locator(variant_prepare)
    variant_prepare.add_argument("--instructions", default="")
    variant_add = variant_sub.add_parser("add")
    add_locator(variant_add)
    variant_add.add_argument("--turn", required=True, type=int)
    variant_add.add_argument("--response-file", required=True)
    variant_add.add_argument("--status-file")
    variant_add.add_argument("--scene-patch")
    variant_add.add_argument("--state-updates")
    variant_add.add_argument("--motif-used", action="append", default=[])
    variant_select = variant_sub.add_parser("select")
    add_locator(variant_select)
    variant_select.add_argument("--turn", required=True, type=int)
    variant_select.add_argument("--variant", required=True)

    checkpoint = groups.add_parser("checkpoint", help="检查点")
    checkpoint_sub = checkpoint.add_subparsers(dest="action", required=True)
    checkpoint_create = checkpoint_sub.add_parser("create")
    add_locator(checkpoint_create)
    checkpoint_create.add_argument("--id", required=True)
    checkpoint_create.add_argument("--through-turn", type=int)

    branch = groups.add_parser("branch", help="会话分支")
    branch_sub = branch.add_subparsers(dest="action", required=True)
    branch_create = branch_sub.add_parser("create")
    add_locator(branch_create)
    branch_create.add_argument("--checkpoint", required=True)
    branch_create.add_argument("--new-session", required=True)

    memory = groups.add_parser("memory", help="阶段压缩")
    memory_sub = memory.add_subparsers(dest="action", required=True)
    memory_prepare = memory_sub.add_parser("prepare")
    add_locator(memory_prepare)
    memory_prepare.add_argument("--verbose", action="store_true", help="输出完整压缩候选")
    memory_prepare.add_argument("--through-turn", type=int)
    memory_prepare.add_argument("--mode", choices=("quality", "economy"))
    memory_apply = memory_sub.add_parser("apply")
    add_locator(memory_apply)
    memory_apply.add_argument("--memory-file", required=True)
    memory_apply.add_argument("--through-turn", required=True, type=int)
    memory_apply.add_argument("--state-updates")
    memory_apply.add_argument('--operation-id')
    memory_apply.add_argument('--stage-summaries', help='阶段摘要 JSON 文件')

    scene = groups.add_parser("scene", help="场景切换")
    scene_sub = scene.add_subparsers(dest="action", required=True)
    scene_transition = scene_sub.add_parser("transition")
    add_locator(scene_transition)
    scene_transition.add_argument("--id", required=True)
    scene_transition.add_argument("--state-file", required=True)

    index = groups.add_parser("index", help="分块索引")
    index_sub = index.add_subparsers(dest="action", required=True)
    index_rebuild = index_sub.add_parser("rebuild")
    index_rebuild.add_argument("--project", required=True)
    index_query = index_sub.add_parser("query")
    index_query.add_argument("--project", required=True)
    index_query.add_argument("--query", required=True)
    index_query.add_argument("--top-k", type=int, default=8)
    index_query.add_argument("--scope", choices=("canon", "story"), default="canon")

    migrate = groups.add_parser("migrate", help="项目架构迁移")
    migrate_sub = migrate.add_subparsers(dest="action", required=True)
    migrate_project = migrate_sub.add_parser("project-v3")
    migrate_project.add_argument("--project", required=True)
    migrate_project.add_argument("--apply", action="store_true")

    validate_group = groups.add_parser("validate", help="结构和会话验证")
    validate_group.add_argument("--project", required=True)

    milestone = groups.add_parser("milestone", help="Git 里程碑")
    milestone.add_argument("--project", required=True)
    milestone.add_argument("--message", required=True)
    return parser


def add_locator(parser: argparse.ArgumentParser, include_session: bool = True) -> None:
    parser.add_argument("--project", required=True)
    if include_session:
        parser.add_argument("--session", required=True)


def run(args: argparse.Namespace) -> object:
    if args.group == "ticket":
        from studio_workbench import StudioService
        return StudioService(Path(args.workspace)).ticket(args.file, args.context_delivery)
    if args.group == "bible" and args.action == "operation":
        from studio_construction import operation_bible
        return operation_bible(Path(args.project).resolve(), args.action, args.operation_id, args.reason)
    if args.group == "workbench":
        from studio_workbench import StudioService
        return StudioService(Path(args.workspace)).dispatch_transport(args.operation, json_file(args.payload_file))
    if args.group == "variant" and args.action == "prepare":
        from studio_workbench import StudioService
        project, session = locate(args)
        service = StudioService(project.parent.parent, project.parent)
        return service.prepare(project.name, session.name, user_text=args.instructions, regenerate=True,
                               expected_version=service.version(project, session))
    if args.group == "style":
        if args.action == "list":
            return list_styles()
        project = Path(args.project).resolve()
        session = session_path(project, args.session) if args.session else None
        if args.action == "show":
            override = load_yaml(Path(args.style_file)) if args.style_file else None
            return show_style(project, session, override)
        request = {"preset": args.preset} if args.preset else load_yaml(Path(args.profile_file)) if args.profile_file else None
        return set_style(project, session, request, args.inherit)
    if args.group == "mode":
        project = Path(args.project).resolve()
        return set_mode(project, args.set, session_path(project, args.session) if args.session else None)
    if args.group == "config":
        project = Path(args.project).resolve()
        selected = session_path(project, args.session) if args.session else None
        if args.config_file and (args.pairs or args.reset or args.undo):
            raise StudioError("--config-file 仅用于查看临时有效配置")
        if not (args.pairs or args.reset or args.undo):
            return config_view(project, selected, args.writing,
                               load_yaml(Path(args.config_file)) if args.config_file else None)
        return set_config(project, args.pairs, selected, writing=args.writing, reset=args.reset, undo=args.undo)
    if args.group == "bible" and args.action == "prepare":
        return prepare_bible(Path(args.project).resolve(), args.module, json_file(args.options), args.mode, args.related)
    if args.group == "bible" and args.action == "commit":
        return commit_bible(Path(args.project).resolve(), json_file(args.payload))
    if args.group == "bible" and args.action == "revise":
        return revise_bible(Path(args.project).resolve(), args.module)
    if args.group == "project" and args.action == "new":
        return str(create_project(Path(args.root).resolve(), args.name, args.mode, args.anchor, args.date_anchor))
    if args.group == "bible" and args.action == "audit":
        return audit_bible(Path(args.project).resolve(), args.for_finalize)
    if args.group == "bible" and args.action == "finalize":
        return finalize_bible(Path(args.project).resolve(), args.apply)
    if args.group == "profile" and args.action == "import":
        return import_profile(Path(args.project).resolve(), Path(args.payload).resolve(), Path(args.source).resolve() if args.source else None)
    if args.group == "profile" and args.action == "apply":
        return apply_profile(Path(args.project).resolve(), args.id, args.revision)
    if args.group == "profile" and args.action == "list":
        return list_profiles(Path(args.project).resolve())
    if args.group == "profile" and args.action == "show":
        return show_profile(Path(args.project).resolve(), args.id, args.revision)
    if args.group == "profile" and args.action == "validate":
        errors = validate_profile(Path(args.project).resolve(), args.id)
        if errors:
            raise StudioError("\n".join(errors))
        return {"status": "ok", "profile_id": args.id}
    if args.group == "profile" and args.action == "voice-set":
        return set_profile_voice(Path(args.project).resolve(), args.id, Path(args.file).resolve())
    if args.group == "profile" and args.action == "openings":
        return list_openings(Path(args.project).resolve(), args.id, args.revision)
    if args.group == "session" and args.action == "new":
        return str(create_session(Path(args.project).resolve(), args.id, args.player, args.profile, args.opening, args.mode))
    if args.group == "index" and args.action == "rebuild":
        return str(save_index(Path(args.project).resolve()))
    if args.group == "index" and args.action == "query":
        return search_chunks(Path(args.project).resolve(), args.query, args.top_k, scope=args.scope)
    if args.group == "migrate" and args.action == "project-v3":
        return migrate_project_v3(Path(args.project).resolve(), apply=args.apply)
    if args.group == "validate":
        errors = validate(Path(args.project).resolve())
        if errors:
            raise StudioError("\n".join(errors))
        return {"status": "ok"}
    if args.group == "milestone":
        return git_milestone(Path(args.project).resolve(), args.message)

    project, session = locate(args)
    if args.group == "session" and args.action == "upgrade-bible":
        return upgrade_bible(project, session, args.apply, json_file(args.resolution))
    if args.group == "session" and args.action == "render":
        return rebuild_views(session)
    if args.group == "session" and args.action == "unlock":
        unlock_session(session, args.force)
        return {"status": "unlocked"}
    if args.group == "session" and args.action == "repair":
        return {"repaired": repair_trailing_event(session)}
    if args.group == "turn" and args.action == "prepare":
        override = load_yaml(Path(args.style_file)) if args.style_file else None
        output_override = load_yaml(Path(args.config_file)) if args.config_file else None
        context_path, report_path = write_context(project, session, text_file(args.input_file), args.query, args.mode, override, output_override)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        return {
            "status": report["status"],
            "operation_id": json.loads((session / ".runtime/current/transaction.json").read_text(encoding="utf-8"))["operation_id"],
            "context": str(context_path),
            "report": str(report_path),
            "actual_chars": report["actual_chars"],
            "isolation": report["isolation"]["mode"],
            "recent_turn_count": len(report["recent_turns"]),
            "voice_chunk_count": len(report["voice_retrieved"]),
            "retrieval_chunk_count": len(report["retrieved"]),
            "compaction": report["compaction"],
            "missing_core_voice": report["missing_core_voice"],
            "unresolved_characters": report["unresolved_characters"],
            "history_changes_to_review": report["history_changes_to_review"],
            "writing_style": {key: report["writing_style"][key] for key in ("source", "value", "fingerprint", "warnings")},
        }
    if args.group == "turn" and args.action == "commit":
        from studio_core import session_lock
        with session_lock(project), session_lock(session):
            return commit_prepared(args, project, session)
    if args.group == "variant" and args.action == "add":
        return {"variant": add_variant(
            session,
            args.turn,
            text_file(args.response_file),
            text_file(args.status_file) if args.status_file else "",
            json_file(args.scene_patch),
            args.motif_used,
            json_file(args.state_updates),
        )}
    if args.group == "variant" and args.action == "select":
        select_variant(session, args.turn, args.variant)
        return {"status": "selected", "turn": args.turn, "variant": args.variant}
    if args.group == "checkpoint" and args.action == "create":
        return str(create_checkpoint(session, args.id, args.through_turn))
    if args.group == "branch" and args.action == "create":
        return str(branch_session(project, args.session, args.checkpoint, args.new_session))
    if args.group == "memory" and args.action == "prepare":
        candidate = prepare_memory(session, args.through_turn, args.mode)
        if args.verbose:
            return candidate
        return {
            "status": "prepared",
            "operation_id": candidate['operation_id'],
            "source_event_id": candidate['source_event_id'],
            "candidate": str(session / ".runtime" / "memory-candidate.json"),
            "from_turn": candidate["from_turn"],
            "through_turn": candidate["through_turn"],
        }
    if args.group == "memory" and args.action == "apply":
        candidate_path = session / '.runtime/memory-candidate.json'
        candidate = json_file(str(candidate_path)) if candidate_path.exists() else {}
        if not args.operation_id and candidate.get("through_turn") != args.through_turn:
            raise StudioError("压缩终点与候选不一致")
        source = candidate.get('source_event_id') if not args.operation_id or candidate.get('operation_id') == args.operation_id else None
        return apply_memory(session, text_file(args.memory_file), args.through_turn, json_file(args.state_updates), source,
                            operation_id=args.operation_id or candidate.get('operation_id'), stage_summaries=json_file(args.stage_summaries))
    if args.group == "scene" and args.action == "transition":
        transition_scene(session, args.id, json_file(args.state_file))
        return {"status": "transitioned", "scene": args.id}
    raise StudioError("未知命令")


def commit_prepared(args, project, session):
    current = session / ".runtime" / "current"
    transaction_path = current / "transaction.json"
    if not transaction_path.exists():
        receipt = session / ".runtime/last-commit.json"
        if receipt.exists() and not args.input_file and not args.response_file:
            saved_receipt = json.loads(receipt.read_text(encoding="utf-8"))
            if saved_receipt.get("operation_id") == args.operation_id:
                return saved_receipt
        raise StudioError("请先 turn prepare，提交必须绑定操作 ID")
    transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
    if transaction["operation_id"] != args.operation_id:
        raise StudioError("提交操作 ID 不匹配；旧操作不能提交到新任务")
    if transaction["status"] != "ready":
        raise StudioError(f"上下文尚不可起草：{transaction['status']}")
    committed = any(e["type"] == "turn_committed" and e["payload"].get("operation_id") == transaction["operation_id"] for e in read_events(session))
    if not committed and transaction["config_hash"] != hashlib.sha256((session / "会话配置.yaml").read_bytes()).hexdigest():
        raise StudioError("会话配置已变化，请重新准备上下文")
    if not committed and "writing_style" in transaction:
        dependency = style_dependency(project, session, transaction.get("style_override"))
        if dependency != transaction["style_dependency"]:
            raise StudioError("有效文风已变化，草稿已保留；请移存草稿后重新 prepare 并复核")
    input_path = Path(args.input_file).resolve() if args.input_file else current / "user.md"
    response_path = Path(args.response_file).resolve() if args.response_file else current / "response.md"
    status_path = Path(args.status_file).resolve() if args.status_file else current / "status.txt"
    scene_path = Path(args.scene_patch).resolve() if args.scene_patch else current / "scene-patch.json"
    if input_path.read_text(encoding="utf-8").rstrip() != (current / "user.md").read_text(encoding="utf-8").rstrip():
        raise StudioError("提交输入必须与 prepare 的用户原文一致")
    event = commit_turn(
        session,
        input_path.read_text(encoding="utf-8"),
        response_path.read_text(encoding="utf-8"),
        status_path.read_text(encoding="utf-8") if status_path.exists() else "",
        json.loads(scene_path.read_text(encoding="utf-8")) if scene_path.exists() else {},
        args.motif_used,
        json_file(args.state_updates or (str(current / "state-updates.json") if (current / "state-updates.json").exists() else None)),
        transaction["operation_id"], transaction["source_event_id"],
        writing_style=transaction.get("writing_style"),
        output_config=transaction.get("output_config"),
        requirements=transaction.get("requirements"),
    )
    receipt = json.loads((session / ".runtime/last-commit.json").read_text(encoding="utf-8"))
    from studio_requirements import check_requirements
    requirement_check = check_requirements(transaction.get("requirements") or {}, event["payload"]["prose"])
    if args.verbose:
        return event
    result = {
        "status": "committed",
        "requirements_check": requirement_check,
        "turn": event["payload"]["turn"],
        "event_id": event["event_id"],
    }
    for key in ("prose_chars", "prose_actual_chars", "prose_warning"):
        if key in receipt:
            result[key] = receipt[key]
    return result


def main() -> None:
    parser = build_parser()
    try:
        result = run(parser.parse_args())
        print(json.dumps(result, ensure_ascii=False, indent=2) if not isinstance(result, str) else result)
    except (StudioError, FileNotFoundError, FileExistsError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
