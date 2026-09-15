"""Small transport receipts backed by immutable, operation-scoped files."""
import hashlib
import json
import re
import os
import tempfile
from pathlib import Path

from studio_core import StudioError, atomic_write_json, safe_name, session_lock

PART_BYTES = 12 * 1024


def split_packet(text):
    """Preserve every character; prefer section boundaries, then UTF-8 boundaries."""
    parts, current = [], ""
    for section in re.split(r"(?m)(?=^#{1,4} )", text):
        while section:
            if len((current + section).encode('utf-8')) <= PART_BYTES:
                current += section
                break
            if current:
                parts.append(current)
                current = ""
                continue
            raw = section.encode('utf-8')[:PART_BYTES]
            piece = raw.decode('utf-8', errors='ignore')
            parts.append(piece)
            section = section[len(piece):]
    if current:
        parts.append(current)
    return parts


def deliver(root, result, context_delivery='reference'):
    if context_delivery not in {'reference', 'inline'}:
        raise StudioError('context_delivery 必须为 reference 或 inline')
    if context_delivery == 'inline':
        return result
    operation = result.get('operation_id')
    content = result.get('context')
    primary = content is not None
    if content is None and any(result.get(k) for k in ('payload', 'drafts', 'recovery')):
        content = {k: result[k] for k in ('payload', 'drafts', 'recovery') if result.get(k)}
    if not operation or content is None:
        return result
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2)
    original_checksum = hashlib.sha256(text.encode('utf-8')).hexdigest()
    # Recovery input and drafts are also handed over by file, never echoed twice.
    if primary and (result.get('drafts') or result.get('recovery')):
        text += '\n\n## 恢复材料（沿用原操作）\n' + json.dumps({
            'drafts': result.get('drafts', {}), 'recovery': result.get('recovery', {})}, ensure_ascii=False)
    checksum = hashlib.sha256(text.encode('utf-8')).hexdigest()
    folder = Path(root) / '.runtime/delivery' / safe_name(operation) / checksum
    if not folder.resolve().is_relative_to(Path(root).resolve()):
        raise StudioError('上下文交付路径超出工作区')
    with session_lock(Path(root)):
        binding = folder.parent / 'context-binding.json'
        if primary and binding.exists():
            saved = json.loads(binding.read_text(encoding='utf-8'))
            if saved.get('sha256') != original_checksum:
                raise StudioError('同一操作的不可变上下文已变化；请通过原事务恢复或归档')
        elif primary:
            atomic_write_json(binding, {'operation_id': operation, 'sha256': original_checksum})
        chunks = split_packet(text)
        entries = []
        for index, chunk in enumerate(chunks, 1):
            path = folder / f'{index:03d}.md'
            if path.exists():
                if path.read_bytes() != chunk.encode('utf-8'):
                    raise StudioError('不可变上下文快照校验失败')
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(dir=path.parent)
                try:
                    with os.fdopen(fd, 'wb') as handle:
                        handle.write(chunk.encode('utf-8'))
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, path)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            entries.append({'order': index, 'path': str(path), 'chars': len(chunk),
                            'bytes': len(chunk.encode('utf-8')),
                            'sha256': hashlib.sha256(chunk.encode('utf-8')).hexdigest()})
    keep = ('kind', 'status', 'operation_id', 'regenerate', 'topic_id', 'module', 'prompt_id',
            'version', 'request_id', 'has_draft', 'omitted', 'from_turn', 'through_turn', 'source_event_id')
    receipt = {key: result[key] for key in keep if key in result}
    receipt.update(context_delivery='reference', context_chars=len(text), context_sha256=checksum,
                   context_parts=entries,
                   next='按清单顺序读取全部分段一次；无需枚举目录、探测结构或再次 prepare。审核后沿用 operation_id 提交。软字数不单独计数或反复找补。')
    if result.get('motif_ids'):
        receipt['motif_ids'] = result['motif_ids']
    if result.get('commit_contract'):
        receipt['commit_contract'] = result['commit_contract']
    if result.get('status') == 'prepared':
        receipt['next'] = '按顺序读取全部压缩候选分段，主 Agent 审核后以本 operation_id、through_turn 应用记忆；不要新增回合或重复压缩。'
    elif result.get('status') == 'pending':
        receipt['next'] = '提交尚在恢复中。分段保留原 payload；以原 operation_id 调用 bible_operation(action="resume")，不重新生成或改写正文。'
    elif result.get('status') != 'ready':
        receipt['next'] = result.get('next') or '当前状态不是 ready；不得起草或提交。按状态恢复原操作。'
        if result.get('report'):
            receipt['report'] = result['report']
    return receipt
