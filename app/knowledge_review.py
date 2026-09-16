"""Version-bound knowledge approval. No model, chapter, audit or export writes.

Previews are read-only signed tickets. Actual operations use the existing snapshot
table and one SQLite transaction with a savepoint per document. The ticket key is
process-local: an unused preview must be refreshed after a server restart.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import date

from . import db, content_review, review_rules

_KEY = secrets.token_bytes(32)
KINDS = 'knowledge_approval'
SCOPE_NAMES = {'general': '通用', 'archive': '会计电子档案', 'expense': '费控',
               'historical': '历史投标参考', 'internal': '内部参考'}
SOURCE_NOTICE = re.compile(r'保密资料|请勿.*?(?:外传|客户)|如无特殊需要.*?客户')
CONFIRMATION = '确认后，所列内容可在其产品、版本及适用范围内作为投标依据。本操作不会自动批准投标章节，也不代表报价、签章或附件准备完成。'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def text_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _read(conn, id):
    doc = db.decode(conn.execute('SELECT * FROM documents WHERE id=?', (id,)).fetchone())
    chunks = [db.decode(r) for r in conn.execute('SELECT * FROM chunks WHERE document_id=? ORDER BY ordinal,id', (id,))]
    return doc, chunks


def revision(doc, chunks):
    # Includes reparse IDs, parsed text, metadata, scope, expiry and review time.
    return digest({'document': doc, 'chunks': chunks}) if doc else 'missing'


def readable_text(text, kind='text'):
    """Exclude navigation/provenance/link-only lines, without a length/page gate.

    This does not fetch or infer attachment contents. Actual body lines, including
    headings inside a substantive block and tables, retain their original bytes.
    """
    if not review_rules.active('knowledge_body'):
        # This is the literal text we have, never the body behind an attachment
        # URL. The approval preview explicitly labels this expanded text range.
        return text.strip()
    if kind == 'heading':
        return ''
    kept, substance = [], False
    for line in text.splitlines():
        s = line.strip().strip('\u200b\ufeff')
        classified = re.sub(r'^(?:[-*+]\s+|\d+[.)、]\s+)', '', s)
        if not s or re.fullmatch(r'[|:\-\s]+', s):
            kept.append(line)
            continue
        if (s.rstrip('：:') in ('下载', '下载地址', '点击下载', '查看附件', '附件', '点击查看', '阅读原文', '原文链接', '查看详情', 'PDF版本', 'Word版本', '往期清单')
                or SOURCE_NOTICE.search(s)
                or re.fullmatch(r'(?:旗舰版(?:APP\s*)?|国际版)?功能清单\s*20\d{2}(?:Q[1-4])?', s)
                or re.match(r'^(?:语雀来源|采集时间|业务范围|资料覆盖提示|附件下载|阅读版)[：:(（]', s)
                or s.startswith('【原文含访问凭据') or s == '若有收获，就点个赞吧'
                or re.fullmatch(r'!?\[[^\]]*\]\(https?://.*\)|<?https?://\S+>?', classified)
                or re.fullmatch(r'.*\.(?:docx?|pdf|pptx?|xlsx?|zip)(?:\s*\([^)]*\))?', s, re.I)
                or re.fullmatch(r'[（(]?[\d.]+\s*(?:KB|MB|GB)[)）]?', s, re.I)):
            continue
        kept.append(line)
        if not s.startswith(('#', '```', '~~~')) and re.search(r'[\w\u4e00-\u9fff]', s):
            substance = True
    return '\n'.join(kept).strip() if substance else ''


def _constraints(doc):
    meta = doc['metadata']
    return {k: meta[k] for k in ('source_document_id', 'source_url', 'source_date', 'limitations',
            'scope_limit', 'authorization_scope', 'product_version', 'version', 'project_ids',
            'applicable_projects', 'applicability') if meta.get(k)}


def allowed_for_project(row, project_id=None):
    meta = row.get('document_metadata') or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    ids = meta.get('project_ids', meta.get('applicable_projects'))
    return original_source(row.get('document_name', ''), meta) and (
        not review_rules.active('knowledge_scope') or not ids or (isinstance(ids, list) and project_id in ids))


def original_source(name, meta):
    if not review_rules.active('knowledge_origin'):
        return True
    return (not any(meta.get(k) for k in ('ai_generated', 'other_customer_only', 'conflicts_with_project', 'failure_case'))
            and meta.get('source_kind') not in ('generated', 'tender', 'customer_specific')
            and not re.search(r'(?:技术标|商务标|投标书).*?(?:Final|最终|生成稿)|(?:AI|系统)生成(?:稿|的投标)', name, re.I))


def plan(doc, chunks):
    """The single and batch approval decision, including exact recognized text."""
    item = {'id': doc['id'] if doc else '', 'name': doc['name'] if doc else '资料不存在',
            'scope': doc['scope'] if doc else '', 'status': 'blocked', 'reason': '',
            'recognized_scope': '无可认可内容', 'entries': [], 'constraints': {}, 'expanded': False}
    def blocked(reason):
        item['reason'] = reason
        return item
    if not doc:
        return blocked('资料已删除或ID无效')
    if review_rules.active('knowledge_identity') and (doc['project_id'] or doc['source_type'] != 'knowledge'):
        return blocked('采购文件不能作为企业能力资料批准')
    if review_rules.active('knowledge_parse') and doc['parse_status'] != 'ready':
        return blocked('解析未完成或失败，请先核对解析结果')
    if review_rules.active('knowledge_expiry') and (doc['status'] == 'expired' or (doc['valid_until'] and doc['valid_until'] < date.today().isoformat())):
        return blocked('资料已停用或明确过期；本操作不续期')
    if review_rules.active('knowledge_scope') and doc['scope'] not in ('general', 'archive', 'expense'):
        return blocked('历史投标/内部参考用途不作为通用产品事实，请先单条核对用途及内容')
    meta = doc['metadata']
    item['constraints'] = _constraints(doc)
    notices = [line for c in chunks for line in c['text'].splitlines() if SOURCE_NOTICE.search(line)]
    if notices:
        item['constraints']['source_notices'] = list(dict.fromkeys(notices))
    item['valid_until'] = doc['valid_until']
    # Explicit provenance takes precedence over approval. Name checks only catch
    # clearly identified generated bid drafts, not product features such as AI开票.
    if not original_source(doc['name'], meta):
        return blocked('生成稿、客户专属或冲突资料不能自动作为产品原始事实；需先明确可复用范围')
    version_check = review_rules.active('knowledge_version')
    curated = version_check and meta.get('evidence_kind') == 'curated_extract'
    existing = meta.get(KINDS)
    if curated:
        item['recognized_scope'] = '保留原有部分摘录认可范围（不扩大为全文）'
    elif version_check and existing and existing.get('mode') == 'partial':
        item['recognized_scope'] = '保留原有部分正文认可范围（不扩大为全文）'
    else:
        item['recognized_scope'] = ('当前已解析正文' if review_rules.active('knowledge_body')
                                    else '当前已读取文本（含标题、索引或链接；不含未获取的附件正文）')
    prior = {r['id']: r for r in (existing or {}).get('entries', [])}
    stale = existing and any(c['id'] not in prior or prior[c['id']]['sha256'] != text_hash(c['text'])
                             for c in chunks if readable_text(c['text'], c['kind']))
    if version_check and existing and (existing.get('mode') == 'partial' or doc['status'] == 'approved'):
        current = {c['id']: c for c in chunks}
        if any(e['id'] not in current or e['sha256'] != text_hash(current[e['id']]['text']) for e in existing['entries']):
            return blocked('原认可内容版本已变化；需要重新核对范围，不能自动扩大认可')
    if stale and not curated and doc['status'] != 'approved' and existing.get('mode') != 'partial':
        item['expanded'] = True
        item['recognized_scope'] += '（原认可版本已变化，本次重新认可当前版本）'
    excluded = 0
    for chunk in chunks:
        text = chunk['text']
        if version_check and existing and (doc['status'] == 'approved' or existing.get('mode') == 'partial'):
            entry = prior.get(chunk['id'])
            if not entry or entry['sha256'] != text_hash(text):
                continue
            body = entry['text']
        elif curated:
            if '\n原文：\n' not in text:
                continue
            body = text.split('\n原文：\n', 1)[1].strip()
            audited = meta.get('audited_excerpts')
            if isinstance(audited, list) and not any(a.get('quote', '').strip() == body and a.get('sha256') == text_hash(a.get('quote', '')) for a in audited):
                continue
        else:
            body = readable_text(text, chunk['kind'])
        if not body:
            continue
        if review_rules.active('knowledge_origin') and not content_review.source_allowed({'document_name': doc['name'], 'text': body, 'document_metadata': meta}):
            excluded += 1
            continue
        item['entries'].append({'id': chunk['id'], 'sha256': text_hash(text), 'text': body, 'locator': chunk['locator']})
    linked = any(re.search(r'https?://|附件|\.(?:pdf|docx|pptx|xlsx)\b', c['text'], re.I) for c in chunks)
    item['attachment_note'] = '附件正文不在本次认可范围内；只认可实际读取部分' if linked else ''
    if not item['entries']:
        return blocked('附件正文未入库，无可用正文' if linked else '没有可用正文，或仅含采购要求/冲突等不可复用内容')
    if excluded:
        item['recognized_scope'] += f'；排除{excluded}段采购要求、客户专属或冲突内容'
    if not version_check and (existing or meta.get('evidence_kind') == 'curated_extract'):
        original_entries = (existing or {}).get('entries', [])
        item['expanded'] = not existing or item['entries'] != original_entries
        if item['expanded']:
            item['recognized_scope'] += '（已关闭版本/局部范围限制，本次明确重新认可所列当前内容）'
    item['mode'] = 'partial' if curated or excluded or (version_check and (existing or {}).get('mode') == 'partial') else 'parsed'
    unchanged = doc['status'] == 'approved' and not item['expanded']
    item['status'] = 'already_approved' if unchanged else 'eligible'
    item['reason'] = '已批准且范围未变，跳过，不重复写入' if unchanged else '可批准：符合当前已启用的审核规则；实际认可范围见预览'
    item['disabled_rules'] = [rule for rule in ('knowledge_identity', 'knowledge_parse', 'knowledge_expiry',
                                'knowledge_scope', 'knowledge_origin', 'knowledge_version', 'knowledge_body')
                              if not review_rules.active(rule)]
    return item


def _public(item):
    return {**{k: v for k, v in item.items() if k != 'entries'},
            'readable_blocks': len(item['entries']), 'readable_chars': sum(len(e['text']) for e in item['entries']),
            'content_preview': [{'locator': e['locator'], 'text': e['text']} for e in item['entries']]}


def _counts(items):
    return {s: sum(r['status'] == s for r in items) for s in
            ('eligible', 'approved', 'already_approved', 'changed', 'blocked', 'failed', 'undone', 'conflict')}


def _rules_revision():
    return review_rules.revision({key: review_rules.active(key) for key in review_rules.EDITABLE})


@review_rules.governed
def preview(ids):
    ids = list(dict.fromkeys(ids))
    if not ids or len(ids) > 2000:
        raise ValueError('请选择1至2000份明确的资料ID')
    items, versions = [], []
    with db.connect() as conn:
        conn.execute('BEGIN')
        for id in ids:
            doc, chunks = _read(conn, id)
            item = plan(doc, chunks)
            item['id'] = id
            items.append(_public(item))
            versions.append({'id': id, 'revision': revision(doc, chunks)})
    payload = {'id': db.uid(), 'issued': time.time(), 'documents': versions, 'database': str(db.DATA.resolve()),
               'rules_revision': _rules_revision()}
    raw = json.dumps(payload, separators=(',', ':')).encode()
    ticket = base64.urlsafe_b64encode(raw).decode() + '.' + hmac.new(_KEY, raw, hashlib.sha256).hexdigest()
    return {'operation_id': payload['id'], 'ticket': ticket, 'selected': len(ids), 'items': items,
            'rules_revision': payload['rules_revision'],
            'counts': _counts(items), 'confirmation': CONFIRMATION}


def _write_approval(conn, doc, item):
    """Atomic document state + exact permitted evidence, shared with single edit."""
    metadata = {**doc['metadata'], KINDS: {'mode': item['mode'], 'entries': item['entries'],
                'source_sha256': doc['sha256'], 'recognized_scope': item['recognized_scope'],
                'source_notices': item['constraints'].get('source_notices', []), 'approved_at': db.now()}}
    conn.execute('UPDATE documents SET status=?,metadata=?,updated_at=? WHERE id=?',
                 ('approved', json.dumps(metadata, ensure_ascii=False), db.now(), doc['id']))


def _operation(conn, id):
    row = db.decode(conn.execute('SELECT * FROM project_snapshots WHERE id=?', (id,)).fetchone())
    return row['payload'] if row and row['payload'].get('kind') == KINDS else None


@review_rules.governed
def apply(ticket):
    from . import workflow
    try:
        encoded, signature = ticket.split('.')
        raw = base64.urlsafe_b64decode(encoded)
        payload = json.loads(raw)
    except (ValueError, TypeError, KeyError):
        raise ValueError('无效预览，请重新预览')
    with workflow.editing(None), db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        previous = _operation(conn, payload.get('id'))
        ticket_hash = text_hash(ticket)
        if previous and hmac.compare_digest(previous.get('ticket_hash', ''), ticket_hash):
            return previous['result']  # Durable idempotency, including after restart/undo.
        if (not hmac.compare_digest(signature, hmac.new(_KEY, raw, hashlib.sha256).hexdigest())
                or time.time() - payload['issued'] > 3600 or payload['database'] != str(db.DATA.resolve())):
            raise ValueError('预览已失效（超过1小时或服务重启），请重新预览')
        if review_rules.active('operation_revision') and payload.get('rules_revision') != _rules_revision():
            raise ValueError('审核设置在预览后变化，请重新预览认可范围')
        items, changes = [], []
        for selected in payload['documents']:
            id = selected['id']
            doc, chunks = _read(conn, id)
            item = plan(doc, chunks)
            item['id'] = id
            if review_rules.active('operation_revision') and revision(doc, chunks) != selected['revision']:
                item.update(status='changed', reason='预览后内容、范围、审核状态变化或已删除；跳过，请重新预览')
            elif item['status'] == 'eligible':
                conn.execute('SAVEPOINT approve_document')
                try:
                    before = {k: doc[k] for k in ('status', 'metadata')}
                    _write_approval(conn, doc, item)
                    after_doc, after_chunks = _read(conn, id)
                    change = {'id': id, 'name': doc['name'], 'before': before,
                              'before_version': revision(doc, chunks), 'after_version': revision(after_doc, after_chunks),
                              'after': {'status': after_doc['status'], 'metadata': after_doc['metadata']}}
                    conn.execute('RELEASE approve_document')
                    changes.append(change)
                    item.update(status='approved', reason='已批准；获准正文已可供检索和生成使用，适用条件保留')
                except Exception as exc:
                    conn.execute('ROLLBACK TO approve_document')
                    conn.execute('RELEASE approve_document')
                    item.update(status='failed', reason=f'本份执行失败，已回滚：{type(exc).__name__}: {exc}')
            items.append(_public(item))
        result = {'operation_id': payload['id'], 'selected': len(items), 'items': items, 'counts': _counts(items),
                  'created_at': db.now(), 'can_undo': bool(changes)}
        record = {'kind': KINDS, 'ticket_hash': ticket_hash, 'changes': changes, 'result': result}
        conn.execute('INSERT INTO project_snapshots(id,label,payload,created_at) VALUES(?,?,?,?)',
                     (payload['id'], '企业知识库批量批准', json.dumps(record, ensure_ascii=False), result['created_at']))
    return result


@review_rules.governed
def undo(id):
    from . import workflow
    with workflow.editing(None), db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        op = _operation(conn, id)
        if not op:
            raise ValueError('批准操作不存在')
        if op.get('undo'):
            return op['undo']
        items = []
        for change in op['changes']:
            doc, chunks = _read(conn, change['id'])
            item = {'id': change['id'], 'name': change['name']}
            if not doc or (review_rules.active('restore_conflict') and revision(doc, chunks) != change['after_version']):
                item.update(status='conflict', reason='资料已被编辑、重新解析或再次审核；保留后续决定')
            else:
                # Only this operation's review fields. Never restore chunks or a database.
                conn.execute('UPDATE documents SET status=?,metadata=?,updated_at=? WHERE id=?',
                             (change['before']['status'], json.dumps(change['before']['metadata'], ensure_ascii=False), db.now(), change['id']))
                item.update(status='undone', reason='已恢复本次批准前的状态及认可范围')
            items.append(item)
        result = {'operation_id': id, 'selected': len(items), 'items': items, 'counts': _counts(items), 'created_at': db.now(), 'can_undo': False}
        op['undo'] = result
        conn.execute('UPDATE project_snapshots SET payload=? WHERE id=?', (json.dumps(op, ensure_ascii=False), id))
    return result


def operations():
    result = []
    for row in db.all("SELECT id,payload,created_at FROM project_snapshots WHERE project_id IS NULL AND document_id IS NULL ORDER BY created_at DESC"):
        op = row['payload']
        if op.get('kind') == KINDS:
            result.append({'id': row['id'], 'created_at': row['created_at'], 'counts': op['result']['counts'],
                           'can_undo': bool(op['changes']) and not op.get('undo'), 'undone': bool(op.get('undo'))})
        if len(result) == 10:
            break
    return result


@review_rules.governed
def review_single(id, values):
    """Existing single review UI, same plan/write rules; no implicit range expansion."""
    with db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        doc, chunks = _read(conn, id)
        if not doc or (review_rules.active('knowledge_identity') and (doc['project_id'] or doc['source_type'] != 'knowledge')):
            raise ValueError('企业资料不存在或是招标文件')
        if values.get('status', doc['status']) not in ('pending', 'approved', 'expired'):
            raise ValueError('未知资料状态')
        if values.get('scope', doc['scope']) not in SCOPE_NAMES:
            raise ValueError('未知资料用途')
        if values.get('valid_until'):
            try:
                if date.fromisoformat(values['valid_until']).isoformat() != values['valid_until']:
                    raise ValueError()
            except ValueError:
                raise ValueError('有效期应为 YYYY-MM-DD')
        candidate = {**doc, **{k: v for k, v in values.items() if k != 'warnings_acknowledged'}}
        # A single review may explicitly change status/expiry. Scope recognition
        # still uses the old status to detect preservation vs initial approval.
        approval_candidate = {**candidate, 'status': doc['status']}
        if values.get('status') == 'approved':
            item = plan(approval_candidate, chunks)
            if item['status'] == 'blocked':
                raise ValueError(item['reason'])
            if item.get('expanded'):
                raise ValueError('认可版本已变化，请使用批量预览明确确认当前正文范围')
            if item['status'] == 'eligible':
                    _write_approval(conn, candidate, item)
                    candidate, _ = _read(conn, id)
                    candidate.update({k: v for k, v in values.items() if k in ('status', 'scope', 'valid_until')})
        if 'warnings_acknowledged' in values:
            candidate['metadata'] = {**candidate['metadata'], 'warnings_acknowledged': values['warnings_acknowledged']}
        updates = {k: candidate[k] for k in ('status', 'scope', 'valid_until', 'metadata')}
        if any(doc[k] != v for k, v in updates.items()):
            conn.execute('UPDATE documents SET status=?,scope=?,valid_until=?,metadata=?,updated_at=? WHERE id=?',
                         (updates['status'], updates['scope'], updates['valid_until'], json.dumps(updates['metadata'], ensure_ascii=False), db.now(), id))
        return db.decode(conn.execute('SELECT * FROM documents WHERE id=?', (id,)).fetchone())
