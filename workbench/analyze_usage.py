"""Offline OpenCode export analysis. Never emits prompts, prose or private paths."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re

FIELDS = ('input', 'cache_read', 'cache_write', 'output', 'reasoning')


def counts(tokens):
    result = {k: tokens.get(k, 0) for k in ('input', 'output', 'reasoning')}
    result.update(cache_read=tokens.get('cache', {}).get('read', 0), cache_write=tokens.get('cache', {}).get('write', 0))
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in result.values()):
        raise ValueError('Token counts must be nonnegative integers')
    return result


def analyze(data):
    totals, tools, lengths = Counter(), Counter(), Counter()
    steps = []
    for message in data['messages']:
        info = message.get('info', {})
        if info.get('role') != 'assistant':
            continue
        usage = counts(info.get('tokens', {}))
        cost = info.get('cost', 0)
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
            raise ValueError('Invalid cost')
        names = []
        for part in message.get('parts', []):
            kind = part.get('type')
            if kind == 'tool':
                name = part.get('tool', '')
                name = name if isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,120}', name) else 'unknown'
                tools[name] += 1
                names.append(name)
                state = part.get('state', {})
                lengths['tool_input_chars'] += len(json.dumps(state.get('input', {}), ensure_ascii=False))
                lengths['tool_output_chars'] += len(str(state.get('output', '')))
                lengths['tool_errors'] += state.get('status') == 'error'
            elif kind in {'text', 'reasoning'}:
                lengths[kind + '_chars'] += len(part.get('text', ''))
        # step-finish duplicates message usage; deliberately ignored.
        if sum(usage.values()) or cost:
            steps.append({'step': len(steps) + 1, **usage, 'total': sum(usage.values()), 'cost': cost, 'tools': names})
            totals.update(usage)
            totals['cost'] += cost
    total_input = totals['input'] + totals['cache_read'] + totals['cache_write']
    summary = data.get('info', {})
    matched = None
    if 'tokens' in summary:
        matched = counts(summary['tokens']) == {k: totals[k] for k in FIELDS}
    return {'schema_version': 1, 'model_steps': len(steps), 'tool_calls': sum(tools.values()),
            'tokens': {k: totals[k] for k in FIELDS}, 'total_tokens': sum(totals[k] for k in FIELDS),
            'input_total': total_input, 'cache_hit_ratio': totals['cache_read'] / total_input if total_input else None,
            'recorded_cost': totals['cost'], 'session_tokens_match': matched,
            'session_cost_match': math.isclose(summary['cost'], totals['cost'], abs_tol=1e-9) if 'cost' in summary else None,
            'tools': dict(tools), 'lengths': dict(lengths), 'steps': steps,
            'note': 'Cumulative message usage including repeated cache reads; recorded cost is not subscription quota.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exports', type=Path, nargs='+')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    results = [analyze(json.loads(path.read_text(encoding='utf-8-sig'))) for path in args.exports]
    text = json.dumps({'samples': results}, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(text, encoding='utf-8')
    else:
        print(text, end='')


if __name__ == '__main__':
    main()
