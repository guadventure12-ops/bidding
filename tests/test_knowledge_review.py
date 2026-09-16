"""Isolated, offline knowledge approval contract and real downstream consumers."""
import copy
import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import db, knowledge_review as kr, workflow, provider
from app.main import app


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setenv('MX_TESTING', '1')
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    def forbidden(*args, **kwargs):
        pytest.fail('This suite must not call any model')
    monkeypatch.setattr(provider, 'chat_json', forbidden)
    workflow._evidence_index.cache_clear()
    db.init()
    return tmp_path


def document(text='支持档案检索。', **fields):
    id = db.uid()
    doc = {'id': id, 'name': '企业产品资料.md', 'path': 'isolated.md', 'sha256': kr.text_hash(text),
           'source_type': 'knowledge', 'parse_status': 'ready', 'scope': 'archive',
           'status': 'pending', 'page_count': 0, 'text_chars': len(text), 'created_at': db.now(), 'updated_at': db.now(), **fields}
    db.insert('documents', doc)
    for n, body in enumerate(text.split('\n\n')):
        db.insert('chunks', {'id': db.uid(), 'document_id': id, 'ordinal': n, 'text': body, 'locator': f'段落{n+1}'})
    return id


def get(id):
    return db.one('SELECT * FROM documents WHERE id=?', (id,))


def approve(ids):
    return kr.apply(kr.preview(ids)['ticket'])


def seed_project():
    id = db.uid()
    db.insert('projects', {'id': id, 'name': '隔离项目', 'domain': 'archive', 'created_at': db.now(), 'updated_at': db.now()})
    return id


def immutable():
    return {table: db.all('SELECT * FROM '+table) for table in
            ('projects', 'sections', 'requirements', 'reviews', 'checks', 'exports', 'chunks', 'jobs')}


def test_preview_readonly_apply_persist_downstream_and_no_project_writes(isolated):
    project = seed_project()
    id = document('支持量子盒档案检索。\n\n支持数据校验。', metadata={'product_version': '4.0及以后版本', 'limitations': ['部署条件保持原资料限定']})
    source_id = db.one('SELECT id FROM chunks WHERE document_id=?', (id,))['id']
    db.insert('sections', {'id': db.uid(), 'project_id': project, 'ordinal': 0, 'title': '已有章节',
              'content': '已有正文，禁止修改', 'status': 'approved', 'evidence_ids': [source_id], 'created_at': db.now(), 'updated_at': db.now()})
    db.insert('reviews', {'id': db.uid(), 'project_id': project, 'fingerprint': 'old-review', 'status': 'failed', 'created_at': db.now()})
    db.insert('checks', {'id': db.uid(), 'project_id': project, 'code': 'attachment', 'severity': 'error', 'message': '签章未完成', 'created_at': db.now()})
    exported = isolated / '旧交付.txt'
    exported.write_text('原有交付内容', encoding='utf-8')
    db.insert('exports', {'id': db.uid(), 'project_id': project, 'name': exported.name, 'path': str(exported), 'format': 'txt', 'created_at': db.now()})
    assert workflow.search_evidence('量子盒档案检索', 'archive') == []  # Prime old cache.
    assert workflow.search_evidence('量子盒档案检索', 'archive', project_id=project) == []
    before = immutable()
    saved_doc = get(id)
    preview = kr.preview([id])
    assert preview['counts']['eligible'] == 1
    assert preview['items'][0]['recognized_scope'] == '当前已解析正文'
    assert get(id) == saved_doc
    assert not db.all('SELECT * FROM project_snapshots')
    result = kr.apply(preview['ticket'])
    assert result['counts']['approved'] == 1
    approved = get(id)
    assert approved['metadata'][kr.KINDS]['entries']
    assert kr.apply(preview['ticket']) == result
    assert get(id) == approved
    generic = workflow.search_evidence('量子盒档案检索', 'archive')
    specific = workflow.search_evidence('量子盒档案检索', 'archive', project_id=project)
    assert generic[0]['document_id'] == id == specific[0]['document_id']
    evidence = workflow._valid_evidence([r['id'] for r in specific], 'archive', project)
    inputs = workflow._generation_inputs({'title': '功能响应', 'requirements': []}, {e['id']: e for e in evidence})
    assert any('量子盒' in e['support_text'] for e in inputs['evidence'])
    assert inputs['evidence'][0]['source_constraints']['product_version'] == '4.0及以后版本'
    assert workflow.search_evidence('量子盒档案检索', 'expense') == []
    assert immutable() == before
    assert exported.read_text(encoding='utf-8') == '原有交付内容'
    assert kr.operations()[0]['can_undo']
    outcome = kr.undo(result['operation_id'])
    assert outcome['counts']['undone'] == 1
    assert get(id)['metadata'] == saved_doc['metadata']
    assert workflow.search_evidence('量子盒档案检索', 'archive') == []
    assert kr.undo(result['operation_id']) == outcome
    assert kr.apply(preview['ticket']) == result  # Retry never reapplies undone approval.


def test_short_markdown_zero_pages_link_only_mixed_and_empty(isolated):
    ids = [document('支持。'), document('# 标题'), document(''),
           document('# 附件\n\n[说明](https://example.test/a.pdf)'),
           document('支持加密。\n\n附件下载：\n技术白皮书.pdf\n(4.1 MB)\nhttps://example.test/a.pdf'),
           document('有内容', parse_status='error')]
    p = kr.preview(ids)
    assert p['counts']['eligible'] == 2
    assert p['counts']['blocked'] == 4
    assert p['items'][3]['reason'] == '附件正文未入库，无可用正文'
    assert p['items'][4]['attachment_note']
    result = kr.apply(p['ticket'])
    assert result['counts']['approved'] == 2
    texts = [e['text'] for e in get(ids[4])['metadata'][kr.KINDS]['entries']]
    assert texts == ['支持加密。']
    assert 'example' not in json.dumps(workflow.search_evidence('加密', 'archive'), ensure_ascii=False)


def test_partial_curated_never_expands_even_pending(isolated):
    quote = '支持星河档案索引。'
    meta = {'evidence_kind': 'curated_extract', 'audited_excerpts': [{'quote': quote, 'sha256': kr.text_hash(quote)}],
            'authorization_scope': '仅草稿', 'limitations': ['仅本地版']}
    text = '资料定位\n原文：\n'+quote+'\n\n其他未经认可的能力。\n\n定位\n原文：\n不在已认可摘录中'
    approved = document(text, metadata=meta, status='approved')
    pending = document(text, metadata=meta)
    before = copy.deepcopy(get(approved))
    result = approve([approved, pending])
    assert result['counts']['already_approved'] == result['counts']['approved'] == 1
    assert get(approved) == before
    assert len(get(pending)['metadata'][kr.KINDS]['entries']) == 1
    assert '部分摘录' in result['items'][1]['recognized_scope']
    assert not workflow.search_evidence('未经认可', 'archive')
    assert len(workflow.search_evidence('星河档案', 'archive')) == 2


@pytest.mark.parametrize('change', ['text', 'metadata', 'reparse', 'delete', 'scope', 'valid_until', 'status'])
def test_preview_version_changes_skip_not_new_content(isolated, change):
    id = document(); second = document('支持不动星档案。')
    p = kr.preview([id, second])
    if change == 'text':
        chunk = db.one('SELECT * FROM chunks WHERE document_id=?', (id,))
        db.update('chunks', chunk['id'], {'text': '新增尚未核对的内容'})
    elif change == 'reparse':
        db.execute('DELETE FROM chunks WHERE document_id=?', (id,))
        db.insert('chunks', {'id': db.uid(), 'document_id': id, 'ordinal': 0, 'locator': '重新解析', 'text': '支持档案检索。'})
    elif change == 'delete':
        db.execute('DELETE FROM chunks WHERE document_id=?', (id,)); db.execute('DELETE FROM documents WHERE id=?', (id,))
    else:
        db.update('documents', id, {change: {'metadata': {'version': '新'}, 'scope': 'expense', 'valid_until': '2099-01-01', 'status': 'approved'}[change]})
    result = kr.apply(p['ticket'])
    assert result['counts']['changed'] == result['counts']['approved'] == 1
    assert result['items'][0]['status'] == 'changed'


def test_expiry_not_renewed_and_tender_generated_excluded(isolated):
    project = seed_project()
    ids = [document(valid_until='2000-01-01'), document(status='expired'), document(project_id=project, source_type='tender'),
           document(metadata={'ai_generated': True}), document(metadata={'other_customer_only': True}),
           document(name='客户技术标_Final.docx'), document('甲方要求：必须支持量子检索。'),
           document(scope='internal'), document('支持OCR。', name='AI 创建发票.md')]
    r = approve(ids)
    assert r['counts']['approved'] == 1
    assert r['counts']['blocked'] == 8
    assert get(ids[0])['valid_until'] == '2000-01-01'
    assert get(ids[-1])['valid_until'] == ''


def test_savepoint_mixed_failure_atomic_idempotency(isolated, monkeypatch):
    ids = [document(), document(status='approved'), document('支持加密。'), document('')]
    orig = kr._write_approval
    def fail(conn, doc, item):
        orig(conn, doc, item)
        if doc['id'] == ids[2]:
            raise RuntimeError('isolated injected failure')
    monkeypatch.setattr(kr, '_write_approval', fail)
    p = kr.preview(ids)
    r = kr.apply(p['ticket'])
    assert [r['counts'][k] for k in ('approved', 'already_approved', 'failed', 'blocked')] == [1, 1, 1, 1]
    assert get(ids[2])['status'] == 'pending'
    assert kr.KINDS not in get(ids[2])['metadata']
    assert len(db.one('SELECT * FROM project_snapshots')['payload']['changes']) == 1
    assert kr.apply(p['ticket']) == r


def test_undo_conflict_and_scope_restore_only_this_operation(isolated):
    a, b = document(), document('支持加密。')
    r = approve([a, b])
    old_b = get(b)
    with workflow.editing(None):
        kr.review_single(b, {'scope': 'expense'})
    new_b = get(b)
    result = kr.undo(r['operation_id'])
    assert result['counts']['undone'] == result['counts']['conflict'] == 1
    assert get(a)['status'] == 'pending'
    assert get(b) == new_b != old_b


def test_project_constraints_and_explicit_project_selection_preserved(isolated):
    a, b = seed_project(), seed_project()
    id = document('支持限定项目档案索引。', metadata={'project_ids': [a], 'version': '仅V4'})
    approve([id])
    assert workflow.search_evidence('限定项目档案索引', 'archive') == []
    assert workflow.search_evidence('限定项目档案索引', 'archive', project_id=a)
    assert not workflow.search_evidence('限定项目档案索引', 'archive', project_id=b)
    db.update('projects', a, {'metadata': {'trusted_sources': {}}})
    assert not workflow.search_evidence('限定项目档案索引', 'archive', project_id=a)


def test_api_shared_rules_confirmation_and_restart_idempotency(isolated, monkeypatch):
    with TestClient(app) as client:
        good, empty = document('支持。'), document('# 只有标题')
        assert client.patch('/api/knowledge/'+empty, json={'status': 'approved'}).status_code == 400
        p = client.post('/api/knowledge/approval/preview', json={'document_ids': [good, empty]}).json()
        assert p['counts']['blocked'] == 1
        assert client.post('/api/knowledge/approval/apply', json={'ticket': p['ticket']}).status_code == 400
        r = client.post('/api/knowledge/approval/apply', json={'ticket': p['ticket'], 'confirmed': True}).json()
        approved = get(good)
        assert client.patch('/api/knowledge/'+good, json={'status': 'approved'}).status_code == 200
        assert get(good) == approved
        assert client.get('/api/knowledge').json()['stats']['approved'] == 1
        assert client.get('/api/knowledge/approval/operations').json()['operations'][0]['id'] == r['operation_id']
        assert client.post('/api/snapshots/'+r['operation_id']+'/restore').status_code == 400
        monkeypatch.setattr(kr, '_KEY', b'restarted-process')
        assert kr.apply(p['ticket']) == r
        assert client.post('/api/knowledge/approval/'+r['operation_id']+'/undo', json={'confirmed': True}).json()['counts']['undone'] == 1


def test_single_approval_changes_scope_and_recognition_together(isolated):
    id = document('支持费用报销。', scope='general')
    with workflow.editing(None):
        result = kr.review_single(id, {'status': 'approved', 'scope': 'expense', 'valid_until': ''})
    assert result['scope'] == 'expense'
    assert result['status'] == 'approved'
    assert result['metadata'][kr.KINDS]['entries']
    assert workflow.search_evidence('费用报销', 'expense')
    assert not workflow.search_evidence('费用报销', 'archive')


def test_active_writer_and_signature_cannot_bypass_preview(isolated):
    id = document()
    p = kr.preview([id])
    db.insert('jobs', {'id': db.uid(), 'mode': 'knowledge_import', 'status': 'running', 'created_at': db.now()})
    with pytest.raises(ValueError, match='任务'):
        kr.apply(p['ticket'])
    db.execute('DELETE FROM jobs')
    with pytest.raises(ValueError, match='失效'):
        kr.apply(p['ticket'][:-1] + ('a' if p['ticket'][-1] != 'a' else 'b'))
    assert get(id)['status'] == 'pending'


def test_legacy_approved_generated_or_procurement_is_not_retrieved(isolated):
    project = seed_project()
    generated = document('支持错误星环档案检索。', status='approved', metadata={'source_kind': 'generated'})
    buyer = document('支持错误星环档案检索。', status='approved', source_type='tender')
    ids = [r['id'] for r in db.all('SELECT * FROM chunks')]
    assert not workflow.search_evidence('错误星环档案检索', 'archive')
    assert not workflow.search_evidence('错误星环档案检索', 'archive', project_id=project)
    assert not workflow._valid_evidence(ids, 'archive', project)
    assert kr.preview([generated, buyer])['counts']['blocked'] == 2


def test_attachment_listing_with_bullets_versions_and_notices(isolated):
    text = '# 功能清单\n\n往期清单\n\n⚠️保密资料 请勿随意外传\n\n请勿随意发给客户！\n\n旗舰版功能清单2024Q1\n\nPDF版本：\n\n- [功能清单2024.xlsx](https://example.test/a.xlsx)'
    only_links = document(text)
    mixed = document(text+'\n\n支持费用校验。')
    p = kr.preview([only_links, mixed])
    assert p['items'][0]['status'] == 'blocked'
    assert '附件正文未入库' in p['items'][0]['reason']
    assert p['items'][1]['readable_blocks'] == 1
    assert p['items'][1]['constraints']['source_notices']
    kr.apply(p['ticket'])
    evidence = workflow.search_evidence('费用校验', 'archive')
    assert evidence[0]['support_text'] == '支持费用校验。'
    assert evidence[0]['source_constraints']['source_notices']


def test_null_expiry_is_not_expired_and_is_searchable(isolated):
    id = document('支持银河档案检索。', valid_until=None)
    assert approve([id])['counts']['approved'] == 1
    assert workflow.search_evidence('银河档案检索', 'archive')[0]['document_id'] == id
    assert get(id)['valid_until'] is None
