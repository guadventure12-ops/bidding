from app.evidence_retrieval import build_index, search, split_query
import hashlib


def row(id, text, document='doc', approved=True):
    return {'id': id, 'document_id': document, 'document_status': 'approved' if approved else 'pending', 'text': text}


def test_only_explicit_approved_curated_metadata_filters_headers():
    rows = [row('header', '适用限制：XBRL尚待确认'), row('quote', '## 摘录\n原文：\n提供四性检测。')]
    ordinary = build_index(rows)
    assert search(ordinary, 'XBRL')[0]['id'] == 'header'
    curated = build_index(rows, {'doc': {'evidence_kind': 'curated_extract', 'limitations': '只能作为草稿'}})
    assert curated.filtered_count == 1
    assert search(curated, 'XBRL') == []
    hits = search(curated, '四性检测')
    assert hits[0]['source_constraints']['limitations'] == '只能作为草稿'
    assert hits[0]['text'] == rows[1]['text']
    assert hits[0]['support_text'] == '提供四性检测。'
    assert 'supported' not in hits[0]
    # A misleading filename/text or an unapproved source cannot opt into it.
    pending = build_index([row('p', '适用限制：XBRL', approved=False)], {'doc': {'evidence_kind': 'curated_extract'}})
    assert pending.filtered_count == 0


def test_curated_provenance_is_not_scored_as_product_capability():
    source = row('a', '原文件：Oracle安全认证资料；来源编号200\n原文：\n数据库采用MongoDB副本集。')
    index = build_index([source], {'doc': {'evidence_kind': 'curated_extract'}})
    assert search(index, 'Oracle') == []
    assert search(index, '200') == []
    assert search(index, 'MongoDB')[0]['text'] == source['text']
    assert search(index, 'MongoDB')[0]['support_text'] == '数据库采用MongoDB副本集。'


def test_audited_quote_allowlist_excludes_unreviewed_additions():
    quote = '数据库采用MongoDB副本集。'
    metadata = {'evidence_kind': 'curated_extract', 'authorization_scope': '仅本次草稿',
                'audited_excerpts': [{'quote': quote, 'source_block_id': 'original', 'locator': '原文段落',
                                      'sha256': hashlib.sha256(quote.encode()).hexdigest()}]}
    index = build_index([row('good', '原文来源\n原文：\n'+quote),
                         row('bad', '原文来源\n原文：\n支持Oracle兼容认证')], {'doc': metadata})
    assert index.filtered_count == 1
    assert search(index, 'Oracle') == []
    hit = search(index, 'MongoDB')[0]
    assert hit['source_excerpt']['source_block_id'] == 'original'
    assert hit['source_constraints']['authorization_scope'] == '仅本次草稿'


def test_compound_query_preserves_secondary_clause_and_bounded_budget():
    rows = [row('db1', '数据库MySQL数据库高可用数据库备份'), row('db2', '数据库MySQL高可用'),
            row('db3', '数据库高可用MySQL'), row('rule', '凭证摘要条件组合完整性校验'),
            row('integration', 'DEX中间件推送上游系统接口数据')]
    index = build_index(rows)
    query = '数据库MySQL高可用数据库备份；凭证摘要条件组合完整性校验；DEX中间件推送上游系统接口数据'
    hits = search(index, query, limit=3)
    assert {h['id'] for h in hits} == {'db1', 'rule', 'integration'}
    assert len({h['id'] for h in hits}) == len(hits)
    assert all(h['retrieval_method'] == 'lexical_clause_diversity' for h in hits)
    short = search(index, query, limit=6, max_text_chars=30)
    assert sum(len(h['text']) for h in short) <= 30


def test_different_metadata_formats_and_empty_inputs():
    source = row('a', '原文来源\n原文：\n四性检测')
    source['document_metadata'] = '{"evidence_kind":"curated_extract","source_date":"2025-09-01"}'
    index = build_index([source])
    assert search(index, '四性')[0]['source_constraints']['source_date'] == '2025-09-01'
    assert search(index, '') == []
    assert search(index, '四性', limit=0) == []
    assert search(build_index([]), '四性') == []


def test_facets_keep_decimal_and_tail_of_long_requirement():
    clauses = [f'条目{i}需要字段{i}相关内容' for i in range(20)]
    facets = split_query('；'.join(clauses), max_facets=4)
    assert len(facets) == 4 and '条目19' in facets[-1]
    assert split_query('支持TLS1.2；AES加密')[0] == '支持TLS1.2'
