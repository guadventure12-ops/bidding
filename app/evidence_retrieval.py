"""Local lexical evidence retrieval. Relevance is never a support verdict.

The caller must pass only approved, current, in-scope documents. Curated-extract
handling additionally requires an explicit document metadata marker; names and
text alone cannot opt a document into filtering. No database or model calls.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
import json
import hashlib
import math
import re
from typing import Any

from . import review_rules


# These very common query words should not make an unrelated passage evidence.
# Keep meaningful words, technical identifiers and numeric tokens unchanged.
_QUERY_STOP = frozenset({'系统', '支持', '要求', '提供', '采用', '功能', '能力',
                         '进行', '相关', '包括', '实现', '根据', '是否', '方案'})
_EXTRACT_SEPARATOR = '\n原文：\n'
PROPOSAL_SOURCE_VERSION = 'proposal-source-1'
_FAILURE_SOURCE_KINDS = frozenset({'failure_case', 'postmortem', 'failure_review', 'project_postmortem'})


def _product_failure_reason(text, match):
    """Recognize an operational error-detail feature, without editing source text."""
    start = max((text.rfind(mark, 0, match.start()) for mark in ('。', '！', '？', '\n')), default=-1) + 1
    ends = [text.find(mark, match.end()) for mark in ('。', '！', '？', '\n')]
    end = min((pos for pos in ends if pos >= 0), default=len(text))
    sentence = text[start:end]
    if re.search(r'(?:项目|实施|上线|交付|投标|中标|成交)(?:的)?失败|失败案例|项目复盘|事故复盘|复盘报告|客户项目失败', sentence):
        return False
    return bool(re.search(r'导入|导出|同步|上传|下载|校验|检测|接口|登录|解析|备份|恢复|归档|请求|调用', sentence)
                and re.search(r'系统|平台|产品|模块|功能|管理员|用户|界面|日志|异常|任务', sentence)
                and re.search(r'提示|显示|展示|记录|查询|查看|定位|排查|返回|反馈', sentence))


def _proposal_statement_allowed(row):
    """Retain origin exclusions; disambiguate only the overloaded 失败原因 token."""
    from . import content_review
    meta = _object(row.get('document_metadata'))
    if (meta.get('source_kind') in _FAILURE_SOURCE_KINDS
            or any(meta.get(k) for k in ('failure_case', 'postmortem', 'failure_review'))):
        return False
    if content_review.source_allowed(row):
        return True
    if any(meta.get(k) for k in ('ai_generated', 'other_customer_only', 'conflicts_with_project', 'failure_case')):
        return False
    text = str(row.get('text') or '')
    matches = list(content_review.EXCLUDED_SOURCE.finditer(text))
    # No substitution of words: all other matched exclusion categories remain
    # blocking, as does a failure reason without explicit product-feature context.
    return bool(matches) and all(m.group(0) == '失败原因' and _product_failure_reason(text, m) for m in matches)


def _ids(value):
    """An explicit set, never an open-ended selection or a string iterator."""
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
        return None
    return set(value)


def _sha(text):
    return hashlib.sha256(str(text or '').encode('utf-8')).hexdigest()


def _source_subset(body, original):
    # Approval may omit navigation/link lines, but may not add source facts.
    # Keep order; testing each line as an unordered substring would allow a
    # fabricated reordering of a statement and its qualification.
    offset = 0
    for line in body.splitlines():
        if not line.strip():
            continue
        found = original.find(line, offset)
        if found < 0:
            return False
        offset = found + len(line)
    return True


def filter_proposal_rows(rows, project, document_metadata=None):
    """Pure candidate boundary for an explicitly selected proposal profile.

    Returns ``(rows, diagnostics)``. For every other profile rows pass through
    unchanged. No database, model calls or global review-setting writes occur.

    The SQL caller supplies chunk fields id/document_id/text/kind/locator and
    document fields document_name, source_type, document_project_id,
    document_status (or status), scope, valid_until, parse_status,
    document_sha256, document_updated_at, document_metadata. Missing identity,
    approval, parsing or hash fields fail closed only in this profile.

    Policy document IDs and optional per-document chunk IDs are intersections,
    also intersected with an existing version-bound trusted_sources selection.
    The result's text is the exact readable approved range; the index must not
    re-expand it when ordinary review rules are disabled. The private boundary
    marker detects any subsequent change to that selected text.
    """
    from . import knowledge_review, content_review

    rows = [dict(row) for row in rows]
    meta = _object((project or {}).get('metadata'))
    strict = meta.get('generation_profile') == 'technical_proposal'
    diagnostic = {'version': PROPOSAL_SOURCE_VERSION, 'active': strict,
                  'input_rows': len(rows), 'accepted_rows': 0, 'rejected_rows': 0,
                  'reason_counts': {}, 'items': []}
    if not strict:
        diagnostic['accepted_rows'] = len(rows)
        return rows, diagnostic

    policy = _object(meta.get('proposal_source_policy'))
    facts = _ids(policy.get('fact_document_ids'))
    references = _ids(policy.get('reference_document_ids', []))
    limits = policy.get('fact_chunk_ids_by_document', {})
    trusted = meta.get('trusted_sources')
    invalid = (policy.get('mode') != 'approved_facts_only' or facts is None or references is None
               or not isinstance(limits, dict) or any(not isinstance(k, str) or _ids(v) is None for k, v in limits.items())
               or ('trusted_sources' in meta and not isinstance(trusted, dict)))

    def reject(row, code, reason):
        diagnostic['items'].append({'id': row.get('id'), 'document_id': row.get('document_id'),
                                    'code': code, 'reason': reason})
        diagnostic['reason_counts'][code] = diagnostic['reason_counts'].get(code, 0) + 1
        diagnostic['rejected_rows'] += 1

    accepted = []
    # These are input scope conditions of the selected profile, not edits to
    # the user's 75 review switches. Existing helpers share their semantics.
    strict_rules = {key: True for key in ('knowledge_identity', 'knowledge_parse', 'knowledge_expiry',
                                        'knowledge_scope', 'knowledge_origin', 'knowledge_version', 'knowledge_body')}
    with review_rules.scope(strict_rules):
        for row in rows:
            doc_id, chunk_id = row.get('document_id'), row.get('id')
            if invalid:
                reject(row, 'invalid_policy', '技术方案资料策略缺失或不是明确的资料/摘录ID集合')
                continue
            if not doc_id or not chunk_id or not isinstance(row.get('text'), str):
                reject(row, 'invalid_row', '资料或摘录身份、已读取正文缺失')
                continue
            if doc_id in references:
                reject(row, 'reference_only', '本次明确指定为结构参考，不作为企业事实')
                continue
            if doc_id not in facts:
                reject(row, 'not_selected', '不在本次明确选定的事实资料范围内')
                continue
            if doc_id in limits and chunk_id not in _ids(limits[doc_id]):
                reject(row, 'outside_chunk_selection', '不在本次核对的混合资料正文范围内')
                continue
            if row.get('source_type') != 'knowledge' or 'document_project_id' not in row or row['document_project_id'] not in (None, ''):
                reject(row, 'not_knowledge', '采购文件及项目生成材料不能作为企业事实资料')
                continue
            if row.get('document_status', row.get('status')) != 'approved':
                reject(row, 'not_approved', '企业资料尚未批准，项目选择不能替代资料批准')
                continue
            if row.get('parse_status') != 'ready':
                reject(row, 'not_ready', '资料解析未完成或失败')
                continue
            if row.get('scope') not in ('general', (project or {}).get('domain')) or row.get('scope') not in ('general', 'archive', 'expense'):
                reject(row, 'wrong_scope', '产品用途不适用本项目')
                continue
            expiry = row.get('valid_until')
            if expiry:
                try:
                    expires = date.fromisoformat(expiry)
                except (TypeError, ValueError):
                    reject(row, 'invalid_expiry', '已有有效期格式无效，需要核对，不自动续期')
                    continue
                if expires < date.today():
                    reject(row, 'expired', '资料已明确过期，不自动续期')
                    continue
            source_sha = row.get('document_sha256')
            if not isinstance(source_sha, str) or not source_sha:
                reject(row, 'missing_source_hash', '缺少原始资料版本指纹')
                continue
            if 'trusted_sources' in meta:
                saved = trusted.get(doc_id)
                if not isinstance(saved, dict) or saved.get('sha256') != source_sha or not saved.get('source_updated_at') or saved.get('source_updated_at') != row.get('document_updated_at'):
                    reject(row, 'project_trust_changed', '不在本项目当前认可版本范围，不能以本次集合扩大范围')
                    continue
            supplied = (document_metadata or {}).get(doc_id)
            docmeta = _object(supplied if supplied is not None else row.get('document_metadata'))
            chunkmeta = _object(row.get('metadata'))
            # File names are retained provenance, not a verdict. An approved
            # historical bid can supply explicitly selected product paragraphs.
            origin_row = {'document_name': '', 'document_metadata': docmeta}
            if (not knowledge_review.allowed_for_project(origin_row, (project or {}).get('id'))
                    or not knowledge_review.original_source('', chunkmeta)):
                reject(row, 'excluded_origin_or_project', '明确标记的生成稿、客户专属、冲突、失败来源或其他项目范围不适用')
                continue
            if docmeta.get('source_kind') in ('customer_case', 'case_study') or chunkmeta.get('source_kind') in ('customer_case', 'case_study'):
                reject(row, 'customer_case', '客户案例事实不能作为通用产品事实')
                continue

            original = row['text']
            raw_sha = _sha(original)
            recognition = docmeta.get('knowledge_approval')
            entry = None
            if 'knowledge_approval' in docmeta:
                if (not isinstance(recognition, dict) or not isinstance(recognition.get('entries'), list)
                        or (recognition.get('source_sha256') and recognition['source_sha256'] != source_sha)):
                    reject(row, 'approval_version_changed', '原始资料与认可版本不一致或认可记录无效')
                    continue
                entry = next((r for r in recognition['entries'] if isinstance(r, dict) and r.get('id') == chunk_id
                              and r.get('sha256') == raw_sha and isinstance(r.get('text'), str)), None)
                if not entry:
                    reject(row, 'outside_approved_range', '摘录未获认可或其正文已变化')
                    continue
                body = entry['text']
                if not _source_subset(body, original):
                    reject(row, 'approval_not_source_text', '认可文本不能在实际读取原文中定位，未纳入事实')
                    continue
            elif docmeta.get('evidence_kind') == 'curated_extract':
                if _EXTRACT_SEPARATOR not in original:
                    reject(row, 'outside_approved_range', '原有摘录资料的正文标记缺失')
                    continue
                body = original.split(_EXTRACT_SEPARATOR, 1)[1].strip()
            else:
                # Legacy approved documents without a separate range record
                # used approval of the parsed source itself. Never add content.
                body = original

            if docmeta.get('evidence_kind') == 'curated_extract':
                audited = docmeta.get('audited_excerpts')
                if isinstance(audited, list):
                    excerpt = next((r for r in audited if isinstance(r, dict) and str(r.get('quote', '')).strip() == body.strip()
                                    and r.get('sha256') == _sha(r.get('quote', ''))), None)
                    if not excerpt:
                        reject(row, 'outside_curated_range', '不在原有核对摘录范围，不能扩大为全文')
                        continue
                    row['source_excerpt'] = {k: excerpt[k] for k in ('source_block_id', 'locator', 'start_offset', 'length', 'sha256') if k in excerpt}
                row['evidence_kind'] = 'curated_extract'
            body = knowledge_review.readable_text(body, row.get('kind', 'paragraph'))
            if not body:
                reject(row, 'no_readable_body', '没有实际可用正文，未获取的链接附件不在范围内')
                continue
            if (not _proposal_statement_allowed({**origin_row, 'text': body})
                    or not _proposal_statement_allowed({'document_name': '', 'document_metadata': chunkmeta, 'text': body})):
                reject(row, 'excluded_statement', '正文含明确采购要求、客户专属、失败或冲突内容，不能作为产品事实')
                continue
            scope = (recognition or {}).get('recognized_scope') or ('原有部分摘录认可范围' if row.get('evidence_kind') == 'curated_extract' else '原有已批准的当前解析正文')
            constraints = {k: docmeta[k] for k in ('source_document_id', 'source_url', 'source_date', 'limitations',
                         'scope_limit', 'authorization_scope', 'product_version', 'version', 'project_ids',
                         'applicable_projects', 'applicability') if docmeta.get(k)}
            row.update(text=body, support_text=body, document_metadata=docmeta)
            row['source_constraints'] = {**constraints, 'document_name': row.get('document_name', ''),
                                        'recognized_scope': scope, 'source_notices': (recognition or {}).get('source_notices', []),
                                        'document_sha256': source_sha, 'source_text_sha256': raw_sha,
                                        'attachment_note': '仅包含当前实际读取且获准正文，不代表附件或签章已完成'}
            row['_proposal_fact_boundary'] = {'version': PROPOSAL_SOURCE_VERSION, 'document_id': doc_id,
                                              'source_text_sha256': raw_sha, 'recognized_text_sha256': _sha(body)}
            accepted.append(row)
    diagnostic['accepted_rows'] = len(accepted)
    return accepted, diagnostic


def tokens(text: str) -> list[str]:
    text = str(text).lower()
    result = re.findall(r'[a-z][a-z0-9_.-]*|\d+(?:\.\d+)?', text)
    for segment in re.findall(r'[\u4e00-\u9fff]+', text):
        result.extend(segment[i:i + 2] for i in range(len(segment) - 1))
        if len(segment) == 1:
            result.append(segment)
    return result


def _object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except (TypeError, ValueError):
            pass
    return {}


@dataclass
class EvidenceIndex:
    rows: list[dict]
    scoring_texts: list[str]
    lengths: list[int]
    average_length: float
    postings: dict
    filtered_count: int = 0


def build_index(rows, document_metadata: dict | None = None) -> EvidenceIndex:
    """Build once inside the caller's knowledge-fingerprint cache.

    rows can include ``document_metadata`` from a SQL join, or the caller can
    supply a map keyed by document ID. The documented marker is
    ``evidence_kind: curated_extract``. Return text retains original provenance;
    only the scoring text drops curated headers. Limits remain structured data.
    """
    accepted, scoring, filtered = [], [], 0
    for original in rows:
        row = dict(original)
        if '_proposal_fact_boundary' in row:
            boundary = row['_proposal_fact_boundary']
            body = str(row.get('text') or '')
            if (not isinstance(boundary, dict) or boundary.get('version') != PROPOSAL_SOURCE_VERSION
                    or boundary.get('document_id') != row.get('document_id')
                    or boundary.get('recognized_text_sha256') != _sha(body) or not body.strip()):
                filtered += 1
                continue
            # The profile has already checked source identity and the original
            # chunk hash. Reapplying range checks to the narrowed text would
            # either reject it or expand it when ordinary review switches are off.
            row['support_text'] = body
            accepted.append(row)
            scoring.append(body)
            continue
        supplied = (document_metadata or {}).get(row.get('document_id'))
        metadata = _object(supplied if supplied is not None else row.get('document_metadata'))
        recognition = metadata.get('knowledge_approval')
        recognized = None
        version_check = review_rules.active('knowledge_version')
        if recognition and version_check:
            recognized = next((e for e in recognition.get('entries', []) if e['id'] == row.get('id')
                               and e['sha256'] == hashlib.sha256(str(row.get('text') or '').encode()).hexdigest()), None)
            if not recognized:
                filtered += 1
                continue
        status = row.get('document_status', row.get('status'))
        curated = version_check and status == 'approved' and metadata.get('evidence_kind') == 'curated_extract'
        text = str(row.get('text') or '')
        if curated:
            if _EXTRACT_SEPARATOR not in text:
                filtered += 1
                continue
            body = text.split(_EXTRACT_SEPARATOR, 1)[1].strip()
            if not body:
                filtered += 1
                continue
            audited = metadata.get('audited_excerpts')
            if isinstance(audited, list):
                verified = next((item for item in audited if isinstance(item, dict)
                    and str(item.get('quote', '')).strip() == body
                    and item.get('sha256') == hashlib.sha256(str(item.get('quote', '')).encode('utf-8')).hexdigest()), None)
                if verified is None:
                    filtered += 1
                    continue
                row['source_excerpt'] = {key: verified[key] for key in
                    ('source_block_id', 'locator', 'start_offset', 'length', 'sha256') if key in verified}
            row['evidence_kind'] = 'curated_extract'
            row['source_constraints'] = {
                key: metadata[key] for key in ('source_document_id', 'source_url',
                    'source_date', 'limitations', 'scope_limit', 'authorization_scope') if metadata.get(key)
            }
        else:
            body = text
            if not version_check:
                # Range checks may be disabled, but only already parsed source
                # text is available. Never substitute the content of a link.
                from .knowledge_review import readable_text
                body = readable_text(text, row.get('kind', 'text'))
                row['text'] = body
        if recognized:
            body = recognized['text']
            row['text'] = body  # Every consumer, not only scoring, sees approved text.
        constraints = {key: metadata[key] for key in ('source_document_id', 'source_url', 'source_date',
            'limitations', 'scope_limit', 'authorization_scope', 'product_version', 'version',
            'project_ids', 'applicable_projects', 'applicability') if metadata.get(key)}
        if constraints or recognition:
            row['source_constraints'] = {**constraints, 'document_name': row.get('document_name', ''),
                                         'recognized_scope': (recognition or {}).get('recognized_scope', '原有认可范围'),
                                         'source_notices': (recognition or {}).get('source_notices', [])}
        if not version_check:
            row.setdefault('source_constraints', {}).update(
                recognized_scope='版本及局部认可范围审核已关闭：使用当前已读取文本',
                attachment_note='不包含未获取的附件正文')
        if not body.strip():
            continue
        # Fact validation uses exact source content, excluding source IDs,
        # locators, dates and other provenance wrapper text.
        row['support_text'] = body
        accepted.append(row)
        scoring.append(body)
    counters = [Counter(tokens(text)) for text in scoring]
    lengths = [sum(counter.values()) for counter in counters]
    average = sum(lengths) / max(1, len(lengths)) or 1.0
    postings = defaultdict(list)
    for index, counter in enumerate(counters):
        for term, frequency in counter.items():
            postings[term].append((index, frequency))
    return EvidenceIndex(accepted, scoring, lengths, average, dict(postings), filtered)


def split_query(query: str, max_facets: int = 12) -> list[str]:
    """Bounded literal clauses; no invented synonyms or claims."""
    query = str(query).strip()[:3000]
    # Decimal points and technical names are not split. Commas divide long
    # clauses only, so short noun phrases retain their context.
    primary = re.split(r'[；;。\n]+', query)
    facets = []
    for clause in primary:
        clause = re.sub(r'^\s*(?:[（(]?\d+[）).、]|[一二三四五六七八九十]+、)\s*', '', clause).strip()
        parts = re.split(r'[，,]', clause) if len(clause) > 45 else [clause]
        for part in parts:
            part = part.strip()
            if len(part) >= 4 and part not in facets:
                facets.append(part)
    if len(facets) > max_facets:
        # Sample the entire requirement, including its tail, rather than only
        # considering the first numbered points of a large scoring criterion.
        indices = sorted({round(i * (len(facets) - 1) / (max_facets - 1)) for i in range(max_facets)}) if max_facets > 1 else [0]
        facets = [facets[i] for i in indices]
    return facets


def _rank(index: EvidenceIndex, query: str) -> list[tuple[int, float]]:
    query_tokens = set(tokens(query)) - _QUERY_STOP
    scores = defaultdict(float)
    count = len(index.rows)
    for term in query_tokens:
        matches = index.postings.get(term, [])
        idf = math.log(1 + (count - len(matches) + .5) / (len(matches) + .5))
        for position, frequency in matches:
            scores[position] += idf * frequency * 2.2 / (
                frequency + 1.2 * (.25 + .75 * index.lengths[position] / index.average_length))
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def search(index: EvidenceIndex, query: str, limit: int = 6,
           diversify: bool = True, max_text_chars: int = 18000) -> list[dict]:
    """Return traceable lexical candidates, never ``supported=True``.

    In generation mode, reserve the first result for the entire requirement,
    then take each literal clause's best new result before falling back to the
    overall ranking. This limits one topic crowding out other clauses. The
    result count and original-text budget are bounded; no text is truncated.
    """
    if not str(query).strip() or limit <= 0 or max_text_chars <= 0:
        return []
    limit = min(int(limit), 30)
    full = _rank(index, str(query)[:3000])
    if not full:
        return []
    full_scores = dict(full)
    facets = split_query(query) if diversify else []
    facet_rankings = [(facet, _rank(index, facet)) for facet in facets]
    order = [full[0][0]]
    matched = defaultdict(list)
    for facet, ranking in facet_rankings:
        if ranking:
            matched[ranking[0][0]].append(facet)
            if ranking[0][0] not in order:
                order.append(ranking[0][0])
    order.extend(position for position, _ in full if position not in order)
    result, consumed = [], 0
    for position in order:
        row = index.rows[position]
        size = len(str(row.get('text') or ''))
        if consumed + size > max_text_chars:
            continue
        result.append({**row, 'score': round(full_scores.get(position, 0.0), 4),
                       'retrieval_facets': matched.get(position, []),
                       'retrieval_method': 'lexical_clause_diversity' if diversify else 'lexical_bm25'})
        consumed += size
        if len(result) >= limit:
            break
    return result
