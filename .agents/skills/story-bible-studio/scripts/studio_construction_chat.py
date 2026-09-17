"""Natural-language construction routing; no model calls or inferred canon."""
from copy import deepcopy
import uuid

from studio_core import StudioError
from studio_construction_history import module_path
from studio_versions import digest


def next_topic(project, state, current, spec, prompt):
    if spec is None:
        return current
    if not isinstance(spec, dict) or not prompt:
        raise StudioError('next_topic 必须与 next_prompt 一起提交')
    if 'topic_id' in spec:
        if set(spec) != {'topic_id', 'expected_version'}:
            raise StudioError('已有下一主题需要 topic_id 与 expected_version')
        target = state['topics'].get(spec['topic_id'])
        if not target or target['id'] == current['id'] or target['version'] != spec['expected_version']:
            raise StudioError('下一主题不存在、与当前相同或版本已变化')
        if target['status'] not in {'open', 'proposal'} or target.get('prompt_id'):
            raise StudioError('下一主题必须开放且没有未回答问题；已有问题请直接接续该主题')
        target = deepcopy(target)
        target['version'] += 1
        return target
    if set(spec) != {'title', 'module'} or not isinstance(spec['title'], str) or not spec['title'].strip() or len(spec['title']) > 120:
        raise StudioError('新下一主题需要不超过 120 字的 title 和 module')
    module_path(project, spec['module'])
    return {'id': uuid.uuid4().hex, 'title': spec['title'].strip(), 'module': spec['module'],
            'status': 'open', 'decision': '', 'version': 1, 'source': 'conversation',
            'depends_on': [], 'open_questions': [], 'intentional_blanks': []}


def apply_fact_review(topic, payload, edits, operation_id):
    """Opt-in explicit application receipts, never semantic matching against prose.

    Legacy facts remain readable. Once tracking is used, changed facts and module
    rewrites must be reviewed before the topic can close.
    """
    if 'applied_facts' not in payload and not topic.get('fact_application_tracking'):
        return
    topic['fact_application_tracking'] = True
    facts = {f['id']: f for f in topic.get('accepted_facts', [])}
    changed = {p['id'] for p in payload.get('accepted_facts', [])}
    for key, fact in facts.items():
        if key in changed or topic['module'] in edits or 'application' not in fact:
            fact['application'] = 'pending'
    applied = payload.get('applied_facts', [])
    if not isinstance(applied, list) or any(not isinstance(k, str) or k not in facts for k in applied):
        raise StudioError('applied_facts 必须列出当前主题已有的事实 ID')
    if applied and topic['module'] not in edits:
        raise StudioError('确认事实落实必须同时提交目标模块完整正文')
    for key in applied:
        facts[key].update(application='applied', applied_event_id=operation_id,
                          applied_module=topic['module'], applied_hash=digest(edits[topic['module']]))
    pending = [key for key, fact in facts.items() if fact.get('application') != 'applied']
    if payload.get('status', 'complete') == 'complete' and pending:
        raise StudioError('主题仍有未落实或待复核事实：' + '、'.join(pending))
    topic['pending_fact_ids'] = pending
