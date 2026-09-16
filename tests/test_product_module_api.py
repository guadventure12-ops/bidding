"""Product-module API and actual optional writer integration, isolated and offline."""
import copy
import json
import socket
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, workflow, provider, product_modules, module_drafting
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setenv('MX_TESTING', '1')
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.setattr(provider, 'key_configured', lambda: False)
    monkeypatch.setattr(provider, 'chat_json', lambda *a, **k: pytest.fail('No model in local product-module operations'))
    monkeypatch.setattr(workflow.POOL, 'submit', lambda *a, **k: None)
    original_connect = socket.socket.connect
    def ipc_only(sock, address):
        assert isinstance(address, tuple) and address[0] in ('127.0.0.1', '::1') and address[1] not in (8765, 18717), 'External/production network prohibited'
        return original_connect(sock, address)
    def no_dns(*args, **kwargs): raise AssertionError('DNS prohibited in isolated product-module API tests')
    monkeypatch.setattr(socket.socket, 'connect', ipc_only)
    monkeypatch.setattr(socket, 'getaddrinfo', no_dns)
    db.init()
    now = db.now()
    for pid in ('p', 'other'):
        db.insert('projects', {'id': pid, 'name': '合成 API 编制项目', 'domain': 'archive', 'company_name': '合成企业', 'created_at': now, 'updated_at': now})
    for sid, pid, ordinal in [('s', 'p', 0), ('s2', 'p', 1), ('other-s', 'other', 0)]:
        rid = sid + '-r'
        db.insert('requirements', {'id': rid, 'project_id': pid, 'title': '档案查询', 'text': '提供档案查询和授权检索',
                                  'response': '人工核对的原有响应', 'status': 'confirmed', 'created_at': now, 'updated_at': now})
        db.insert('sections', {'id': sid, 'project_id': pid, 'ordinal': ordinal, 'title': '当前二级主题-' + sid,
                              'outline_group_id': 'group-' + pid, 'outline_group_title': '方案分类',
                              'content': '原有正文包含项目数字 123 与接口名称。\n\n| 字段 | 内容 |\n| --- | --- |\n| 代码 | ABC-123 |',
                              'requirement_ids': [rid], 'status': 'approved', 'user_edited': 1, 'created_at': now, 'updated_at': now})
    db.insert('documents', {'id': 'existing-kb', 'name': '原有企业资料', 'path': str(tmp_path / 'existing.md'), 'source_type': 'knowledge',
                           'sha256': 'b' * 64, 'status': 'pending', 'scope': 'archive', 'parse_status': 'ready', 'created_at': now, 'updated_at': now})
    db.insert('chunks', {'id': 'existing-c', 'document_id': 'existing-kb', 'ordinal': 0, 'locator': '段落1', 'text': '原有企业资料全文，保持待审核状态。'})
    db.insert('checks', {'id': 'old-check', 'project_id': 'p', 'code': 'unresolved', 'severity': 'error', 'message': '真实附件尚未提交', 'created_at': now})
    db.insert('reviews', {'id': 'old-review', 'project_id': 'p', 'fingerprint': 'old', 'status': 'complete',
                          'findings': [{'section_id': 's', 'verdict': 'contradiction', 'reason': '历史内容冲突'}], 'created_at': now})
    export_file = tmp_path / 'original-export.md'
    export_file.write_text('已有导出原文，禁止覆盖。', encoding='utf-8')
    db.insert('exports', {'id': 'old-export', 'project_id': 'p', 'name': '旧稿.md', 'path': str(export_file), 'format': 'md', 'created_at': now})
    db.update('projects', 'p', {'analysis_status': 'complete', 'analysis_fingerprint': workflow.fingerprint('p'),
                              'metadata': {'content_todos': [{'id': 'delivery', 'kind': 'delivery', 'origin': 'manual', 'message': '附件签章需处理',
                                                              'section_ids': ['s'], 'requirement_ids': ['s-r'], 'blocks_content': False, 'blocks_delivery': True, 'status': 'open'}]}})
    result = TestClient(app)
    yield result, monkeypatch, export_file
    result.close()
    assert not workflow.EDITING


def checked(response, code=200):
    assert response.status_code == code, response.text
    return response.json()


def create(client, title='接口模块', content='选中的模块固定内容 v1。', scope='general'):
    return checked(client.post('/api/product-modules', json={'title': title, 'content': content, 'scope': scope}))['module']


def append(client, module_ids, sid='s', request=None):
    plan = checked(client.post(f'/api/sections/{sid}/product-modules/preview', json={'module_ids': module_ids}))
    payload = {'module_ids': module_ids, 'revision': plan['revision'], 'request_id': request or uuid.uuid4().hex, 'confirmed': True}
    result = checked(client.post(f'/api/sections/{sid}/product-modules/apply', json=payload))
    return plan, payload, result


def section(sid='s'):
    return db.one('SELECT * FROM sections WHERE id=?', (sid,))


def detail(client):
    return checked(client.get('/api/projects/p'))


def test_no_key_api_crud_preview_append_refresh_and_business_preservation(client):
    c, _, old_export = client
    # Refresh can populate the application's existing basic-source cache; take
    # the business baseline after that ordinary read to isolate append effects.
    detail(c)
    protected = {table: db.all('SELECT * FROM ' + table + ' ORDER BY id') for table in ('requirements', 'documents', 'chunks', 'reviews', 'checks', 'exports')}
    other_sections = [section('s2'), section('other-s')]
    original, metadata = section(), db.one('SELECT * FROM projects WHERE id="p"')['metadata']
    assert checked(c.get('/api/product-modules'))['modules'] == []
    a = create(c, content='第一模块保留数字 456。\n\n|项|值|\n|---|---|\n|来源|人工|')
    b = create(c, title='第二模块', content='第二模块中文正文。')
    edited = checked(c.patch('/api/product-modules/' + a['id'], json={'revision': a['revision'], 'title': '自定义新名称'}))['module']
    assert edited['version'] == 2 and edited['content'] == a['content']
    assert checked(c.get('/api/product-modules/' + a['id'])) == edited
    noop = checked(c.patch('/api/product-modules/' + a['id'], json={'revision': edited['revision'], 'content': edited['content']}))['module']
    assert noop == edited
    plan = checked(c.post('/api/sections/s/product-modules/preview', json={'module_ids': [b['id'], a['id']]}))
    assert section() == original and not db.all('SELECT * FROM project_snapshots')
    expected = original['content'] + '\n\n' + b['content'] + '\n\n' + a['content']
    assert plan['after'] == expected and edited['title'] not in expected
    payload = {'module_ids': [b['id'], a['id']], 'revision': plan['revision'], 'request_id': 'local-operation', 'confirmed': True}
    applied = checked(c.post('/api/sections/s/product-modules/apply', json=payload))
    assert applied['appended_count'] == 2 and applied['section']['status'] == 'draft'
    assert checked(c.post('/api/sections/s/product-modules/apply', json=payload)) == applied
    refreshed = detail(c)
    current = next(s for s in refreshed['sections'] if s['id'] == 's')
    assert current['content'] == expected and current['requirement_ids'] == original['requirement_ids']
    assert [section('s2'), section('other-s')] == other_sections
    for table, rows in protected.items(): assert db.all('SELECT * FROM ' + table + ' ORDER BY id') == rows
    assert old_export.read_text(encoding='utf-8') == '已有导出原文，禁止覆盖。'
    sources = refreshed['project']['metadata'][product_modules.SOURCE_KEY]['s']
    assert [(s['module_id'], s['version']) for s in sources] == [(b['id'], 1), (a['id'], 2)]
    assert all(s['source_kind'] == 'user_authored' and s['enterprise_fact'] is False for s in sources)
    assert {k: v for k, v in refreshed['project']['metadata'].items() if k != product_modules.SOURCE_KEY} == metadata
    assert not db.all('SELECT * FROM jobs')
    _, _, repeated = append(c, [a['id'], b['id']])
    assert repeated['appended_count'] == 0 and repeated['skipped_count'] == 2
    assert section() == current
    assert checked(c.request('DELETE', '/api/product-modules/' + a['id'], json={'revision': edited['revision'], 'confirmed': True}))['deleted']
    assert section()['content'] == expected
    assert checked(c.post('/api/product-module-operations/' + applied['snapshot_id'] + '/undo', json={'confirmed': True}))['status'] == 'restored'
    assert section()['content'] == original['content'] and section()['status'] == 'draft'


@pytest.mark.parametrize('change', ['body', 'module', 'delete', 'scope', 'inactive', 'busy'])
def test_api_preview_and_operation_conflicts_never_overwrite(client, change):
    c, _, _ = client
    a = create(c)
    plan = checked(c.post('/api/sections/s/product-modules/preview', json={'module_ids': [a['id']]}))
    db.set_setting('review_rules', {'operation_revision': False, 'operation_conflict': False, 'restore_conflict': False})
    if change == 'body': checked(c.patch('/api/sections/s', json={'content': '后续保存的人工正文'}))
    elif change == 'module': checked(c.patch('/api/product-modules/' + a['id'], json={'revision': a['revision'], 'content': '后续模块版本'}))
    elif change == 'delete': checked(c.request('DELETE', '/api/product-modules/' + a['id'], json={'revision': a['revision'], 'confirmed': True}))
    elif change == 'scope': checked(c.patch('/api/product-modules/' + a['id'], json={'revision': a['revision'], 'scope': 'expense'}))
    elif change == 'inactive':
        p = db.one('SELECT * FROM projects WHERE id="p"')
        db.update('projects', 'p', {'metadata': {**p['metadata'], 'outline_selection': {'confirmed': True, 'groups': [{'id': 'group-p', 'enabled': False, 'section_ids': ['s', 's2']}], 'fixed_groups': []}}})
    else: db.insert('jobs', {'id': 'busy', 'project_id': 'p', 'mode': 'generate', 'status': 'queued', 'created_at': db.now()})
    before = section()
    response = c.post('/api/sections/s/product-modules/apply', json={'module_ids': [a['id']], 'revision': plan['revision'], 'request_id': 'conflict', 'confirmed': True})
    checked(response, 400)
    assert section() == before and not db.all('SELECT * FROM project_snapshots')


def test_api_scope_selection_confirmation_and_undo_conflict(client):
    c, _, _ = client
    a = create(c, scope='expense')
    checked(c.post('/api/sections/s/product-modules/preview', json={'module_ids': [a['id']]}), 400)
    checked(c.post('/api/sections/s/product-modules/preview', json={'module_ids': [a['id'], a['id']]}), 400)
    b = create(c)
    plan = checked(c.post('/api/sections/s/product-modules/preview', json={'module_ids': [b['id']]}))
    checked(c.post('/api/sections/s/product-modules/apply', json={'module_ids': [b['id']], 'revision': plan['revision'], 'request_id': 'no-confirm'}), 400)
    _, _, result = append(c, [b['id']])
    checked(c.post('/api/product-module-operations/' + result['snapshot_id'] + '/undo', json={'confirmed': False}), 400)
    checked(c.patch('/api/sections/s', json={'content': '后来人工调整后的最终草稿'}))
    db.set_setting('review_rules', {'restore_conflict': False})
    checked(c.post('/api/product-module-operations/' + result['snapshot_id'] + '/undo', json={'confirmed': True}), 400)
    assert section()['content'] == '后来人工调整后的最终草稿'


def _model_data(prompt, label):
    value = prompt.split(label, 1)[1].lstrip()
    return json.JSONDecoder().raw_decode(value)[0]


def test_optional_ai_uses_saved_v1_module_once_for_25_legacy_requirements(client):
    c, monkeypatch, old_export = client
    original_other = [section('s2'), section('other-s')]
    # No blueprint is installed: the regression target is the original legacy
    # chapter path that previously split 25 requirements into several batches.
    ids = []
    for i in range(25):
        identity = f'module-r-{i:02}'
        ids.append(identity)
        db.insert('requirements', {'id': identity, 'project_id': 'p', 'title': f'检索要求{i}', 'text': '提供档案查询与授权检索，保持档案完整',
                                  'response': '已有人工作答', 'status': 'confirmed', 'created_at': db.now(), 'updated_at': db.now()})
    db.update('sections', 's', {'requirement_ids': ids})
    a = create(c, title='授权检索', content='固定素材版本一，人工约定接口代号 KEEP-123。')
    _, _, appended = append(c, [a['id']])
    saved_body = section()['content']
    selected_sources = copy.deepcopy(db.one('SELECT * FROM projects WHERE id="p"')['metadata'][product_modules.SOURCE_KEY]['s'])
    assert not provider.key_configured() and not db.all('SELECT * FROM jobs')
    # The only approved knowledge source is a separately uploaded and reviewed
    # enterprise document, never the manually authored product module.
    uploaded = checked(c.post('/api/knowledge/upload', files={'file': ('合成原始产品资料.md', '系统支持档案查询与授权检索。系统提供完整档案查询结果。'.encode(), 'text/markdown')}))['document']
    approved = checked(c.patch('/api/knowledge/' + uploaded['id'], json={'status': 'approved', 'scope': 'archive'}))['document']
    assert approved['status'] == 'approved'
    monkeypatch.setattr(provider, 'key_configured', lambda: True)
    preview = checked(c.get('/api/sections/s/regeneration'))
    assert preview['batch_count'] == 1 and preview['uses_saved_module_draft'] is True
    # Updating the library after AI preview must neither read the new content
    # nor invalidate the frozen input snapshot used for this requested job.
    v2 = checked(c.patch('/api/product-modules/' + a['id'], json={'revision': a['revision'], 'content': '未选版本二 LIVE-V2-MUST-NOT-APPEAR'}))['module']
    assert v2['version'] == 2
    assert checked(c.get('/api/sections/s/regeneration'))['revision'] == preview['revision']
    protected = {table: db.all('SELECT * FROM ' + table + ' ORDER BY id') for table in ('requirements', 'documents', 'chunks', 'reviews', 'exports')}
    captured = []
    def mock_model(system, prompt, **kwargs):
        draft = _model_data(prompt, '当前章节人工素材数据：\n')
        requirements = _model_data(prompt, '要求数据：\n')
        evidence = _model_data(prompt, '证据数据：\n')
        task = _model_data(prompt, '完整章节任务及采购上下文（不是企业事实）：\n')
        captured.append({'prompt': prompt, 'draft': draft, 'requirements': requirements, 'task': task})
        checks = {
            'saved_body_exact': draft['current_saved_body'] == saved_body,
            'frozen_v1_unverified': draft['selected_modules'][0]['version'] == 1 and draft['enterprise_fact_verified'] is False,
            'no_live_v2': 'LIVE-V2-MUST-NOT-APPEAR' not in prompt,
            'one_section_25_requirements': len(requirements) == 25 and task['section_id'] == 's',
            # The real writer uses reversible per-request transport aliases.
            # Persisted original IDs are verified again after the saved result.
            'all_requirement_aliases': {r['id'] for r in requirements} == {f'R{i + 1:02}' for i in range(25)},
            'real_approved_evidence_only': bool(evidence) and all(e['id'] != a['id'] for e in evidence),
        }
        if not all(checks.values()):
            raise provider.ProviderError('Mock observed integration contract failure: ' + json.dumps(checks))
        evidence_id = evidence[0]['id']
        return {'content': saved_body + '\n\n系统支持档案查询与授权检索。[E:' + evidence_id + ']',
                'responses': [{'requirement_id': r['id'], 'response': '系统支持档案查询与授权检索。[E:' + evidence_id + ']',
                               'evidence_ids': [evidence_id], 'gap': False, 'gap_reason': ''} for r in requirements],
                '_usage': {'prompt_tokens': 100, 'completion_tokens': 100, 'total_tokens': 200}}
    monkeypatch.setattr(provider, 'chat_json', mock_model)
    request = {'revision': preview['revision'], 'outline_revision': preview['outline_revision'], 'request_id': uuid.uuid4().hex, 'confirmed': True,
               'instruction': '保留当前素材并完善本章'}
    job = checked(c.post('/api/sections/s/regenerate', json=request))['job']
    assert not captured
    workflow._run(job['id'])
    completed = checked(c.get('/api/jobs/' + job['id']))['job']
    assert completed['status'] == 'succeeded', completed['error']
    assert len(captured) == 1
    generated = section()
    assert generated['content'].startswith(saved_body) and generated['content'].count('固定素材版本一') == 1
    assert generated['status'] == 'draft' and generated['requirement_ids'] == ids
    assert [section('s2'), section('other-s')] == original_other
    for table, before in protected.items(): assert db.all('SELECT * FROM ' + table + ' ORDER BY id') == before
    assert old_export.read_text(encoding='utf-8') == '已有导出原文，禁止覆盖。'
    current_sources = db.one('SELECT * FROM projects WHERE id="p"')['metadata'][product_modules.SOURCE_KEY]['s']
    assert current_sources == selected_sources
    history = checked(c.get('/api/sections/s/generation-history'))['snapshots']
    assert history[0]['content'] == saved_body
    restored = checked(c.post('/api/section-generation/' + completed['result']['snapshot_id'] + '/restore'))
    assert section()['content'] == saved_body and section()['status'] == 'draft'
    assert db.one('SELECT * FROM projects WHERE id="p"')['metadata'][product_modules.SOURCE_KEY]['s'] == selected_sources
    assert len(captured) == 1


def test_large_module_stays_manual_and_only_optional_ai_refuses(client):
    c, monkeypatch, _ = client
    a = create(c, content='人工大段落。' * 7000)
    _, _, applied = append(c, [a['id']])
    original = section()
    assert len(original['content']) > module_drafting.MAX_DRAFT_CHARS
    assert applied['appended_count'] == 1
    monkeypatch.setattr(provider, 'key_configured', lambda: True)
    error = checked(c.get('/api/sections/s/regeneration'), 400)
    assert '40000' in str(error)
    assert section() == original and not db.all('SELECT * FROM jobs')
    saved = checked(c.patch('/api/sections/s', json={'content': original['content'] + '\n人工补充结尾。'}))['section']
    assert saved['content'].endswith('人工补充结尾。')
    assert next(s for s in detail(c)['sections'] if s['id'] == 's')['content'] == saved['content']
