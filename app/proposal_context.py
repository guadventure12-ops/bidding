"""Version-bound, role-labelled reference plans for a single tender sample.

Read-only. This module never approves sources, appoints people, declares delivery
complete, or changes the user's review switches. Reference context is deliberately
separate from product-fact retrieval. Citations always retain original chunk IDs.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from contextlib import contextmanager
import contextvars
import hashlib
import json
import re
from pathlib import Path

from . import db, evidence_retrieval, review_rules

VERSION = 'proposal-reference-context-1'
ROLES = frozenset({'same_tender_proposed_plan', 'conditional_resource_recommendation', 'procurement_requirement'})
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
_OPERATION_CONTEXT = contextvars.ContextVar('proposal_reference_operation', default=None)


class ContextValidationError(ValueError):
    pass


def _object(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            result = json.loads(value)
            if isinstance(result, dict):
                return result
        except ValueError:
            pass
    return {}


def _sha(text):
    return hashlib.sha256(str(text).encode('utf-8')).hexdigest()


def _error(message):
    raise ContextValidationError(message)


@lru_cache(maxsize=64)
def _file_sha(path, size, modified_ns):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(data)
    return digest.hexdigest()


def _source_file_matches(doc, expected):
    text = str(doc.get('path') or '')
    if not text or text.startswith(('\\\\', '//')):
        return False
    path = Path(text)
    try:
        stat = path.stat()
        return path.is_file() and _file_sha(str(path.resolve()), stat.st_size, stat.st_mtime_ns) == expected
    except OSError:
        return False


def _manifest(project):
    meta = _object(project.get('metadata'))
    if meta.get('generation_profile') != 'technical_proposal':
        return None
    setting = meta.get('proposal_reference_context')
    if setting is None:
        return None
    if not isinstance(setting, dict) or not isinstance(setting.get('path'), str) or not setting.get('sha256'):
        _error('参考方案配置必须包含明确的本地manifest路径与SHA256')
    raw_path = setting['path']
    if raw_path.startswith(('\\\\', '//')):
        _error('参考方案manifest不允许使用网络路径')
    root = (db.DATA / 'assets').resolve()
    path = Path(raw_path)
    if not path.is_absolute():
        path = db.DATA / path
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError:
        _error('参考方案manifest必须位于当前DATA/assets目录')
    if not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
        _error('参考方案manifest缺失或超过大小上限')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != setting['sha256']:
        _error('参考方案manifest已变化，请重新核对并绑定版本')
    try:
        manifest = json.loads(raw)
    except ValueError:
        _error('参考方案manifest不是有效JSON')
    if not isinstance(manifest, dict) or manifest.get('version') != VERSION:
        _error('参考方案manifest版本不受支持')
    manifest['_path'] = str(path)
    manifest['_sha256'] = setting['sha256']
    return manifest


def load_context(project):
    """Load and validate all selected reference inputs, regardless of review flags.

    ``metadata.proposal_reference_context`` is ``{path, sha256}``. A relative
    path is relative to DATA and must remain under DATA/assets. Missing optional
    config returns disabled; configured but stale/invalid input raises a concrete
    ContextValidationError instead of silently generating without the material.
    """
    manifest = _manifest(project)
    if manifest is None:
        return {'enabled': False, 'entries': {}, 'groups': [], 'diagnostics': {'reason': 'not_configured'}}
    binding = _object(manifest.get('binding'))
    expected_tenders = binding.get('tender_sha256s')
    if (not isinstance(expected_tenders, list) or not expected_tenders
            or any(not isinstance(x, str) or not x for x in expected_tenders)):
        _error('参考方案缺少明确招标文件版本集合')
    if not project.get('project_number') or project['project_number'] != binding.get('project_number'):
        _error('参考方案项目编号与当前项目不一致')
    tenders = db.all("SELECT * FROM documents WHERE project_id=? AND source_type='tender' ORDER BY id", (project['id'],))
    if (not tenders or sorted(d['sha256'] for d in tenders) != sorted(expected_tenders)
            or any(d['parse_status'] != 'ready' or not _source_file_matches(d, d['sha256']) for d in tenders)):
        _error('当前招标文件集合、解析状态或原件版本与参考方案绑定不一致')
    source_specs = manifest.get('sources')
    entry_specs = manifest.get('entries')
    group_specs = manifest.get('groups')
    if (not isinstance(source_specs, list) or not 1 <= len(source_specs) <= 32
            or not isinstance(entry_specs, list) or not 1 <= len(entry_specs) <= 10000
            or not isinstance(group_specs, list) or not 1 <= len(group_specs) <= 5000):
        _error('参考方案来源、正文或分组结构无效')
    source_ids = [s.get('document_id') for s in source_specs if isinstance(s, dict)]
    if len(source_ids) != len(source_specs) or len(set(source_ids)) != len(source_ids) or any(not x for x in source_ids):
        _error('参考方案资料ID缺失或重复')
    sources = {}
    today = date.today().isoformat()
    project_meta = _object(project.get('metadata'))
    trusted = project_meta.get('trusted_sources')
    for spec in source_specs:
        doc = db.one('SELECT * FROM documents WHERE id=?', (spec['document_id'],))
        if not doc or doc['source_type'] != 'knowledge' or doc.get('project_id'):
            _error('参考方案包含不存在、采购或项目生成资料：' + str(spec['document_id']))
        if doc['status'] != 'approved' or doc['parse_status'] != 'ready':
            _error('参考资料未批准或解析状态变化：' + doc['id'])
        if doc['scope'] not in ('general', project.get('domain')) or doc['scope'] != spec.get('scope'):
            _error('参考资料产品适用范围已变化：' + doc['id'])
        expiry = doc.get('valid_until')
        if expiry:
            try:
                date.fromisoformat(expiry)
            except (TypeError, ValueError):
                _error('参考资料有效期格式无效：' + doc['id'])
            if expiry < today:
                _error('参考资料已明确过期：' + doc['id'])
        if doc['sha256'] != spec.get('source_sha256') or not _source_file_matches(doc, doc['sha256']):
            _error('参考资料原件版本已变化：' + doc['id'])
        meta = _object(doc.get('metadata'))
        if meta.get('ai_generated') or meta.get('source_kind') in ('generated', 'tender'):
            _error('系统生成稿不能冒充已核对参考原件：' + doc['id'])
        allowed_projects = meta.get('project_ids', meta.get('applicable_projects'))
        if allowed_projects and (not isinstance(allowed_projects, list) or project['id'] not in allowed_projects):
            _error('参考资料限定的项目范围不匹配：' + doc['id'])
        if 'trusted_sources' in project_meta:
            saved = trusted.get(doc['id']) if isinstance(trusted, dict) else None
            if (not isinstance(saved, dict) or saved.get('sha256') != doc['sha256']
                    or saved.get('source_updated_at') != doc.get('updated_at')):
                _error('参考资料不在本项目当前认可版本集合：' + doc['id'])
        approval = meta.get('knowledge_approval')
        if approval is not None:
            if (not isinstance(approval, dict) or not isinstance(approval.get('entries'), list)
                    or approval.get('source_sha256') != doc['sha256']):
                _error('参考资料认可版本记录不匹配：' + doc['id'])
            approved = {e['id']: e for e in approval['entries'] if isinstance(e, dict) and e.get('id')}
        else:
            if spec.get('approval_basis') != 'approved_legacy_document' or meta.get('evidence_kind') == 'curated_extract':
                _error('参考资料缺少对应的正文认可记录：' + doc['id'])
            approved = None
        sources[doc['id']] = {'document': doc, 'metadata': meta, 'approved': approved}
    query = 'SELECT * FROM chunks WHERE document_id IN (' + ','.join('?' for _ in source_ids) + ')'
    chunks = {c['id']: c for c in db.all(query, source_ids)}
    entries = {}
    for spec in entry_specs:
        if not isinstance(spec, dict) or spec.get('role') not in ROLES:
            _error('参考正文必须具有明确的采购约束、拟方案或资源建议角色')
        id = spec.get('chunk_id')
        doc_id = spec.get('document_id')
        if not id or id in entries or doc_id not in sources:
            _error('参考正文ID缺失、重复或来源不在manifest内')
        chunk = chunks.get(id)
        if not chunk or chunk['document_id'] != doc_id or _sha(chunk['text']) != spec.get('chunk_sha256'):
            _error('参考正文被删除、重解析或已修改：' + id)
        if spec.get('locator') is not None and chunk['locator'] != spec['locator']:
            _error('参考正文来源定位已变化：' + id)
        source = sources[doc_id]
        approval = source['approved']
        if approval is not None:
            approved = approval.get(id)
            if not approved or approved.get('sha256') != _sha(chunk['text']):
                _error('参考正文不在当前认可范围：' + id)
            text = approved.get('text')
        else:
            text = chunk['text']
        if not isinstance(text, str) or not text.strip() or _sha(text) != spec.get('text_sha256'):
            _error('参考正文实际认可范围已变化：' + id)
        if spec.get('text') is not None and spec['text'] != text:
            _error('manifest正文不能改写来源原文：' + id)
        if not evidence_retrieval._source_subset(text, chunk['text']):
            _error('认可正文无法在已读取原文中定位：' + id)
        conditions = {k: source['metadata'][k] for k in ('product_version','version','scope_limit','authorization_scope',
                      'limitations','project_ids','applicable_projects','applicability') if source['metadata'].get(k)}
        entries[id] = {**chunk, 'text': text, 'support_text': text, 'document_name': source['document']['name'],
                       'document_sha256': source['document']['sha256'], 'reference_role': spec['role'],
                       'evidence_kind': 'proposal_reference', 'claim_status': ('procurement_constraint_not_enterprise_capability' if spec['role']=='procurement_requirement' else 'proposed_not_executed_or_committed'),
                       'topic_path': spec.get('topic_path', []),
                       'source_constraints': {**conditions, 'role': spec['role'], 'project_number': project['project_number'],
                           'tender_sha256s': expected_tenders, 'source_text_sha256': spec['chunk_sha256'],
                           'recognized_text_sha256': spec['text_sha256'],
                           'modality': ('只证明采购要求/约束，不证明企业已实现、拟实现或作出本次能力承诺' if spec['role']=='procurement_requirement' else spec.get('modality', '保留拟/计划/建议及原条件，不证明已落实')),
                           'appointment_confirmed': False, 'delivery_completed': False}}
    groups = []
    seen = set()
    for group in group_specs:
        if not isinstance(group, dict) or not group.get('id') or group['id'] in seen or group.get('role') not in ROLES:
            _error('参考方案分组无效或重复')
        ids = group.get('chunk_ids')
        if (not isinstance(ids, list) or not ids or len(set(ids)) != len(ids)
                or any(id not in entries or entries[id]['reference_role'] != group['role'] for id in ids)):
            _error('参考表组包含越界、缺失或错误角色的正文ID')
        seen.add(group['id'])
        groups.append({**group, 'text': '\n'.join(entries[id]['text'] for id in ids)})
    fingerprint = _sha(json.dumps({'manifest':manifest['_sha256'], 'sources':[
        {k:s['document'].get(k) for k in ('id','sha256','status','scope','valid_until','parse_status','updated_at')}
        for s in sources.values()], 'entries':[(id,e['locator'],_sha(e['text'])) for id,e in entries.items()]}, sort_keys=True))
    return {'enabled': True, 'project_id':project['id'],'project_domain':project.get('domain'),
            'configured_path':project_meta['proposal_reference_context']['path'],
            'context_fingerprint':fingerprint,'manifest_path': manifest['_path'], 'manifest_sha256': manifest['_sha256'],
            'entries': entries, 'groups': groups, 'binding': binding,
            'diagnostics': {'validated_sources': len(sources), 'validated_entries': len(entries),
                            'validated_groups': len(groups), 'roles': sorted(ROLES)}}


def _context(project, context=None):
    loaded = context if context is not None else _OPERATION_CONTEXT.get()
    if loaded is None:
        return load_context(project)
    if not loaded.get('enabled'):
        return load_context(project)
    meta = _object(project.get('metadata'))
    setting = _object(meta.get('proposal_reference_context'))
    if (loaded.get('project_id') != project.get('id') or loaded.get('project_domain') != project.get('domain')
            or meta.get('generation_profile') != 'technical_proposal'
            or loaded['binding'].get('project_number') != project.get('project_number')
            or loaded.get('manifest_sha256') != setting.get('sha256')
            or loaded.get('configured_path') != setting.get('path')):
        _error('预加载参考方案不属于当前项目或配置版本')
    return loaded


@contextmanager
def context_scope(project, context=None):
    """Reuse one validated snapshot inside one review/export operation.

    This is not a cross-request cache. The caller must keep its normal source
    revision guard and call ``assert_context_current`` BEFORE persisting results
    if sources may change during a long operation. Nested evidence resolution
    costs only ID lookups; ordinary calls outside the scope always validate fresh.
    """
    loaded = load_context(project) if context is None else _context(project,context)
    token = _OPERATION_CONTEXT.set(loaded)
    try:
        yield loaded
    finally:
        _OPERATION_CONTEXT.reset(token)


def assert_context_current(project, context):
    """Revalidate source/approval/tender versions before the caller commits."""
    _context(project,context)
    fresh = load_context(project)
    if fresh.get('enabled') != context.get('enabled') or fresh.get('context_fingerprint') != context.get('context_fingerprint'):
        _error('操作期间参考资料版本或范围发生变化，结果不能按原快照保存')
    return fresh


def resolve_evidence(project, ids, *, context=None):
    """Resolve IDs with roles; optional context is an explicit operation snapshot."""
    loaded = _context(project,context)
    return [loaded['entries'][id] for id in dict.fromkeys(ids) if id in loaded['entries']]


def procurement_claim_issues(text, evidence):
    """Conservative review flags for capability claims citing procurement text.

    Citation identity can be valid while its role cannot support an enterprise
    assertion. No prose is edited and no approval verdict is manufactured.
    """
    from . import proposal_numeric_review as numeric
    indexed={str(row['id']):row for row in evidence if isinstance(row,dict) and row.get('id')}
    findings=[]
    label=re.compile(r'^\s*(?:[#>*-]\s*)*(?:采购(?:文件)?(?:要求|规定|约定|约束)|招标(?:文件)?(?:要求|规定|约定))\s*[：:]')
    ability=re.compile(r'支持|实现|具备|提供|满足|达到|采用|完成|允许|能够')
    for offset, statement in numeric._statements(str(text or '')):
        cited=set(numeric._CITATION.findall(statement))
        procurement=[indexed[id] for id in cited if id in indexed and numeric._role(indexed[id])=='procurement_requirement']
        if not procurement:
            continue
        if label.search(statement) and numeric._original_quote(statement,[numeric._support(row) for row in procurement]):
            continue
        if not ability.search(statement):
            continue
        facts=[indexed[id] for id in cited if id in indexed and numeric._role(indexed[id])=='enterprise_fact']
        core=re.sub(r'^\s*(?:[#>*-]\s*)*(?:我方|本公司)\s*','',statement)
        if any(numeric._plain(core) and numeric._plain(core) in numeric._plain(numeric._support(row)) for row in facts):
            continue
        findings.append({'statement':statement.strip(),'start':offset,'evidence_ids':[row['id'] for row in procurement],
            'reason':'能力陈述引用了采购要求复述；该角色不能证明企业已具备或已实现，需补充直接产品依据或明确采购约束语境'})
    return findings


def _label_only(group, entries):
    """Deprioritize source labels without declaring short approved text invalid."""
    labels = {str(x).strip().rstrip('：:') for x in group.get('topic_path',[])}
    labels.add(str(group.get('title','')).strip().rstrip('：:'))
    for id in group['chunk_ids']:
        body=entries[id]['text'].strip()
        clean=body.rstrip('：:')
        if clean in labels:
            continue
        if '|' in body or re.search(r'[。；！？]|将|拟|应|须|可|支持|提供|开展|完成|能够|实现|负责|采用|具备|组织|制定|确保|帮助|进行|覆盖|包括|按照|根据|记录|允许',body):
            return False
        if body.endswith(('：',':')) or re.search(r'培训|方案|机制|计划|要求|目标|职责|标准|原则|环境|配置',body):
            continue
        return False
    return True


def search_context(project, section, requirements=(), *, limit=8, max_chars=18000, context=None):
    """Retrieve bounded topic-first groups, never truncating table conditions.

    ``evidence`` uses real chunk IDs. ``prompt_context`` deliberately omits the
    internal group ID; callers may map chunk IDs to their ordinary prompt aliases.
    No retrieval result is a verdict that a plan has already been executed.
    """
    loaded = _context(project,context)
    empty = {'enabled': loaded['enabled'], 'evidence': [], 'groups': [], 'prompt_context': [],
             'diagnostics': dict(loaded['diagnostics'])}
    if not loaded['enabled'] or limit <= 0 or max_chars <= 0:
        return empty
    max_chars = min(60000, int(max_chars))
    title = str(section.get('title') or '')
    group_title = str(section.get('outline_group_title') or '')
    req_text = '\n'.join(str(r.get('title','')) + ' ' + str(r.get('text','')) for r in requirements)
    query = '\n'.join((group_title, title, req_text))[:12000]
    ranked_rows = [{'id':g['id'], 'text':' / '.join(g.get('topic_path',[]))+'\n'+g.get('title','')+'\n'+g['text']}
                   for g in loaded['groups']]
    # Build_index is used only as a lexical index here, with already verified
    # text and no document approval metadata to re-expand. This scope is local.
    with review_rules.scope({'knowledge_version':True,'knowledge_body':True}):
        index = evidence_retrieval.build_index(ranked_rows)
    ranked = evidence_retrieval._rank(index, query)
    score = {ranked_rows[pos]['id']: value for pos,value in ranked}
    exact = lambda g: int(bool(group_title and g.get('topic_path') and g['topic_path'][0] == group_title))
    resource_focus = any(word in group_title+' '+title for word in ('资源','服务器','部署','整体设计'))
    ordered = sorted((g for g in loaded['groups'] if g['id'] in score),
                     key=lambda g:(-exact(g),-int(resource_focus and g['role']=='conditional_resource_recommendation'),
                                   int(_label_only(g,loaded['entries'])),-score[g['id']],g['id']))
    selected=[];used=set();chars=0;oversized=[]
    for group in ordered:
        fresh = [id for id in group['chunk_ids'] if id not in used]
        cost = sum(len(loaded['entries'][id]['text']) for id in fresh)
        if not fresh:
            continue
        if cost > max_chars-chars:
            oversized.append(group['id'])
            continue
        selected.append(group);used.update(fresh);chars+=cost
        if len(selected)>=min(16,int(limit)):
            break
    ids = list(dict.fromkeys(id for group in selected for id in group['chunk_ids']))
    evidence = [loaded['entries'][id] for id in ids]
    prompt=[]
    for group in selected:
        prompt.append({'role':group['role'],'topic_path':group.get('topic_path',[]),'title':group.get('title',''),
                       'conditions':group.get('conditions',[]),
                       'usage':('采购要求/约束的原文，不能作为产品已实现功能或企业能力依据；只允许明确采购语境引用。' if group['role']=='procurement_requirement' else '同项目拟方案或条件建议；保留拟/计划/条件，不能写成已执行、已任命或已经完成交付。'),
                       'source_chunks':[{'id':id,'document_id':loaded['entries'][id]['document_id'],
                                         'locator':loaded['entries'][id]['locator'],'text':loaded['entries'][id]['text'],
                                         'role':loaded['entries'][id]['reference_role']} for id in group['chunk_ids']]})
    return {'enabled':True,'evidence':evidence,'groups':selected,'prompt_context':prompt,
            'diagnostics':{**loaded['diagnostics'],'selected_groups':len(selected),'selected_entries':len(evidence),
                           'text_chars':chars,'skipped_groups_due_to_budget':oversized}}
