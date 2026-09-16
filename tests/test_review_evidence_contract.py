"""Evidence approval contracts for review batching; isolated DB and no model calls."""
import hashlib
from datetime import date as real_date

import pytest

from app import db, provider, workflow


@pytest.fixture
def isolated_review_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'isolated-review-db')
    monkeypatch.setenv('MX_TESTING', '1')
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.setattr(provider, 'chat_json', lambda *a, **kw: pytest.fail('model call is forbidden'))
    db.init()
    workflow._evidence_index.cache_clear()
    return tmp_path


def make_project():
    id = db.uid()
    db.insert('projects', {'id': id, 'name': '审核隔离项目', 'domain': 'archive',
                          'company_name': '测试企业', 'created_at': db.now(), 'updated_at': db.now()})
    return id


def evidence(text='产品支持20并发用户。', **overrides):
    doc, chunk = db.uid(), db.uid()
    row = {'id': doc, 'name': '隔离证据', 'path': 'not-read-by-this-test.txt',
           'source_type': 'tender' if overrides.get('project_id') else 'knowledge',
           'sha256': hashlib.sha256(text.encode()).hexdigest(), 'status': 'approved',
           'parse_status': 'ready', 'scope': 'archive', 'created_at': db.now(), 'updated_at': db.now()}
    row.update(overrides)
    db.insert('documents', row)
    db.insert('chunks', {'id': chunk, 'document_id': doc, 'ordinal': 0, 'text': text, 'locator': '测试段落'})
    return doc, chunk


def only_ids(ids, domain='archive'):
    return [row['id'] for row in workflow._valid_evidence(ids, domain)]


def test_evidence_gates_dedup_order_and_expiry_boundary(isolated_review_db, monkeypatch):
    class Today(real_date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 10)
    monkeypatch.setattr(workflow, 'date', Today)
    p = make_project()
    _, valid = evidence()
    _, general = evidence(scope='general')
    _, today = evidence(valid_until='2026-09-10')
    _, no_expiry = evidence(valid_until=None)
    excluded = [evidence(**options)[1] for options in (
        {'status': 'pending'}, {'status': 'rejected'}, {'parse_status': 'pending'},
        {'scope': 'expense'}, {'scope': 'historical'}, {'valid_until': '2026-09-09'},
        {'project_id': p},
    )]
    sequence = [today, *excluded, valid, general, valid, no_expiry, 'missing']
    assert only_ids(sequence) == [today, valid, general, no_expiry]
    assert only_ids(sequence, 'expense') == [excluded[3], general]
    assert only_ids(None) == [] and only_ids('not-a-list') == []


def test_curated_allowlist_wrapper_and_constraints_survive_batching(isolated_review_db):
    quote = '系统支持20并发用户。'
    metadata = {'evidence_kind': 'curated_extract', 'source_document_id': 'original-document',
                'source_url': 'https://example.invalid/source', 'source_date': '2025-09-01',
                'limitations': '仅作为技术草稿来源', 'authorization_scope': '本项目验收草稿',
                'audited_excerpts': [{'quote': quote, 'sha256': hashlib.sha256(quote.encode()).hexdigest(),
                                     'source_block_id': 'original-chunk', 'locator': '源段落',
                                     'start_offset': 10, 'length': len(quote)}]}
    _, valid = evidence('来源注记200并发用户\n原文：\n' + quote, metadata=metadata)
    _, header = evidence('标题与前言：200并发用户', metadata=metadata)
    _, unreviewed = evidence('来源注记\n原文：\n系统支持200并发用户。', metadata=metadata)
    _, broken_hash = evidence('来源注记\n原文：\n' + quote,
                              metadata={**metadata, 'audited_excerpts': [{'quote': quote, 'sha256': 'incorrect'}]})
    _, empty_allowlist = evidence('来源注记\n原文：\n' + quote,
                                  metadata={**metadata, 'audited_excerpts': []})
    rows = workflow._valid_evidence([header, valid, unreviewed, broken_hash, empty_allowlist], 'archive')
    assert [row['id'] for row in rows] == [valid]
    row = rows[0]
    assert row['support_text'] == quote and workflow._support_text(row) == quote
    assert '200并发用户' in row['text'] and '200并发用户' not in row['support_text']
    assert row['source_constraints']['authorization_scope'] == '本项目验收草稿'
    assert row['source_constraints']['limitations'] == '仅作为技术草稿来源'
    assert row['source_excerpt']['source_block_id'] == 'original-chunk'
    assert row['source_excerpt']['sha256'] == metadata['audited_excerpts'][0]['sha256']


@pytest.mark.parametrize('change', [
    {'status': 'pending'}, {'scope': 'expense'}, {'valid_until': '2000-01-01'},
    {'parse_status': 'failed'},
    {'metadata': {'evidence_kind': 'curated_extract', 'audited_excerpts': []}},
])
def test_repeated_calls_do_not_reuse_revoked_evidence(isolated_review_db, change):
    doc, chunk = evidence()
    assert only_ids([chunk]) == [chunk]
    # Deliberately no timestamp mutation: invocation-local batching must re-read
    # the current source and cannot depend on callers remembering to touch it.
    db.update('documents', doc, change)
    assert only_ids([chunk]) == []


def test_review_does_not_borrow_numbers_from_other_targets(isolated_review_db):
    p = make_project()
    tender, tender_chunk = evidence('采购要求支持200并发用户。', project_id=p)
    _, low = evidence('企业产品支持20并发用户。')
    _, high = evidence('企业产品支持200并发用户。')
    req_ids, section_ids = [], []
    for ordinal, chunk in enumerate((low, high)):
        rid, sid = db.uid(), db.uid()
        req_ids.append(rid)
        section_ids.append(sid)
        db.insert('requirements', {'id': rid, 'project_id': p, 'document_id': tender,
            'chunk_id': tender_chunk, 'title': '并发能力', 'text': '采购要求200并发用户',
            'quote': '采购要求支持200并发用户。', 'response': '企业满足200并发用户',
            'evidence_ids': [chunk], 'status': 'confirmed', 'created_at': db.now(), 'updated_at': db.now()})
        db.insert('sections', {'id': sid, 'project_id': p, 'ordinal': ordinal, 'title': '并发能力',
            'content': f'企业满足200并发用户[E:{chunk}]', 'requirement_ids': [rid],
            'evidence_ids': [chunk], 'status': 'approved', 'created_at': db.now(), 'updated_at': db.now()})
    findings = workflow.review_project(p)
    issue = next(x for x in findings if x['code'] == 'unverified_numbers')
    assert {x.get('requirement_id') for x in issue['details']['items'] if 'requirement_id' in x} == {req_ids[0]}
    assert {x.get('section_id') for x in issue['details']['items'] if 'section_id' in x} == {section_ids[0]}
    # Revoke the source between reviews. Both citation and numeric checks must
    # reflect fresh state; a batch/memo from the first review cannot survive.
    high_doc = db.one('SELECT document_id FROM chunks WHERE id=?', (high,))['document_id']
    db.update('documents', high_doc, {'status': 'pending'})
    findings = workflow.review_project(p)
    invalid = next(x for x in findings if x['code'] == 'invalid_evidence')
    assert invalid['details']['requirement_ids'] == [req_ids[1]]
    assert any(x['code'] == 'broken_citation' and x['details']['section_id'] == section_ids[1] for x in findings)
