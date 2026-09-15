"""Local evidence search over the selected history of exactly one session."""
import hashlib
import json
import re

from studio_core import StudioError, atomic_write_json, read_events, reduce_events, selected_turns, session_lock
from studio_retrieval import split_markdown, tokens

VERSION = 1


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def explicit_recall(user_text, query=''):
    return bool(query.strip() or (user_text.strip().casefold() not in {'继续', '接着', 'continue'}
                and re.search(r'之前|以前|记得|上次|当时|曾经|承诺|哪里|哪儿|何处|后来|[?？]|remember|earlier|previous', user_text, re.I)))


def index_session(session, events=None):
    projected = events is not None
    events = read_events(session) if events is None else events
    state = reduce_events(events)
    sources = {}
    for event in events:
        p = event['payload']
        if event['type'] in {'turn_committed', 'variant_added'}:
            sources[(p['turn'], p.get('variant_id', 'v1'))] = event['event_id']
    signature = fingerprint([(e['event_id'], e['type'], e['payload']) for e in events])
    path = session / '.runtime/session-recall-index.json'
    if not path.resolve().is_relative_to(session.resolve()):
        raise StudioError('召回索引超出会话边界')
    old = {}
    if not projected and path.exists():
        try:
            old = json.loads(path.read_text(encoding='utf-8'))
            if old.get('version') != VERSION or old.get('session') != str(session.resolve()):
                old = {}
            if old.get('signature') == signature and old.get('checksum') == fingerprint(old.get('documents')):
                return old
        except (ValueError, TypeError, AttributeError):
            old = {}
    reusable = {d['key']: d for d in old.get('documents', []) if isinstance(d, dict) and 'key' in d} if old.get('checksum') == fingerprint(old.get('documents')) else {}
    documents = []
    for turn in selected_turns(state):
        body = f"User: {turn['user']}\nAssistant: {turn['prose']}\nStatus: {turn.get('status', '')}"
        key = fingerprint([turn['turn'], turn['variant_id'], body])
        doc = reusable.get(key)
        if doc is None:
            doc = {'key': key, 'turn': turn['turn'], 'through_turn': turn['turn'], 'variant_id': turn['variant_id'],
                   'event_id': sources[(turn['turn'], turn['variant_id'])], 'kind': 'raw',
                   'chunks': [c for _, c in split_markdown(body, 1200)]}
        documents.append(doc)
    for memory in state['memory_history']:
        stages = memory.get('stage_summaries', [])
        # Legacy snapshots remain searchable as bounded evidence, never as a whole packet.
        if not stages:
            stages = [{'from_turn': 1, 'through_turn': memory['through_turn'], 'text': memory['memory_text']}]
        for stage in stages:
            documents.append({'key': fingerprint([memory['event_id'], stage]), 'turn': stage['from_turn'],
                'through_turn': stage['through_turn'], 'event_id': memory['event_id'], 'variant_id': None,
                'kind': 'summary', 'chunks': [c for _, c in split_markdown(stage['text'], 1200)]})
    result = {'version': VERSION, 'session': str(session.resolve()), 'signature': signature,
              'documents': documents, 'checksum': fingerprint(documents), 'source_event_id': state['last_event_id']}
    if not projected:
        atomic_write_json(path, result)
    return result


def recall(session, query, mode='quality', *, events=None, before_turn=None, aliases=None):
    if mode not in {'quality', 'economy'} or not isinstance(query, str) or not query.strip():
        raise StudioError('召回需要非空 query 和有效 mode')
    query_terms = tokens(query) - tokens('哪里 哪儿 之前 以前 当时 什么 怎么 记得 继续 接着')
    owners = {}
    for canonical, names in (aliases or {}).items():
        for name in names:
            owners.setdefault(name.casefold(), set()).add(canonical)
    ambiguous = []
    for name, names in owners.items():
        if name in query.casefold():
            if len(names) == 1:
                canonical = next(iter(names))
                query_terms.update(tokens(canonical + ' ' + ' '.join(aliases[canonical])))
            else:
                explicit_names = [canonical for canonical in names if canonical.casefold() in query.casefold()]
                if len(explicit_names) == 1:
                    query_terms.update(tokens(explicit_names[0]))
                else:
                    ambiguous.append(name)
    if ambiguous:
        return {'status': 'ambiguous', 'results': [], 'chars': 0, 'missing_evidence': True,
                'ambiguous_aliases': sorted(ambiguous), 'rule': '别名对应多人；请明确人物，不猜测。'}
    with session_lock(session):
        index = index_session(session, events)
    ranked = []
    for doc in index['documents']:
        if before_turn is not None and doc['kind'] == 'raw' and doc['through_turn'] >= before_turn:
            continue
        for i, content in enumerate(doc['chunks']):
            overlap = query_terms & tokens(content)
            if len(overlap) < 2 and not (len(query_terms) == 1 and overlap):
                continue
            ranked.append((len(overlap), doc['through_turn'], doc['kind'] == 'raw', doc, i))
    ranked.sort(key=lambda r: (r[0], r[1], r[2]), reverse=True)
    cap, budget = (2, 3000) if mode == 'economy' else (4, 6000)
    results, seen, used = [], set(), 0
    for score, _, _, doc, i in ranked:
        if (doc['key'], i) in seen:
            continue
        # Merge the following chunk when it belongs to the same source and fits.
        indices = [i]
        if i + 1 < len(doc['chunks']) and (doc['key'], i + 1) not in seen:
            indices.append(i + 1)
        text = '\n'.join(doc['chunks'][j] for j in indices)
        source = {k: doc[k] for k in ('turn', 'through_turn', 'variant_id', 'event_id', 'kind')}
        prefix = '历史证据（当时事实；后续更新与当前状态优先）：' + json.dumps(source, ensure_ascii=False) + '\n'
        if used + len(prefix) + len(text) > budget:
            indices = [i]
            text = doc['chunks'][i]
        if used + len(prefix) + len(text) > budget or fingerprint(text) in seen:
            continue
        results.append({**source, 'text': prefix + text, 'score': score})
        used += len(prefix) + len(text)
        seen.update((doc['key'], j) for j in indices)
        seen.add(fingerprint(text))
        if len(results) == cap:
            break
    # Source order makes temporal changes visible; rank is retained in score.
    results.sort(key=lambda r: (r['through_turn'], r['event_id']))
    return {'status': 'found' if results else 'not_found', 'source_event_id': index['source_event_id'],
            'results': results, 'chars': used, 'budget_chars': budget,
            'missing_evidence': not bool(results), 'rule': '未命中不编造；历史记录不是当前状态。'}
