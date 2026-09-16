import copy
import hashlib

import pytest

from app import db, provider, workflow, product_modules as modules


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setenv('MX_TESTING', '1')
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    monkeypatch.setattr(provider, 'key_configured', lambda: False)
    monkeypatch.setattr(provider, 'chat_json', lambda *a, **k: pytest.fail('Product modules must not call a model'))
    db.init()
    with db.connect() as conn:
        modules.initialize(conn)
    now = db.now()
    for pid in ('p', 'other'):
        db.insert('projects', {'id': pid, 'name': '隔离素材编制验收', 'domain': 'archive', 'created_at': now, 'updated_at': now})
    for sid, pid in (('s', 'p'), ('s2', 'p'), ('other-s', 'other')):
        db.insert('sections', {'id': sid, 'project_id': pid, 'title': '独立二级主题 ' + sid, 'outline_group_id': 'g',
                              'outline_group_title': '一级分类', 'ordinal': 0, 'content': '原有正文，数字 123，名称不变。\n\n|列1|列2|\n|---|---|\n|甲|乙|',
                              'status': 'approved', 'user_edited': 1, 'created_at': now, 'updated_at': now})
    yield monkeypatch
    assert not workflow.EDITING


def new(title='接口管理', content='人工产品模块正文。', scope='general'):
    return modules.create({'title': title, 'content': content, 'scope': scope})


def append(*ids, sid='s', request='append-1'):
    plan = modules.preview(sid, list(ids))
    return modules.apply(sid, list(ids), plan['revision'], request, True)


def row(sid='s'):
    return db.one('SELECT * FROM sections WHERE id=?', (sid,))


def project(pid='p'):
    return db.one('SELECT * FROM projects WHERE id=?', (pid,))


def test_schema_and_crud_are_independent_of_enterprise_facts(isolated):
    before_projects = db.all('SELECT * FROM projects ORDER BY id')
    before_sections = db.all('SELECT * FROM sections ORDER BY id')
    with db.connect() as conn:
        modules.initialize(conn)
    created = new(content='短')
    assert created['version'] == 1 and created['content'] == '短'
    assert created['metadata'] == {'source_kind': 'user_authored', 'enterprise_fact': False}
    assert modules.list_modules()['modules'] == [created]
    assert modules.get_module(created['id']) == created
    assert not db.all('SELECT * FROM documents') and not db.all('SELECT * FROM chunks')
    assert db.all('SELECT * FROM projects ORDER BY id') == before_projects
    assert db.all('SELECT * FROM sections ORDER BY id') == before_sections


def test_mutations_use_one_non_reentrant_admission_lock(isolated):
    import threading
    class DetectNestedLock:
        def __init__(self): self.lock = threading.Lock()
        def __enter__(self):
            assert self.lock.acquire(timeout=0.1), 'Nested non-reentrant lock would deadlock'
            return self
        def __exit__(self, *args): self.lock.release()
    lock = DetectNestedLock()
    isolated.setattr(workflow, 'JOB_LOCK', lock)
    a = new()
    result = append(a['id'])
    modules.undo(result['snapshot_id'], True)
    modules.update(a['id'], a['revision'], {'title': '新标题'})
    assert not workflow.EDITING


@pytest.mark.parametrize('values', [
    {'title': '', 'content': '正文'}, {'title': 'x' * 161, 'content': '正文'},
    {'title': '名称', 'content': ''}, {'title': '名称', 'content': ' \n '},
    {'title': '名称', 'content': 'x' * 250001}, {'title': '名称', 'content': '正文', 'scope': 'tender'},
    {'title': '名称', 'content': '正文', 'approved': True},
])
def test_crud_rejects_invalid_fields_without_writes(isolated, values):
    with pytest.raises(ValueError): modules.create(values)
    assert not modules.list_modules()['modules']


def test_noop_update_keeps_version_and_optimistic_save_rejects_stale(isolated):
    module = new(content='  正文原字节\n')
    unchanged = modules.update(module['id'], module['revision'], {'content': module['content']})
    assert unchanged == module
    updated = modules.update(module['id'], module['revision'], {'title': '新名称'})
    assert updated['version'] == 2 and updated['content'] == module['content']
    with pytest.raises(ValueError, match='修改'): modules.update(module['id'], module['revision'], {'content': '旧页面覆盖'})
    assert modules.get_module(module['id']) == updated


def test_delete_is_soft_confirmed_versioned_and_repeated_safe(isolated):
    module = new()
    with pytest.raises(ValueError, match='确认'): modules.delete(module['id'], module['revision'])
    with pytest.raises(ValueError, match='变化'): modules.delete(module['id'], 'wrong', True)
    assert modules.delete(module['id'], module['revision'], True)['deleted']
    assert modules.delete(module['id'], module['revision'], True)['already_deleted']
    assert not modules.list_modules()['modules']
    deleted = modules.list_modules(True)['modules'][0]
    assert deleted['content'] == module['content'] and deleted['version'] == 2 and deleted['deleted_at']
    with pytest.raises(ValueError, match='删除'): modules.get_module(module['id'])


def test_preview_is_readonly_and_exact_prefix_order_no_heading_injection(isolated):
    a = new('模块标题不能自动写入', '  第一素材正文。\n|a|b|\n|---|---|\n|1|2|\n')
    b = new('第二模块标题', '第二素材正文 [E:unknown]。')
    before = row()
    original_meta = project()['metadata']
    plan = modules.preview('s', [b['id'], a['id']])
    assert plan['before'] == before['content']
    assert plan['after'] == before['content'] + '\n\n' + b['content'] + '\n\n' + a['content']
    assert a['title'] not in plan['after'] and b['title'] not in plan['after']
    assert plan['appended_count'] == 2 and plan['skipped_count'] == 0
    assert row() == before and project()['metadata'] == original_meta
    assert not db.all('SELECT * FROM project_snapshots')


def test_append_saves_one_draft_and_leaves_every_other_business_record(isolated):
    now = db.now()
    db.insert('requirements', {'id': 'r', 'project_id': 'p', 'title': '真实要求', 'text': '采购实际要求', 'response': '已人工核对响应', 'status': 'confirmed', 'created_at': now, 'updated_at': now})
    db.insert('reviews', {'id': 'review', 'project_id': 'p', 'fingerprint': 'old', 'status': 'complete', 'findings': [{'verdict': 'contradiction', 'section_id': 's'}], 'created_at': now})
    db.insert('checks', {'id': 'check', 'project_id': 'p', 'code': 'real', 'severity': 'error', 'message': '真实待办', 'created_at': now})
    db.insert('exports', {'id': 'export', 'project_id': 'p', 'name': '旧导出', 'path': 'unused.docx', 'format': 'docx', 'created_at': now})
    metadata = {'content_todos': [{'id': 'todo', 'section_ids': ['s'], 'message': '真实附件未提供'}],
                'proposal_section_results': {'s': {'revision': 'old', 'responses': ['old']}}, 'other_metadata': {'keep': True}}
    db.update('projects', 'p', {'metadata': metadata})
    tables = {table: db.all('SELECT * FROM ' + table) for table in ('requirements', 'reviews', 'checks', 'exports')}
    other_rows = [row('s2'), row('other-s')]
    before = row()
    a = new(content='自定义内容不是企业事实证据。')
    result = append(a['id'])
    assert result['appended_count'] == 1 and result['section']['status'] == 'draft'
    assert row()['content'] == before['content'] + '\n\n' + a['content']
    assert row()['id'] == before['id'] and row()['requirement_ids'] == before['requirement_ids']
    assert row()['title'] == before['title'] and row()['outline_group_id'] == before['outline_group_id']
    assert [row('s2'), row('other-s')] == other_rows
    for table, rows in tables.items(): assert db.all('SELECT * FROM ' + table) == rows
    after_meta = project()['metadata']
    assert {k: v for k, v in after_meta.items() if k != modules.SOURCE_KEY} == metadata
    source = after_meta[modules.SOURCE_KEY]['s'][0]
    assert source['module_id'] == a['id'] and source['version'] == 1
    assert source['content'] == a['content'] and source['enterprise_fact'] is False
    assert source['content_sha256'] == hashlib.sha256(a['content'].encode()).hexdigest()


def test_idempotency_and_same_version_skip_even_after_restart_or_delete(isolated):
    a, b = new('a', '正文a'), new('b', '正文b')
    plan = modules.preview('s', [a['id']])
    first = modules.apply('s', [a['id']], plan['revision'], 'same', True)
    second = modules.apply('s', [a['id']], plan['revision'], 'same', True)
    assert second == first and len(db.all('SELECT * FROM project_snapshots')) == 1
    after = row()
    mixed = append(a['id'], b['id'], request='mixed')
    assert mixed['appended_count'] == 1 and mixed['skipped_count'] == 1
    assert row()['content'] == after['content'] + '\n\n' + b['content']
    noop_before = row()
    noop = append(a['id'], b['id'], request='nochange')
    assert noop['appended_count'] == 0 and row() == noop_before
    with pytest.raises(ValueError, match='操作编号'): modules.apply('s', [b['id']], plan['revision'], 'same', True)
    modules.delete(a['id'], a['revision'], True)
    assert modules.apply('s', [a['id']], plan['revision'], 'same', True) == first
    assert row() == noop_before


@pytest.mark.parametrize('change', ['body', 'approval', 'module', 'scope', 'delete', 'domain', 'sources'])
def test_preview_conflict_never_overwrites_new_content_even_with_rules_disabled(isolated, change):
    a = new()
    plan = modules.preview('s', [a['id']])
    db.set_setting('review_rules', {'operation_conflict': False, 'operation_revision': False})
    if change == 'body': db.update('sections', 's', {'content': '后续修改正文'})
    elif change == 'approval': db.update('sections', 's', {'status': 'draft'})
    elif change == 'module': modules.update(a['id'], a['revision'], {'content': '后续新素材'})
    elif change == 'scope': modules.update(a['id'], a['revision'], {'scope': 'expense'})
    elif change == 'delete': modules.delete(a['id'], a['revision'], True)
    elif change == 'domain': db.update('projects', 'p', {'domain': 'expense'})
    elif change == 'sources': db.update('projects', 'p', {'metadata': {modules.SOURCE_KEY: {'s': [{'module_id': 'other', 'version': 1}]}}})
    before = row()
    with pytest.raises(ValueError): modules.apply('s', [a['id']], plan['revision'], 'stale', True)
    assert row() == before and not db.all('SELECT * FROM project_snapshots')


@pytest.mark.parametrize('ids', [[], ['missing'], ['missing', 'missing'], [1], ['a'] * 101])
def test_explicit_valid_unique_selection_required(isolated, ids):
    with pytest.raises(ValueError): modules.preview('s', ids)


def test_scope_and_inactive_section_are_hard_boundaries(isolated):
    a = new(scope='expense')
    with pytest.raises(ValueError, match='产品范围'): modules.preview('s', [a['id']])
    b = new()
    db.update('projects', 'p', {'metadata': {'outline_selection': {'confirmed': True, 'groups': [{'id': 'g', 'enabled': False, 'section_ids': ['s', 's2']}], 'fixed_groups': []}}})
    with pytest.raises(ValueError, match='目录'): modules.preview('s', [b['id']])


@pytest.mark.parametrize('job_project', ['p', None])
@pytest.mark.parametrize('job_state', ['queued', 'running'])
def test_jobs_prevent_apply_even_if_optional_conflict_review_disabled(isolated, job_project, job_state):
    a = new()
    plan = modules.preview('s', [a['id']])
    db.set_setting('review_rules', {'operation_conflict': False})
    db.insert('jobs', {'id': 'busy', 'project_id': job_project, 'mode': 'generate', 'status': job_state, 'created_at': db.now()})
    before = row()
    with pytest.raises(ValueError, match='任务'): modules.apply('s', [a['id']], plan['revision'], 'busy', True)
    with pytest.raises(ValueError, match='任务'): modules.create({'title': 'x', 'content': 'y'})
    assert row() == before


def test_other_project_job_does_not_block_target_section_append(isolated):
    a = new()
    db.insert('jobs', {'id': 'other-busy', 'project_id': 'other', 'mode': 'generate', 'status': 'running', 'created_at': db.now()})
    assert append(a['id'])['appended_count'] == 1


def test_undo_restores_only_target_and_preserves_later_other_operations(isolated):
    a = new()
    original = row()
    result = append(a['id'])
    metadata = project()['metadata']
    metadata['new_unrelated_key'] = {'user': 'later'}
    metadata[modules.SOURCE_KEY]['s2'] = [{'module_id': 'different', 'version': 2}]
    db.update('projects', 'p', {'metadata': metadata})
    db.update('sections', 's2', {'content': '另节后续正文'})
    modules.update(a['id'], a['revision'], {'content': '模块新版本'})
    assert modules.undo(result['snapshot_id'], True)['status'] == 'restored'
    restored = row()
    assert restored['content'] == original['content'] and restored['evidence_ids'] == original['evidence_ids']
    assert restored['status'] == 'draft' and restored['user_edited'] == 1
    assert row('s2')['content'] == '另节后续正文'
    metadata = project()['metadata']
    assert metadata['new_unrelated_key'] == {'user': 'later'}
    assert metadata[modules.SOURCE_KEY] == {'s2': [{'module_id': 'different', 'version': 2}]}
    assert modules.get_module(a['id'])['version'] == 2
    assert modules.undo(result['snapshot_id'], True)['status'] == 'already_restored'


@pytest.mark.parametrize('change', ['body', 'approval', 'sources', 'second_append'])
def test_undo_never_overwrites_later_target_decision_even_if_disabled(isolated, change):
    a, b = new('a', '正文a'), new('b', '正文b')
    result = append(a['id'])
    db.set_setting('review_rules', {'restore_conflict': False, 'operation_conflict': False})
    if change == 'body': db.update('sections', 's', {'content': '后续人工修改'})
    elif change == 'approval': db.update('sections', 's', {'status': 'approved'})
    elif change == 'sources':
        metadata = project()['metadata']
        metadata[modules.SOURCE_KEY]['s'][0]['title'] = '后续修改来源'
        db.update('projects', 'p', {'metadata': metadata})
    else: append(b['id'], request='second')
    before = row()
    with pytest.raises(ValueError, match='后续'): modules.undo(result['snapshot_id'], True)
    assert row() == before


def test_fixed_source_context_survives_module_edit_and_delete(isolated):
    a = new(content='用户选择的固定版本，尚非已核验企业事实。')
    append(a['id'])
    context = modules.source_context(row(), project())
    assert context[0]['content'] == a['content'] and context[0]['source_kind'] == 'user_authored'
    b = modules.update(a['id'], a['revision'], {'content': '活动库的新版本'})
    modules.delete(a['id'], b['revision'], True)
    assert modules.source_context(row(), project()) == context
    tampered = project()
    tampered['metadata'][modules.SOURCE_KEY]['s'][0]['content'] = '快照被错误改写'
    with pytest.raises(ValueError, match='快照'): modules.source_context(row(), tampered)


def test_unchosen_and_fake_citations_cannot_become_evidence(isolated):
    a = new(content='模块引用 [E:does-not-exist]，不据此创建证据。')
    result = append(a['id'])
    assert '[E:does-not-exist]' in result['section']['content']
    assert result['section']['evidence_ids'] == []
    assert not db.all('SELECT * FROM documents') and not db.all('SELECT * FROM chunks')


def test_original_registered_evidence_can_be_preserved_without_inventing_new(isolated):
    now = db.now()
    db.insert('documents', {'id': 'e-doc', 'name': '企业官方说明', 'path': 'unused.md', 'source_type': 'knowledge', 'sha256': 'a' * 64,
                            'status': 'approved', 'scope': 'archive', 'parse_status': 'ready', 'created_at': now, 'updated_at': now})
    db.insert('chunks', {'id': 'e-chunk', 'document_id': 'e-doc', 'ordinal': 0, 'locator': '段落1', 'text': '系统支持档案数据查询。'})
    a = new(content='系统支持档案数据查询。[E:e-chunk]')
    result = append(a['id'])
    assert result['section']['evidence_ids'] == ['e-chunk']
    assert len(db.all('SELECT * FROM chunks')) == 1


def test_maximum_combined_body_checks_before_writing(isolated):
    a = new(content='x' * 250000)
    db.update('sections', 's', {'content': 'y' * 250000})
    with pytest.raises(ValueError, match='500000'): modules.preview('s', [a['id']])
    assert row()['content'] == 'y' * 250000


def test_explicit_confirmation_for_append_and_undo(isolated):
    a = new()
    plan = modules.preview('s', [a['id']])
    with pytest.raises(ValueError, match='确认'): modules.apply('s', [a['id']], plan['revision'], 'x')
    result = append(a['id'])
    with pytest.raises(ValueError, match='确认'): modules.undo(result['snapshot_id'])
