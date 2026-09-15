"""Idempotent memory commits; semantic review remains the main agent's job."""
import hashlib
import json

from studio_core import (StudioError, _append_event_unlocked, atomic_write_json,
                         read_events, reduce_events, session_lock,
                         validate_state_updates)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def apply(session, memory_text, through_turn, state_updates=None, expected_event_id=None,
          operation_id=None, stage_summaries=None):
    from studio_core import rebuild_views
    validate_state_updates(state_updates)
    if not isinstance(memory_text, str) or not memory_text.strip():
        raise StudioError('压缩记忆不能为空')
    if isinstance(through_turn, bool) or not isinstance(through_turn, int):
        raise StudioError('压缩终点必须是整数')
    stages = [] if stage_summaries is None else stage_summaries
    if not isinstance(stages, list):
        raise StudioError('stage_summaries 必须为列表')
    for item in stages:
        if not isinstance(item, dict) or set(item) != {'from_turn', 'through_turn', 'text'}:
            raise StudioError('阶段摘要需要 from_turn、through_turn、text')
        if any(isinstance(item[k], bool) or not isinstance(item[k], int) for k in ('from_turn', 'through_turn')):
            raise StudioError('阶段摘要回合必须为整数')
        if not 1 <= item['from_turn'] <= item['through_turn'] <= through_turn:
            raise StudioError('阶段摘要超出已审核覆盖范围')
        if not isinstance(item['text'], str) or not item['text'].strip():
            raise StudioError('阶段摘要不能为空')
    payload = {'through_turn': through_turn, 'memory_text': memory_text.rstrip(),
               'state_updates': state_updates or {}, 'stage_summaries': stages}
    request_hash = digest(payload)
    with session_lock(session):
        history = read_events(session)
        # Legacy callers carrying the source ID also get safe replay.
        op = operation_id or (digest({'source': expected_event_id, 'through': through_turn}) if expected_event_id else None)
        if op:
            prior = next((e for e in history if e['type'] == 'memory_compacted' and e['payload'].get('operation_id') == op), None)
            if prior:
                if prior['payload'].get('request_hash') != request_hash:
                    raise StudioError('相同记忆操作 ID 的内容发生变化')
                rebuild_views(session)
                return receipt(session, prior)
        state = reduce_events(history)
        if expected_event_id and state['last_event_id'] != expected_event_id:
            raise StudioError('压缩候选已过期，请重新准备')
        if not state['last_compressed_turn'] < through_turn <= state['current_turn']:
            raise StudioError('压缩终点超出有效回合范围')
        candidate_path = session / '.runtime/memory-candidate.json'
        candidate = json.loads(candidate_path.read_text(encoding='utf-8')) if candidate_path.exists() else {}
        if operation_id:
            if (candidate.get('operation_id') != operation_id or candidate.get('through_turn') != through_turn
                    or candidate.get('source_event_id') != state['last_event_id']):
                raise StudioError('记忆操作与候选来源或范围不一致')
            expected_event_id = candidate['source_event_id']
        before = len(state['memory'])
        raw_chars = sum(len(v['user']) + len(v['variants'][state['selected_variants'][k]]['prose'])
                        for k, v in state['turns'].items() if state['last_compressed_turn'] < int(k) <= through_turn)
        event = _append_event_unlocked(session, 'memory_compacted', {
            **payload, 'operation_id': op, 'request_hash': request_hash,
            'source_event_id': state['last_event_id'], 'from_turn': state['last_compressed_turn'] + 1,
            'policy_snapshot': candidate.get('policy_snapshot', {}) if candidate.get('source_event_id') == state['last_event_id'] else {},
            'compression_metrics': {'previous_memory_chars': before, 'source_raw_chars': raw_chars,
                                    'memory_chars': len(memory_text.rstrip()),
                                    'saved_chars': before + raw_chars - len(memory_text.rstrip())}})
        rebuild_views(session)
        return receipt(session, event)


def receipt(session, event):
    p = event['payload']
    result = {'status': 'applied', 'operation_id': p.get('operation_id'), 'event_id': event['event_id'],
              'through_turn': p['through_turn'], 'compression_metrics': p.get('compression_metrics', {}),
              'next': '使用原请求重新准备；不要重复压缩已覆盖区间，不沿用旧草稿。'}
    atomic_write_json(session / '.runtime/last-memory-commit.json', result)
    return result
