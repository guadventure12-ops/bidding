"""Local content review, exact legacy-wrapper cleanup and reversible batch edits.

No provider calls. Previews never write. Original documents and historical model
reviews are never edited; project metadata holds trust and internal work items.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

from . import db, bid_body, approval_risk, review_rules

POLICY_VERSION = 'content-review-4'
MARKER = '【待企业确认的承诺模板】'
GENERIC_GAP = '【待补充：请企业核对本条适用性及所需证明材料】'
# Exact strings verified in workflow.py and the historical local correction
# scripts. No fuzzy removal of arbitrary "待确认", brackets, or model prose.
WRAPPERS = (
    (MARKER + '以下为拟用文本，未表示相关事实已核验或材料已提交。', 'workflow._qualification_templates'),
    (MARKER + '\n以下为待核实的拟用文本，不表示企业事实已核验、证明材料已提交或声明已签署盖章。', 'qa/prepare_qualification_fixes.py'),
    (MARKER + '\n以下仅为依据采购文件拟写的待确认文本，需由企业核实后决定是否出具；不表示所述企业事实已经核验，也不表示已签署或盖章。', 'qa/correct_initial_generation.py'),
    ('本章包含程序知悉及待企业确认的承诺模板。以下资格事实和证明材料须逐项核实后使用。', 'workflow._qualification_templates'),
    ('本章按采购原文列出待企业核实的资格声明模板。企业事实、证明材料和签章状态均未获确认。', 'qa/prepare_qualification_fixes.py'),
    (GENERIC_GAP, 'workflow._qualification_templates'),
)
# Verified historical program notes, transferred only for qualification rows.
# Actual fields, certificate copies, credit queries, prices and people are not
# part of this list and must remain visible as specific outstanding work.
DECLARATION_NOTES = tuple('【待补充：' + text + '】' for text in (
    '企业核对与采购人的附属/控制关系及法律主体情况，提供相应主体和关系说明，确认后决定是否出具该承诺。',
    '企业核对是否存在责令停业或破产状态，提供相应存续状态核查结果，并确认是否出具该承诺。',
    '企业核对是否被暂停或取消响应资格，提供核查结果，并确认是否出具该承诺。',
    '企业核对财产是否存在重组、接管、查封、扣押或冻结情形，提供核查结果，并确认是否出具该承诺。',
    '企业核对最近三年成交记录及是否存在骗取成交情形，提供核查结果，并确认是否出具该承诺。',
    '企业核对最近三年合同履约记录及是否因严重违约被解除合同/协议或取消供应商资格，确认后决定是否出具该承诺。',
    '企业核对法律、法规及采购文件规定的其他禁止情形，确认后决定是否出具该承诺。',
))
PLACEHOLDER = re.compile(r'【(?:待补充|待确认|待核实)(?:[：:\s][^】\n]*)?】')
QUALIFICATION = re.compile(r'资格|独立法人|附属机构|停业|破产|失信|控股|未被|商业信誉')
DECLARATION_MESSAGE = '确认《资格审查资料承诺书》的适用条款及企业声明内容；按采购格式集中签章，不默认要求每个子条款的独立证明'
FIELD = re.compile(r'姓名|名称|金额|报价|单价|数量|地址|信用代码|日期|电话|邮箱|联系人|人员|负责人信息|法定代表人信息')
DELIVERY = re.compile(r'签字|签章|盖章|签署|附件|复印件|扫描件|装订|上传|提交材料')
EXCLUDED_SOURCE = re.compile(r'失败案例|失败原因|反面案例|不适用本项目|与本项目冲突|其他客户专属|客户专属|AI生成|AI 生成|(?:甲方|采购人|招标人)(?:采购)?要求|(?:采购|招标)要求[：:为]|(?:某客户|客户[A-Z]|该客户).*?(?:已|专属|仅)')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def project(id):
    p = db.one('SELECT * FROM projects WHERE id=?', (id,))
    if not p:
        raise ValueError('项目不存在')
    return p


def cleanup_text(text, qualification=False):
    """Return text + exact deletion spans. Preserve all non-deleted bytes."""
    protected = []
    offset, fenced = 0, False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
            protected.append((offset, offset + len(line)))
        elif fenced or line.lstrip().startswith(('#', '|', '>')):
            protected.append((offset, offset + len(line)))
        offset += len(line)
    edits = []
    tokens = WRAPPERS + tuple((text, 'qa/correct_initial_generation.py:pending') for text in DECLARATION_NOTES if qualification)
    for token, origin in tokens:
        for match in re.finditer(re.escape(token), text):
            a, b = match.span()
            if any(a < end and b > start for start, end in protected):
                continue
            line_end = text.find('\n', b)
            if text[text.rfind('\n', 0, a) + 1:a].strip() or text[b:line_end if line_end >= 0 else len(text)].strip():
                continue  # Do not remove a quoted token inside substantive prose.
            edits.append({'start': a, 'end': b, 'text': token, 'origin': origin,
                          'transfer_to': 'qualification_declaration' if token in DECLARATION_NOTES else None})
            # Only a label immediately belonging to a verified wrapper qualifies.
            tail = re.match(r'\s*(拟用文本：|拟确认文本：)', text[b:]) if token.startswith(MARKER) else None
            if tail:
                start = b + tail.start(1)
                edits.append({'start': start, 'end': start + len(tail.group(1)), 'text': tail.group(1), 'origin': origin})
    edits.sort(key=lambda item: item['start'])
    nonoverlap = []
    for item in edits:
        if not nonoverlap or item['start'] >= nonoverlap[-1]['end']:
            nonoverlap.append(item)
    cleaned = text
    for item in reversed(nonoverlap):
        cleaned = cleaned[:item['start']] + cleaned[item['end']:]
    uncertain = []
    for line_no, line in enumerate(cleaned.splitlines(), 1):
        if MARKER in line or '拟用文本：' in line or '不表示企业事实已核验' in line:
            uncertain.append({'line': line_no, 'text': line, 'reason': '不能确认是固定程序包装，保留原文'})
    return {'content': cleaned, 'removed': nonoverlap, 'uncertain': uncertain}


def substantive(text):
    clean = bid_body.separate(cleanup_text(text)['content'])['content']
    clean = PLACEHOLDER.sub('', clean).replace(MARKER, '')
    clean = re.sub(r'\[E:[^\]]*\]|!\[[^\]]*\]\([^)]*\)', '', clean)
    lines = []
    raw_lines = clean.splitlines()
    for index, line in enumerate(raw_lines):
        line = line.strip()
        if not line or line.startswith(('#', '>', '```', '~~~')) or re.fullmatch(r'[|:\-\s]+', line):
            continue
        if re.search(r'^(?:以下为|以下仅为|本章包含|本章按采购|本节为).*?(?:待核实|待确认|待人工审核|待企业)', line):
            continue
        if line in ('拟用文本：', '正文：'):
            continue
        if line.startswith('|') and index + 1 < len(raw_lines) and re.fullmatch(r'[|:\-\s]+', raw_lines[index + 1]):
            continue  # A table header alone is not substantive body text.
        if re.search(r'^(?:本次证据|上述承诺须|此文本仅供|本节为待人工审核|采购条款定位：|来源：)', line):
            continue
        lines.append(line)
    return bool(re.search(r'[\w\u4e00-\u9fff]{4}', '\n'.join(lines)))


@review_rules.governed
def trusted_documents(p):
    """Reuse existing status/scope, plus one project-level document selection."""
    selected = (p.get('metadata') or {}).get('trusted_sources', {})
    has_selection = 'trusted_sources' in (p.get('metadata') or {})
    today = date.today().isoformat()
    accepted = []
    for d in db.all('SELECT * FROM documents' + (' WHERE project_id IS NULL' if review_rules.active('knowledge_identity') else '')):
        saved = selected.get(d['id'], {})
        picked = bool(saved) and (not review_rules.active('knowledge_version') or (saved.get('sha256') == d['sha256'] and saved.get('source_updated_at') == d['updated_at']))
        if ((not review_rules.active('knowledge_parse') or d['parse_status'] == 'ready')
                and (not review_rules.active('knowledge_expiry') or (d['status'] != 'expired' and (not d['valid_until'] or d['valid_until'] >= today)))
                and (not review_rules.active('knowledge_scope') or d['scope'] in ('general', p['domain']))
                and (picked if has_selection else d['status'] == 'approved')):
            accepted.append(d)
    return accepted


def source_allowed(row):
    if not review_rules.active('knowledge_origin'):
        return True
    meta = row.get('document_metadata') or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    return (not EXCLUDED_SOURCE.search(str(row.get('document_name', '')) + '\n' + str(row.get('text', '')))
            and not any(meta.get(k) for k in ('ai_generated', 'other_customer_only', 'conflicts_with_project', 'failure_case')))


def trusted_reuse(text, evidence):
    """Exact statement support, not evidence_ids => entire chapter is supported."""
    clean = re.sub(r'\[E:[^\]]+\]', '', text).strip()
    clean = re.sub(r'^(?:[-*+]\s+|\d+[.)、]\s+)', '', clean).replace('**', '').replace('__', '')
    if len(clean) < 8:
        return []
    normalized = re.sub(r'\s+', '', clean).strip('。；;')
    supported = []
    for row in evidence:
        if row.get('reference_role')=='procurement_requirement' or (row.get('source_constraints') or {}).get('role')=='procurement_requirement':
            continue  # Procurement wording is never reusable proof of enterprise facts.
        if not source_allowed(row):
            continue
        body = re.sub(r'\s+', '', str(row.get('text') or '').replace('**', '').replace('__', ''))
        if normalized in body:
            supported.append(row['id'])
    return supported


def todo(kind, message, section, requirement_ids=(), *, content=True, delivery=True, origin='content', raw=''):
    identity = kind if kind == 'qualification_declaration' else kind + ':' + message
    return {'id': digest(identity)[:24], 'kind': kind, 'message': message,
            'section_ids': [section['id']], 'requirement_ids': list(requirement_ids),
            'evidence_ids': list(section.get('evidence_ids') or []), 'blocks_content': content,
            'blocks_delivery': delivery, 'origin': origin, 'raw': raw, 'status': 'open'}


def merge_todos(items, resolutions=None):
    result = {}
    for source in items:
        item = copy.deepcopy(source)
        key = item['id']
        if key in result:
            for field in ('section_ids', 'requirement_ids', 'evidence_ids'):
                result[key][field] = sorted(set(result[key].get(field, []) + item.get(field, [])))
        else:
            result[key] = item
    for item in result.values():
        record = (resolutions or {}).get(item['id'])
        if record and record.get('signature') == todo_signature(item):
            item['status'] = record['status']
            item['resolution'] = record
    return list(result.values())


def todo_signature(item):
    return digest({k: item.get(k) for k in ('kind', 'message', 'section_ids', 'requirement_ids', 'content_version')})


def inspect_section(section, p=None, requirements=None, findings=None, rules=None):
    with review_rules.scope(rules) as config:
        return _inspect_section(section, p, requirements, findings, config)


def _inspect_section(section, p=None, requirements=None, findings=None, rules=None):
    from . import workflow
    p = p or project(section['project_id'])
    requirements = requirements if requirements is not None else db.all('SELECT * FROM requirements WHERE project_id=?', (p['id'],))
    reqs = [r for r in requirements if r['id'] in section['requirement_ids']]
    text = section['content']
    split = bid_body.separate(text)
    items, blockers, reuse = separated_todos(section, split['items']), [], []
    config = review_rules.flags() if rules is None else rules
    structural_checks = []
    def issue(rule_id, message):
        active = review_rules.enabled(rule_id, config)
        structural_checks.append({'rule_id':rule_id, 'message':message, 'rule_enabled':active})
        if active:
            blockers.append(message)
    if split['removed']:
        issue('internal_wrapper', '正文仍含内部提示，请先保存草稿或预览移入内部待办')
    if not substantive(text):
        issue('empty_body', '正文为空或仅有标题、提示')
    citations = workflow._response_citation_state(text, section['evidence_ids'])
    if '_inspection_evidence' in p:
        evidence=[p['_inspection_evidence'][id] for id in dict.fromkeys(section['evidence_ids']) if id in p['_inspection_evidence']]
    else:evidence = workflow._valid_evidence(section['evidence_ids'], p['domain'], p['id'])
    if citations['format_error'] or citations['unregistered']:
        issue('section_citation_format', '正文引用格式错误或未登记到本章证据列表')
    from . import proposal_context
    for role_issue in proposal_context.procurement_claim_issues(text,evidence):
        issue('capability_mismatch',role_issue['reason']+'：'+role_issue['statement'])
    if len(evidence) != len(set(section['evidence_ids'])):
        issue('section_evidence_validity', '引用含未认可、过期、不适用或已变更的企业资料')
    qualification = any(r['category'] == 'qualification' for r in reqs)
    declaration_claims = [claim.strip() for line in text.splitlines() if not line.lstrip().startswith(('#', '>')) for claim in re.split(r'(?<=[。；])', line) if re.search(r'我方|本公司|本企业', claim)]
    if qualification and declaration_claims and not all(trusted_reuse(claim, evidence) for claim in declaration_claims):
        items.append(todo('qualification_declaration', DECLARATION_MESSAGE, section, [r['id'] for r in reqs if r['category'] == 'qualification'], content=True))
    if qualification and declaration_claims:
        items.append(todo('qualification_delivery', '按采购文件指定格式完成《资格审查资料承诺书》签章并装入交付文件；内容认可不代表已经签章或装入', section,
                          [r['id'] for r in reqs if r['category'] == 'qualification'], content=False))
    for match in PLACEHOLDER.finditer(text):
        raw = match.group()
        if any(raw in item['raw'] for item in split['items']):
            continue
        if raw == GENERIC_GAP:
            # Legacy broad fallback: recorded once, never invented as an attachment.
            if not qualification:
                items.append(todo('unresolved_legacy', '核对旧通用提示对应的具体缺项，未明确前不认定内容完整', section, section['requirement_ids'], raw=raw))
            continue
        delivery_only = bool(DELIVERY.search(raw)) and not FIELD.search(raw)
        if qualification and not FIELD.search(raw) and re.search(r'企业核对|核查结果|确认.*承诺|主体和关系说明', raw):
            # This is already represented by the procurement's declaration, not
            # an invented list of separate proofs for every prohibited condition.
            continue
        items.append(todo('delivery' if delivery_only else 'content_gap', raw[1:-1], section, section['requirement_ids'], content=not delivery_only, raw=raw))
    # A word in ordinary procurement prose ("材料待补充流程") is not a gap.
    # Fact-bearing statements must have their own support; another paragraph's
    # citation cannot approve an unrelated capability or another customer's fact.
    for line in text.splitlines():
        if line.lstrip().startswith(('#', '>')) or PLACEHOLDER.search(line):
            continue
        for claim in re.split(r'(?<=[。；])|\|', line):
            if not re.search(r'(?:我方|本公司|本系统|本产品|本方案|系统)(?:已|能够|可以|可|将)?(?:支持|具备|实现|拥有|提供)|其他客户专属|失败案例', claim):
                continue
            ids = trusted_reuse(claim.strip(), evidence)
            if ids:
                reuse.append({'text': claim.strip(), 'evidence_ids': ids})
            elif not re.search(r'采购要求|招标要求|须确认|待补充|尚未|不承诺', claim):
                items.append(todo('support_review', '核对具体陈述与企业来源是否一致：' + claim.strip(), section, section['requirement_ids'], raw=claim))
    if findings is None:
        review = db.one('SELECT * FROM reviews WHERE project_id=? AND status=? ORDER BY created_at DESC LIMIT 1', (p['id'], 'complete'))
        findings = review['findings'] if review else []
    for f in findings:
        if f.get('verdict') == 'contradiction' and (f.get('section_id') == section['id'] or f.get('requirement_id') in section['requirement_ids']):
            items.append(todo('content_conflict', f['reason'], section, [f['requirement_id']] if f.get('requirement_id') else section['requirement_ids'], origin='model_review'))
            items[-1]['rule_id'] = review_rules.model_finding_rule(f)
    # Unknown wrappers stay visible and cannot accidentally become approval.
    if cleanup_text(text)['uncertain']:
        items.append(todo('unresolved_wrapper', '仍有来源未确认的内部包装，请逐项核对预览中保留的文字', section, section['requirement_ids']))
    items = merge_todos(items)
    return {'section_id': section['id'], 'title': section['title'], 'status': section['status'], 'blockers': blockers, 'structural_checks':structural_checks, 'structural_risk':bool(blockers), 'todos': items, 'trusted_reuse': reuse}


def context(project_id):
    p = project(project_id)
    reqs = db.all('SELECT * FROM requirements WHERE project_id=? ORDER BY id', (project_id,))
    sections = db.all('SELECT * FROM sections WHERE project_id=? ORDER BY ordinal', (project_id,))
    from . import workflow, proposal_runtime
    if proposal_runtime.blueprint(p):
        reqs,_=proposal_runtime.project_responses(p,sections,reqs)
        review=db.one('SELECT * FROM reviews WHERE project_id=? AND status=? AND fingerprint=? ORDER BY created_at DESC LIMIT 1',
                      (project_id,'complete',workflow.review_fingerprint(project_id)))
    else:
        review = db.one('SELECT * FROM reviews WHERE project_id=? AND status=? ORDER BY created_at DESC LIMIT 1', (project_id, 'complete'))
    return p, reqs, sections, review['findings'] if review else []


def assessments(project_id, replacements=None, extra_items=(), threshold=None, rules=None):
    p, reqs, sections, findings = context(project_id)
    config = review_rules.flags() if rules is None else rules
    effective=[{**s, **(replacements or {}).get(s['id'], {})} for s in sections]
    from . import workflow
    ids=list(dict.fromkeys(id for section in effective for id in section['evidence_ids']))
    with review_rules.scope(config):
        p={**p,'_inspection_evidence':{e['id']:e for e in workflow._valid_evidence(ids,p['domain'],project_id)}}
    rows = [inspect_section(s,p,reqs,findings,rules=config) for s in effective]
    saved = (p.get('metadata') or {}).get('content_todos', [])
    all_items = merge_todos([t for row in rows for t in row['todos']] + saved + list(extra_items))
    for item in all_items:
        item['content_version'] = digest([(s['id'], (replacements or {}).get(s['id'], {}).get('content', s['content'])) for s in sections if s['id'] in item['section_ids']])
    all_items = merge_todos(all_items, (p.get('metadata') or {}).get('content_resolutions'))
    all_items = [review_rules.annotate(t, config) for t in all_items]
    threshold = approval_risk.policy() if threshold is None else threshold
    for row in rows:
        row['todos'] = [t for t in all_items if row['section_id'] in t['section_ids']]
        row['blockers'] += list(dict.fromkeys(t['message'] for t in row['todos'] if t['blocks_content'] and t['status'] != 'resolved' and t.get('rule_enabled', True)))
        row['disabled_checks'] = [c for c in row['structural_checks'] if not c['rule_enabled']] + [t for t in row['todos'] if not t['rule_enabled']]
        row['review_rules_revision'] = review_rules.revision(config)
        row['approval_threshold_enabled'] = review_rules.enabled('approval_threshold', config)
        row.update(approval_risk.evaluate(row, threshold))
    return rows, all_items


@review_rules.governed
def trust_preview(project_id):
    p = project(project_id)
    allowed = {d['id'] for d in trusted_documents(p)}
    docs = []
    for d in db.all('SELECT * FROM documents' + (' WHERE project_id IS NULL' if review_rules.active('knowledge_identity') else '') + ' ORDER BY name'):
        eligible = (not review_rules.active('knowledge_parse') or d['parse_status'] == 'ready') and (not review_rules.active('knowledge_expiry') or (d['status'] != 'expired' and (not d['valid_until'] or d['valid_until'] >= date.today().isoformat()))) and (not review_rules.active('knowledge_scope') or d['scope'] in ('general', p['domain'])) and source_allowed({'document_name': d['name'], 'document_metadata': d['metadata']})
        docs.append({'id': d['id'], 'name': d['name'], 'scope': d['scope'], 'status': d['status'], 'sha256': d['sha256'], 'source_updated_at': d['updated_at'], 'trusted': d['id'] in allowed, 'eligible': eligible, 'reason': '可选为本项目内容来源' if eligible else '来源范围、解析状态、有效期或内容类型不适用'})
    return {'project_id': project_id, 'documents': docs, 'token': digest([revision(project_id), 'trust'])}


@review_rules.governed
def set_trust(project_id, ids, token, confirmed):
    from . import workflow
    if not confirmed:
        raise ValueError('请确认本项目认可的企业资料范围')
    with workflow.editing(project_id, whole_workspace=True):
        plan = trust_preview(project_id)
        if review_rules.enabled('operation_revision', review_rules.flags()) and token != plan['token']:
            raise ValueError('资料或项目已变化，请重新选择')
        eligible = {d['id']: d for d in plan['documents'] if d['eligible']}
        if not set(ids) <= eligible.keys():
            raise ValueError('选择含不适用或非企业资料')
        p = project(project_id)
        meta = copy.deepcopy(p['metadata'])
        meta['trusted_sources'] = {id: {'sha256': eligible[id]['sha256'], 'source_updated_at': eligible[id]['source_updated_at'], 'confirmed_at': db.now()} for id in ids}
        if meta.get('generation_profile')=='technical_proposal':
            # A new project selection can add approved material without losing
            # an existing mixed-source paragraph limit or reference-only role.
            policy=meta.setdefault('proposal_source_policy',{'mode':'approved_facts_only','reference_document_ids':[]})
            policy['fact_document_ids']=list(ids)
        backup = _backup()
        db.update('projects', project_id, {'metadata': meta, 'updated_at': db.now()})
        # A trust change never marks any chapter or attachment approved.
        db.execute("UPDATE sections SET status='draft',updated_at=? WHERE project_id=?", (db.now(), project_id))
        workflow.review_project(project_id)
        return {'selected': len(ids), 'backup': backup, 'message': '已保存本项目资料范围，企业原资料状态未改变；附件和签章仍单独待办'}


def can_resolve(item):
    # A saved generation issue can be checked after editing. A placeholder
    # still present in the body independently remains a content blocker.
    return item['kind'] != 'unresolved_wrapper' and (item['kind'] != 'content_gap' or item['origin'] in ('generation', 'body_separation'))


def resolve_todo(project_id, todo_id, signature, note, confirmed):
    from . import workflow
    if not confirmed or len(note.strip()) < 10:
        raise ValueError('请明确确认，并填写至少10字的核对依据或实际交付记录')
    with workflow.editing(project_id, whole_workspace=True):
        _, items = assessments(project_id)
        item = next((i for i in items if i['id'] == todo_id), None)
        if not item or signature != todo_signature(item):
            raise ValueError('待办或正文已变化，请刷新后重新核对')
        if not can_resolve(item):
            raise ValueError('正文仍有具体占位或未知包装，请编辑正文后再批准，不能仅标记完成')
        p = project(project_id)
        meta = copy.deepcopy(p['metadata'])
        meta.setdefault('content_resolutions', {})[todo_id] = {'signature': signature, 'status': 'resolved', 'note': note.strip(), 'confirmed_at': db.now()}
        _backup()
        db.update('projects', project_id, {'metadata': meta, 'updated_at': db.now()})
        workflow.review_project(project_id)
        return {'message': '已记录本项人工核对；未自动批准章节或通过模型审计'}


def _top_gap_fragments(value):
    """Keep unexpected nonempty structures visible rather than losing gaps."""
    if value is None or value is False or value == '' or value == [] or value == {}:
        return []
    if isinstance(value, list):
        return [part for item in value for part in _top_gap_fragments(item)]
    if not isinstance(value, str):
        return [(json.dumps(value, ensure_ascii=False, sort_keys=True), True)]
    text = value.strip()
    if text.lower().strip('。.;；：: ') in {'', '无', '空', '暂无', '无缺项', '无待确认事项', '无待补充项', '未发现缺项', 'none', 'null', 'n/a'}:
        return []
    starts = list(re.finditer(r'(?m)(?:^\s*|(?<=[。；;])\s*)(?:\d{1,3}[.)、](?!\d)|[一二三四五六七八九十]+、|[-*+]\s)\s*', text))
    if not starts:
        return [(text, False)]
    parts = []
    prefix = text[:starts[0].start()].strip()
    if prefix and prefix not in {'以下为需项目确认的具体缺项，不逐段写入正文：', '以下为待确认事项：', '待确认事项：', '缺项说明：'}:
        parts.append((prefix, False))
    for index, match in enumerate(starts):
        end = starts[index+1].start() if index+1 < len(starts) else len(text)
        parts.append((text[match.start():end].strip(), False))
    return parts


def top_level_gap_notes(section, result, evidence=None):
    """Turn model top-level gap data into chapter-local work, never prose/facts.

    Without the original operation's evidence/requirement order, leave aliases
    unresolved and preserve its diagnostic pointer. Do not guess bindings.
    """
    if not isinstance(result, dict):
        return []
    requirements = section.get('requirements')
    rmap = {f'R{i:02d}': r['id'] for i,r in enumerate(requirements,1)} if isinstance(requirements,list) else {}
    rids = set(rmap.values()) | set(section.get('requirement_ids') or [])
    evidence = evidence or {}
    emap = {f'E{i:02d}': id for i,id in enumerate(sorted(evidence),1)}
    notes = []
    for original, malformed in _top_gap_fragments(result.get('gap_reason')):
        message = re.sub(r'^(?:[-*+]\s+|\d+[.)、](?!\d)\s*|[一二三四五六七八九十]+、\s*)', '', original.strip())
        if malformed:
            message = '模型顶层gap_reason结构非标准，需人工核对原始缺项数据：' + original
        required, cited, rbindings, ebindings, unknown = set(), set(), {}, {}, []
        for alias in re.findall(r'(?<![A-Za-z0-9])R\d+(?![A-Za-z0-9])', original, re.I):
            if alias in rmap:
                required.add(rmap[alias]);rbindings[alias]=rmap[alias]
            else:unknown.append({'type':'requirement','alias':alias})
        for id in re.findall(r'(?<![A-Za-z0-9])[a-f0-9]{32}(?![A-Za-z0-9])', original):
            if id in rids:required.add(id)
        refs = re.findall(r'\[E:([^\]]+)\]', original)
        refs.extend(re.findall(r'(?<![A-Za-z0-9])E\d+(?![A-Za-z0-9])', original))
        for alias in dict.fromkeys(refs):
            exact = alias[2:] if alias.startswith('E:') and alias[2:] in emap else alias
            id = emap.get(exact, exact if exact in evidence else None)
            if id:
                cited.add(id);ebindings[alias]={'id':id,'document_id':evidence[id].get('document_id'),
                    'reference_role':evidence[id].get('reference_role','enterprise_fact')}
            else:unknown.append({'type':'evidence','alias':alias})
        label = message.split('】',1)[0] if message.startswith('【') else ''
        explicit_delivery = bool(re.search(r'交付附件待办|附件待办|签章待办|签字盖章待办', label) or re.search(r'属于交付(?:附件|待办)[。.;；]*$', message))
        delivery_only = not malformed and (explicit_delivery or (bool(DELIVERY.search(message)) and not bid_body.FACT.search(message)
            and not re.search(r'数值|指标|策略|分歧|时限|条件|\d+(?:\.\d+)?\s*(?:%|年|小时|分钟|个月)', message)))
        notes.append({'kind':'delivery' if delivery_only else 'support_review','message':message,'raw':original,
            'origin':'generation_top_gap','top_level_field':'gap_reason','requirement_ids':sorted(required),
            'evidence_ids':sorted(cited),'blocks_content':not delivery_only,'blocks_delivery':True,
            'source_diagnostic':result.get('_request_diagnostic') or '',
            'gap_alias_bindings':{'requirements':rbindings,'evidence':ebindings},
            'unresolved_gap_aliases':list({(x['type'],x['alias']):x for x in unknown}.values()),
            'gap_structure_requires_review':malformed})
    # Stable raw spans prevent repeated normalization from appending duplicates.
    return list({(item['kind'],item['raw']):item for item in notes}.values())


def separated_todos(section, items):
    result = []
    for i in items:
        message = i['message'].strip() if i.get('origin')=='generation_top_gap' else re.sub(r'^(?:[-*+]\s+|\d+[.)、]\s*)', '', i['message'].strip())
        if message.startswith('【') and message.endswith('】'):
            message = message[1:-1]
        kind = 'body_editorial' if i['kind'] == 'editorial' else i['kind']
        is_top_gap = i.get('origin') == 'generation_top_gap'
        linked = i.get('requirement_ids',[]) if is_top_gap else i.get('requirement_ids') or section['requirement_ids']
        item = todo(kind, message, section, linked, content=i['blocks_content'], delivery=i['blocks_delivery'],
                    origin='generation_top_gap' if is_top_gap else 'body_separation', raw=i['raw'])
        if is_top_gap:
            item['id'] = digest(['generation_top_gap',section['id'],kind,message])[:24]
            for field in ('evidence_ids','source_diagnostic','gap_alias_bindings','unresolved_gap_aliases','gap_structure_requires_review','top_level_field'):
                item[field] = copy.deepcopy(i.get(field))
        result.append(item)
    return result


def record_generation(project_id, section_id, result):
    """Store explicit generation gaps outside delivery text, never add prose."""
    section = db.one('SELECT * FROM sections WHERE id=?', (section_id,))
    p = project(project_id)
    db.update('projects', project_id, {'metadata': generation_metadata(p, section, result)})


def generation_metadata(p, section, result):
    section_id = section['id']
    items = []
    explicit_notes=list(result.get('_internal_notes',[])) if p.get('metadata',{}).get('generation_profile')=='technical_proposal' else []
    if p.get('metadata',{}).get('generation_profile')=='technical_proposal':
        for note in top_level_gap_notes(section,result):
            if not any(i.get('origin')=='generation_top_gap' and i.get('kind')==note['kind'] and i.get('raw')==note['raw'] for i in explicit_notes):
                explicit_notes.append(note)
    for row in result.get('responses', []):
        if row.get('gap'):
            if explicit_notes and result.get('_typed_gap_notes_complete') is True:
                continue
            reason = str(row.get('gap_reason') or '核对本条陈述对应的直接来源，模型未给出明确依据')
            # The profile writer already supplies typed, scoped tasks for forms
            # and evidence gaps. Do not infer a second differently typed task
            # from the identical response reason.
            if any(str(note.get('message','')).strip()==reason.strip()
                   and row['requirement_id'] in (note.get('requirement_ids') or section['requirement_ids'])
                   for note in explicit_notes):
                continue
            req = db.one('SELECT category FROM requirements WHERE id=?', (row['requirement_id'],))
            if req and req['category'] == 'qualification':
                items.append(todo('qualification_declaration', DECLARATION_MESSAGE, section, [row['requirement_id']], origin='generation'))
                if FIELD.search(reason) or DELIVERY.search(reason):
                    delivery_only = bool(DELIVERY.search(reason)) and not FIELD.search(reason)
                    items.append(todo('delivery' if delivery_only else 'content_gap', reason, section, [row['requirement_id']], origin='generation', content=not delivery_only))
            else:
                delivery_only = bool(DELIVERY.search(reason)) and not FIELD.search(reason)
                items.append(todo('delivery' if delivery_only else 'support_review', reason, section, [row['requirement_id']], origin='generation', content=not delivery_only))
    meta = copy.deepcopy(p['metadata'])
    retained = []
    for t in meta.get('content_todos', []):
        if t.get('origin') == 'generation_top_gap' and t.get('kind') != 'delivery':
            other_sections=[id for id in t.get('section_ids',[]) if id!=section_id]
            if section_id not in t.get('section_ids',[]) or other_sections:
                retained.append({**t,'section_ids':other_sections} if section_id in t.get('section_ids',[]) else t)
            continue
        if t.get('origin') != 'generation' or t.get('kind') not in ('support_review', 'content_gap') or DELIVERY.search(t.get('message', '')):
            retained.append(t)
            continue
        other_sections = [id for id in t.get('section_ids', []) if id != section_id]
        if section_id not in t.get('section_ids', []) or other_sections:
            retained.append({**t, 'section_ids': other_sections} if section_id in t.get('section_ids', []) else t)
    merged = merge_todos(retained + items + separated_todos(section, explicit_notes if p.get('metadata',{}).get('generation_profile')=='technical_proposal' else result.get('_internal_notes', [])))
    previous = meta.get('content_todos', [])
    # Repeated saves of unchanged notes must not reorder the work list.
    if len(previous)==len(merged) and {t['id']:t for t in previous}=={t['id']:t for t in merged}:
        merged = copy.deepcopy(previous)
    meta['content_todos'] = merged
    from . import proposal_runtime
    return proposal_runtime.record_result(meta,section,result)


def approval(section, extra_items=()):
    rows, _ = assessments(section['project_id'], {section['id']: section}, extra_items)
    return next(row for row in rows if row['section_id'] == section['id'])


def revision(project_id, threshold=None, rules=None):
    p, reqs, sections, findings = context(project_id)
    docs = db.all('SELECT id,sha256,status,scope,valid_until,updated_at FROM documents ORDER BY id')
    return digest([POLICY_VERSION, review_rules.revision(rules), approval_risk.VERSION, approval_risk.policy() if threshold is None else threshold, p, reqs, sections, findings, docs])


def preview(project_id, ids, action='cleanup'):
    if action not in ('cleanup', 'approve', 'cleanup_and_approve', 'separate_notes'):
        raise ValueError('批量操作类型无效')
    if not ids or len(ids) != len(set(ids)) or len(ids) > 1000:
        raise ValueError('请选择不重复的章节，最多1000章')
    threshold = approval_risk.policy()
    config = review_rules.flags()
    initial_revision = revision(project_id, threshold, config)
    p, reqs, sections, _ = context(project_id)
    by_id = {s['id']: s for s in sections}
    if not set(ids) <= by_id.keys():
        raise ValueError('所选章节不属于当前项目')
    from . import compilation_outline
    if any(not compilation_outline.is_enabled(p, by_id[id]) for id in ids):
        raise ValueError('所选章节已移出当前编制目录，请刷新选择；如需处理，请先启用所属模块')
    replacements, changes, migrated = {}, [], []
    for id in ids:
        section = by_id[id]
        qualification = any(r['category'] == 'qualification' and r['id'] in section['requirement_ids'] for r in reqs)
        clean = (bid_body.separate(section['content']) if review_rules.enabled('body_separation', config) else {'content':section['content'],'items':[],'removed':[],'uncertain':[]}) if action == 'separate_notes' else cleanup_text(section['content'], qualification=qualification) if action != 'approve' else {'content': section['content'], 'removed': [], 'uncertain': []}
        if action == 'separate_notes':
            migrated.extend(separated_todos(section, clean['items']))
        if clean['removed']:
            replacements[id] = {'content': clean['content'], 'status': 'draft'}
        changes.append({'section_id': id, 'title': section['title'], 'before': section['content'], 'after': clean['content'],
                        'removed': clean['removed'], 'uncertain': clean['uncertain'],
                        'diff': ''.join(difflib.unified_diff(section['content'].splitlines(True), clean['content'].splitlines(True), fromfile='before', tofile='after'))})
    rows, todos = assessments(project_id, replacements, migrated, threshold=threshold, rules=config)
    chosen = [row for row in rows if row['section_id'] in ids]
    # Linked response cleanup is limited to requirements exclusively owned by the
    # selected chapters; shared/unselected content is not silently changed.
    selected_reqs = {r for id in ids for r in by_id[id]['requirement_ids']}
    outside = {r for s in sections if s['id'] not in ids for r in s['requirement_ids']}
    response_changes = []
    if action not in ('approve', 'separate_notes'):
        for r in reqs:
            if r['id'] in selected_reqs - outside:
                clean = cleanup_text(r['response'], qualification=r['category'] == 'qualification')
                if clean['removed']:
                    response_changes.append({'requirement_id': r['id'], 'before': r['response'], 'after': clean['content'], 'removed': clean['removed']})
    summary = {'selected': len(ids), 'affected': len(replacements), 'responses_affected': len(response_changes),
               'eligible': sum(row['eligible'] and row['status'] != 'approved' for row in chosen),
               'already_approved': sum(row['status'] == 'approved' for row in chosen),
               'risk_counts':{level:sum(row['risk_level']==level for row in chosen) for level in approval_risk.LEVELS},
               'blocked': sum(not row['eligible'] and row['status'] != 'approved' for row in chosen)}
    if review_rules.enabled('operation_revision', config) and revision(project_id) != initial_revision:
        raise ValueError('预览期间内容、来源或风险阈值发生变化，请重新预览')
    token = digest([initial_revision, ids, action])
    return {'project_id': project_id, 'action': action, 'section_ids': ids, 'token': token, 'summary': summary,
            'changes': changes, 'response_changes': response_changes, 'assessments': chosen,
            'approval_policy': {'threshold':threshold, 'label':approval_risk.LABELS[threshold]}, 'review_rules_revision':review_rules.revision(config),
            'todos': [t for t in todos if set(t['section_ids']) & set(ids)], 'migrated_todos': merge_todos(migrated), 'policy_version': POLICY_VERSION}


def _backup():
    folder = db.DATA / 'content-backups'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (db.uid() + '.sqlite3')
    with sqlite3.connect((db.DATA / 'bidding.sqlite3').as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(path) as dest:
        source.backup(dest)
        if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('备份校验失败，未修改业务内容')
    return str(path)


def _update(conn, table, id, values):
    allowed = {'sections', 'requirements', 'projects', 'project_snapshots'}
    if table not in allowed:
        raise ValueError('无效更新类型')
    values = {k: json.dumps(v, ensure_ascii=False) if k in db.JSON_FIELDS else v for k, v in values.items()}
    conn.execute('UPDATE ' + table + ' SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?', (*values.values(), id))


def execute(project_id, ids, action, token, confirmed):
    from . import workflow
    if not confirmed:
        raise ValueError('请先查看预览并明确确认本次范围')
    # Whole-workspace reservation prevents a concurrent source edit during the
    # consistent backup; only this project's selected rows are changed.
    with workflow.editing(project_id, whole_workspace=True):
        plan = preview(project_id, ids, action)
        if review_rules.enabled('operation_revision', review_rules.flags()) and token != plan['token']:
            raise ValueError('内容或来源已变化，请重新预览后确认')
        p, reqs, sections, _ = context(project_id)
        by_id = {s['id']: s for s in sections}
        results, section_updates, req_updates = [], {}, {}
        assessments_by_id = {r['section_id']: r for r in plan['assessments']}
        for change in plan['changes']:
            id = change['section_id']
            row = assessments_by_id[id]
            values = {}
            if change['removed']:
                values.update(content=change['after'], status='draft', user_edited=1)
                if action == 'separate_notes':
                    values['evidence_ids'] = workflow._response_citation_state(change['after'], by_id[id]['evidence_ids'])['visible']
            outcome = 'success' if values else 'skipped'
            reason = ('内部提示已转入待办，正文保留为草稿' if action == 'separate_notes' else '已清理固定包装') if values else '没有需要移出的提示'
            if action in ('approve', 'cleanup_and_approve'):
                if row['status'] == 'approved' and not values:
                    reason = '已批准，跳过'
                elif row['eligible']:
                    values['status'] = 'approved'
                    outcome, reason = 'success', '已按' + row['approval_threshold_label'] + '阈值人工批准正文；' + ('接受的风险及' if row['accepted_risk'] else '') + '附件/签章待办继续保留'
                else:
                    reason = row['approval_reason']
                    outcome = 'success' if values else 'skipped'
            if values:
                section_updates[id] = {**values, 'updated_at': db.now()}
            results.append({'section_id': id, 'title': change['title'], 'result': outcome, 'reason': reason,
                            'cleaned': bool(change['removed']), 'approved': values.get('status') == 'approved'})
        for change in plan['response_changes']:
            req_updates[change['requirement_id']] = {'response': change['after'], 'status': 'drafted', 'updated_at': db.now()}
        if not section_updates and not req_updates:
            return {'results': results, 'snapshot_id': None, 'changed': 0, 'approved': 0, 'skipped': len(results), 'failed': 0}
        backup = _backup()
        snapshot_id = db.uid()
        payload = {'kind': 'content_batch', 'action': action, 'backup': backup, 'before_project': p,
                   'before_sections': [s for s in sections if s['id'] in section_updates],
                   'before_requirements': [r for r in reqs if r['id'] in req_updates],
                   'before_checks': db.all('SELECT * FROM checks WHERE project_id=?', (project_id,)),
                   'after_sections': {id: {**by_id[id], **values} for id, values in section_updates.items()},
                   'after_requirements': {r['id']: {**r, **req_updates[r['id']]} for r in reqs if r['id'] in req_updates},
                   'preview_token': token, 'undone': False}
        meta = copy.deepcopy(p.get('metadata') or {})
        # Keep the source of removed wrappers, but do not turn the old vague
        # fallback into a new demand for nonexistent procurement attachments.
        meta['content_cleanup_log'] = meta.get('content_cleanup_log', []) + [{'snapshot_id': snapshot_id, 'section_ids': list(section_updates), 'policy': POLICY_VERSION}]
        if action == 'separate_notes':
            meta['content_todos'] = merge_todos(meta.get('content_todos', []) + plan['migrated_todos'])
        for id, values in section_updates.items():
            if values.get('status') == 'approved':
                meta.setdefault('section_approvals', {})[id] = approval_risk.record({**by_id[id], **values}, assessments_by_id[id])
        payload['after_metadata'] = meta
        with db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if review_rules.enabled('operation_revision', review_rules.flags()) and token != digest([revision(project_id), ids, action]):
                raise ValueError('备份期间内容或来源已变化，未应用清理；请重新预览')
            for id, values in section_updates.items():
                _update(conn, 'sections', id, values)
            for id, values in req_updates.items():
                _update(conn, 'requirements', id, values)
            _update(conn, 'projects', project_id, {'metadata': meta, 'status': 'review', 'updated_at': db.now()})
            conn.execute('INSERT INTO project_snapshots(id,project_id,document_id,label,payload,created_at) VALUES(?,?,NULL,?,?,?)',
                         (snapshot_id, project_id, '正文批量操作：' + action, json.dumps(payload, ensure_ascii=False), db.now()))
        # Refresh derived checks while keeping prior checks in the snapshot and
        # the full independent model audit in reviews unchanged.
        workflow.review_project(project_id)
        # Include this operation's derived project metadata (e.g. parsed basics)
        # in the conflict baseline; otherwise our own review breaks undo.
        payload['after_metadata'] = project(project_id)['metadata']
        db.update('project_snapshots', snapshot_id, {'payload':payload})
        return {'results': results, 'snapshot_id': snapshot_id, 'backup': backup,
                'changed': sum(r['cleaned'] for r in results), 'approved': sum(r['approved'] for r in results),
                'skipped': sum(r['result'] == 'skipped' for r in results), 'failed': 0}


def undo(snapshot_id, confirmed=False):
    from . import workflow
    snapshot = db.one('SELECT * FROM project_snapshots WHERE id=?', (snapshot_id,))
    if not snapshot or snapshot['payload'].get('kind') != 'content_batch':
        raise ValueError('不是正文清理快照')
    if not confirmed:
        raise ValueError('撤销需要明确确认')
    p_id = snapshot['project_id']
    with workflow.editing(p_id, whole_workspace=True):
        payload = db.one('SELECT * FROM project_snapshots WHERE id=?', (snapshot_id,))['payload']
        if payload['undone']:
            return {'message': '本轮操作已撤销，无额外修改'}
        for table, field in ([('sections', 'after_sections'), ('requirements', 'after_requirements')] if review_rules.enabled('restore_conflict', review_rules.flags()) else []):
            for id, expected in payload[field].items():
                current = db.one('SELECT * FROM ' + table + ' WHERE id=?', (id,))
                from .chapter_outline import snapshot_matches
                if not snapshot_matches(current, expected):
                    raise ValueError('操作后内容或批准状态已有变化，不能覆盖；请先核对差异')
        if review_rules.enabled('restore_conflict', review_rules.flags()) and project(p_id)['metadata'] != payload['after_metadata']:
            raise ValueError('项目待办或信任设置已有变化，不能直接覆盖撤销')
        _backup()
        with db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            for table, field in [('sections', 'before_sections'), ('requirements', 'before_requirements')]:
                for row in payload[field]:
                    values={k:v for k,v in row.items() if k!='id'}
                    if table=='sections':
                        from .chapter_outline import preserve_directory_on_restore
                        current=db.decode(conn.execute('SELECT * FROM sections WHERE id=?',(row['id'],)).fetchone())
                        values=preserve_directory_on_restore(current,values)
                    _update(conn, table, row['id'], values)
            _update(conn, 'projects', p_id, {'metadata': payload['before_project']['metadata'], 'status': payload['before_project']['status'], 'updated_at': db.now()})
            payload['undone'] = True
            _update(conn, 'project_snapshots', snapshot_id, {'payload': payload})
        workflow.review_project(p_id)
        return {'message': '本轮章节、响应与批准状态已恢复；历史审计保留', 'sections': len(payload['before_sections'])}
