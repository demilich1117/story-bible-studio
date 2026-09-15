"""Explicit fact patches; never infer facts from prose or previous discussions."""
from copy import deepcopy
import re
from studio_core import StudioError


def update_facts(existing, patches, event_id):
    if not isinstance(patches, list):
        raise StudioError('accepted_facts 必须是事实操作列表')
    facts = {f['id']: deepcopy(f) for f in existing}
    seen = set()
    for patch in patches:
        if not isinstance(patch, dict) or set(patch) - {'id', 'action', 'text'}:
            raise StudioError('事实操作仅接受 id、action、text')
        key, action = patch.get('id'), patch.get('action')
        if not isinstance(key, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', key) or key in seen:
            raise StudioError('事实 ID 必须是唯一 stable-kebab-case')
        seen.add(key)
        if action not in {'add', 'replace', 'revoke'}:
            raise StudioError('事实 action 必须为 add/replace/revoke')
        if action == 'add' and key in facts:
            raise StudioError('事实已存在；修改请用 replace')
        if action != 'add' and key not in facts:
            raise StudioError('不能修改不存在的事实')
        text = patch.get('text')
        if action != 'revoke' and (not isinstance(text, str) or not text.strip()):
            raise StudioError('事实文本不能为空')
        before = facts.get(key, {})
        facts[key] = {'id': key, 'text': before.get('text', '') if action == 'revoke' else text.strip(),
                      'status': 'revoked' if action == 'revoke' else 'active',
                      'source_event_id': event_id, 'created_event_id': before.get('created_event_id', event_id)}
    return list(facts.values())
