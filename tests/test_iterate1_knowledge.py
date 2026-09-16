"""Synthetic, offline coverage for each enterprise knowledge review switch."""
import copy

import pytest

from app import db, knowledge_review as kr, evidence_retrieval as er, review_rules as rr


def source(**changes):
    doc = {'id': 'doc', 'name': '产品说明.md', 'project_id': None, 'source_type': 'knowledge',
           'parse_status': 'ready', 'status': 'pending', 'valid_until': '', 'scope': 'archive',
           'metadata': {}, 'sha256': 'source-hash', **changes}
    chunks = [{'id': 'chunk', 'document_id': 'doc', 'text': '支持档案检索。',
               'kind': 'text', 'locator': '第1段'}]
    return doc, chunks


@pytest.mark.parametrize('rule,changes,text', [
    ('knowledge_identity', {'source_type': 'tender'}, None),
    ('knowledge_parse', {'parse_status': 'error'}, None),
    ('knowledge_expiry', {'valid_until': '2000-01-01'}, None),
    ('knowledge_scope', {'scope': 'internal'}, None),
    ('knowledge_origin', {'metadata': {'ai_generated': True}}, None),
    ('knowledge_body', {}, '# 只有标题'),
    ('knowledge_version', {'status': 'approved', 'metadata': {kr.KINDS: {
        'mode': 'partial', 'entries': [{'id': 'chunk', 'sha256': 'old-version', 'text': '旧正文'}]}}}, None),
])
def test_each_knowledge_switch_changes_actual_plan(rule, changes, text):
    doc, chunks = source(**copy.deepcopy(changes))
    if text is not None:
        chunks[0]['text'] = text
    assert kr.plan(doc, chunks)['status'] == 'blocked'
    with rr.scope({rule: False}):
        result = kr.plan(doc, chunks)
    assert result['status'] == 'eligible'
    assert result['entries'][0]['text'] == chunks[0]['text']
    assert result['disabled_rules'] == [rule]
    if rule == 'knowledge_version':
        assert result['expanded']
        assert '重新认可' in result['recognized_scope']


def test_origin_scope_filters_and_explicit_provenance_remain_attached():
    row = {'document_name': 'AI生成稿', 'document_metadata': {'project_ids': ['a'], 'ai_generated': True}}
    assert not kr.allowed_for_project(row, 'b')
    with rr.scope({'knowledge_origin': False}):
        assert not kr.allowed_for_project(row, 'b')
        assert kr.allowed_for_project(row, 'a')
    with rr.scope({'knowledge_origin': False, 'knowledge_scope': False}):
        assert kr.allowed_for_project(row, 'b')
    assert row['document_metadata'] == {'project_ids': ['a'], 'ai_generated': True}


def test_version_switch_changes_index_scope_without_inventing_attachment_body():
    text = '支持新版星河索引。'
    row = {'id': 'c', 'document_id': 'd', 'text': text, 'document_status': 'approved',
           'document_metadata': {'product_version': 'V7', kr.KINDS: {'mode': 'partial', 'entries': [
               {'id': 'c', 'sha256': kr.text_hash('旧正文'), 'text': '旧正文'}]}}}
    assert not er.build_index([row]).rows
    with rr.scope({'knowledge_version': False}):
        index = er.build_index([row])
        assert er.search(index, '星河索引')[0]['support_text'] == text
        assert index.rows[0]['source_constraints']['product_version'] == 'V7'
        assert '审核已关闭' in index.rows[0]['source_constraints']['recognized_scope']
        row['text'] = '[尚未下载](https://example.test/manual.pdf)'
        assert not er.build_index([row]).rows
    with rr.scope({'knowledge_version': False, 'knowledge_body': False}):
        result = er.build_index([row]).rows[0]
        assert result['support_text'] == row['text']
        assert result['source_constraints']['attachment_note'] == '不包含未获取的附件正文'


def test_all_disabled_still_requires_an_object_and_literal_stored_text():
    doc, chunks = source()
    with rr.scope({key: False for key in rr.EDITABLE}):
        assert kr.plan(None, [])['status'] == 'blocked'
        assert kr.plan(doc, [])['status'] == 'blocked'
        chunks[0]['text'] = '   '
        assert kr.plan(doc, chunks)['status'] == 'blocked'
        chunks[0]['text'] = '[产品手册](https://example.test/manual.pdf)'
        item = kr.plan(doc, chunks)
        assert item['status'] == 'eligible'
        assert item['entries'][0]['text'] == chunks[0]['text']
        assert '不含未获取的附件正文' in item['recognized_scope']
        assert '只认可实际读取部分' in item['attachment_note']


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setenv('MX_TESTING', '1')
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    db.init()
    return tmp_path


def seed(**fields):
    doc, chunks = source(**fields)
    doc.update(created_at=db.now(), updated_at=db.now(), path='synthetic.md')
    db.insert('documents', doc)
    db.insert('chunks', {**chunks[0], 'ordinal': 0})
    return doc['id']


def test_single_and_batch_use_saved_flags_and_keep_existing_dates(isolated):
    id = seed(parse_status='error', valid_until='2000-01-01')
    with pytest.raises(ValueError, match='解析'):
        kr.review_single(id, {'status': 'approved'})
    db.set_setting('review_rules', {'knowledge_parse': False, 'knowledge_expiry': False})
    assert kr.preview([id])['counts']['eligible'] == 1
    result = kr.review_single(id, {'status': 'approved'})
    assert result['status'] == 'approved'
    assert result['parse_status'] == 'error'
    assert result['valid_until'] == '2000-01-01'
    assert result['metadata'][kr.KINDS]['entries'][0]['text'] == '支持档案检索。'
    assert kr.preview([id])['counts']['already_approved'] == 1


def test_settings_revision_is_bound_to_preview(isolated):
    id = seed()
    preview = kr.preview([id])
    db.set_setting('review_rules', {'knowledge_version': False})
    with pytest.raises(ValueError, match='审核设置'):
        kr.apply(preview['ticket'])
    assert db.one('SELECT status FROM documents WHERE id=?', (id,))['status'] == 'pending'


def test_disabling_knowledge_version_does_not_disable_operation_revision(isolated):
    id = seed()
    db.set_setting('review_rules', {'knowledge_version': False})
    preview = kr.preview([id])
    db.update('chunks', 'chunk', {'text': '预览后新增的正文'})
    result = kr.apply(preview['ticket'])
    assert result['counts']['changed'] == 1
    assert result['counts']['approved'] == 0


def test_explicit_operation_revision_off_is_effective_and_snapshot_is_actual(isolated):
    id = seed()
    db.set_setting('review_rules', {'operation_revision': False})
    preview = kr.preview([id])
    db.update('chunks', 'chunk', {'text': '预览后新增的真实存储文字'})
    with db.connect() as conn:
        doc, chunks = kr._read(conn, id)
    expected_revision = kr.revision(doc, chunks)
    result = kr.apply(preview['ticket'])
    assert result['counts']['approved'] == 1
    assert result['items'][0]['content_preview'][0]['text'] == chunks[0]['text']
    snapshot = db.one('SELECT * FROM project_snapshots WHERE id=?', (result['operation_id'],))['payload']
    assert snapshot['changes'][0]['before_version'] == expected_revision
    assert kr.apply(preview['ticket']) == result


def test_version_off_partial_expansion_is_previewed_and_saved(isolated):
    id = seed(status='approved')
    chunk = db.one('SELECT * FROM chunks WHERE id=?', ('chunk',))
    db.update('documents', id, {'metadata': {kr.KINDS: {'mode': 'partial', 'entries': [
        {'id': chunk['id'], 'text': '支持档案', 'sha256': kr.text_hash(chunk['text']), 'locator': chunk['locator']}]}}})
    db.set_setting('review_rules', {'knowledge_version': False})
    preview = kr.preview([id])
    assert preview['items'][0]['expanded']
    assert preview['items'][0]['content_preview'][0]['text'] == chunk['text']
    assert preview['counts']['eligible'] == 1
    result = kr.apply(preview['ticket'])
    assert result['counts']['approved'] == 1
    assert kr.preview([id])['counts']['already_approved'] == 1
    assert kr.undo(result['operation_id'])['counts']['undone'] == 1
    assert db.one('SELECT * FROM documents WHERE id=?', (id,))['metadata'][kr.KINDS]['entries'][0]['text'] == '支持档案'
