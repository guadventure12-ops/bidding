"""Explicit H1/H2 compilation, isolated SQLite and no model/network calls."""
import copy
import json
import uuid

import pytest

from app import db, workflow, provider, outline_library as library, compilation_outline as outline, outline_sources, proposal_runtime


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setenv('MX_TESTING', '1'); monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    monkeypatch.setattr(provider, 'key_configured', lambda: False)
    monkeypatch.setattr(provider, 'chat_json', lambda *a, **k: pytest.fail('No model in explicit H2 save'))
    db.init()
    now = db.now()
    for pid in ('p', 'other'):
        db.insert('projects', {'id': pid, 'name': '合成目录项目', 'domain': 'archive', 'created_at': now, 'updated_at': now})
    db.insert('documents', {'id': 'd', 'project_id': 'p', 'name': '合成招标文件', 'path': 'unused', 'sha256': 'a' * 64,
                            'source_type': 'tender', 'parse_status': 'ready', 'created_at': now, 'updated_at': now})
    db.insert('chunks', {'id': 'c', 'document_id': 'd', 'text': '系统应支持档案查询和授权检索。', 'ordinal': 0, 'locator': '段落1'})
    for index in range(2):
        db.insert('requirements', {'id': f'r{index}', 'project_id': 'p', 'document_id': 'd', 'chunk_id': 'c',
                                  'title': '档案查询', 'text': '系统应支持档案查询和授权检索。', 'quote': '系统应支持档案查询和授权检索。',
                                  'category': 'technical', 'response': '原有响应', 'status': 'confirmed', 'created_at': now, 'updated_at': now})
    source = {'mode': 'explicit', 'explicit_count': 1, 'scoring_count': 0, 'completeness_reason': '明确规定两级目录', 'notices': [],
              'groups': [{'id': 'source-h1', 'title': '系统集成方案', 'origin': 'tender',
                          'source_refs': [{'document_id': 'd', 'chunk_id': 'c', 'quote': '系统集成方案', 'locator': '目录段落'}],
                          'children': [{'id': 'source-child-a', 'title': '接口范围', 'origin': 'tender',
                                        'source_refs': [{'document_id': 'd', 'chunk_id': 'c', 'quote': '接口范围', 'locator': '目录子项1'}], 'requirement_ids': ['r0']},
                                       {'id': 'source-child-b', 'title': '授权检索', 'origin': 'tender',
                                        'source_refs': [{'document_id': 'd', 'chunk_id': 'c', 'quote': '授权检索', 'locator': '目录子项2'}], 'requirement_ids': ['r1']}]}]}
    monkeypatch.setattr(outline_sources, 'recommend', lambda *a, **k: copy.deepcopy(source))
    return monkeypatch, source


def old_section(sid='old', title='已有独立主题', gid='old-group', reqs=('r0',), pid='p', ordinal=0):
    now = db.now()
    db.insert('sections', {'id': sid, 'project_id': pid, 'ordinal': ordinal, 'title': title,
                          'outline_group_id': gid, 'outline_group_title': '系统集成方案', 'legacy_title': '',
                          'content': '已有正文，数字 123 与名称 ABC 不改变。\n\n|项|值|\n|---|---|\n|甲|乙|',
                          'status': 'approved', 'user_edited': 1, 'requirement_ids': list(reqs), 'created_at': now, 'updated_at': now})


def row(sid): return db.one('SELECT * FROM sections WHERE id=?', (sid,))
def project(): return db.one('SELECT * FROM projects WHERE id="p"')


def payload(plan, enabled=None):
    return [{'id': g['id'], 'enabled': g['enabled'] if enabled is None else g['id'] in enabled,
             'children': [{k: child[k] for k in ('id', 'title', 'enabled')} for child in g['children']]}
            for g in plan['groups']]


def save(plan, groups=None):
    return outline.save('p', plan['revision'], payload(plan) if groups is None else groups, True)


def new_child(title='手工二级'):
    return {'id': 'new-' + str(uuid.uuid4()), 'title': title, 'enabled': True}


def test_library_reads_old_children_default_without_revising_or_writing(isolated):
    old = library.defaults(); old['version'] = 'outline-library-1'
    for item in old['modules']: item.pop('children', None)
    db.set_setting(library.KEY, old)
    raw = db.one('SELECT value FROM settings WHERE key=?', (library.KEY,))['value']
    read = library.get_library()
    assert all(m['children'] == [] for m in read['modules'])
    assert read['version'] == 'outline-library-1' and read['revision'] == library.digest(old)
    assert db.one('SELECT value FROM settings WHERE key=?', (library.KEY,))['value'] == raw
    children = [{'id': 'leaf-a', 'title': '业务流程'}, {'id': 'leaf-b', 'title': '异常处理'}]
    read['modules'][0]['children'] = children
    revised = library.save_library(read['revision'], read['modules'], read['presets'])
    assert revised['version'] == library.VERSION and revised['modules'][0]['children'] == children
    assert library.save_library(revised['revision'], revised['modules'], revised['presets']) == revised


@pytest.mark.parametrize('children', [
    [{'id': 'x', 'title': '重复'}, {'id': 'y', 'title': '重复'}],
    [{'id': 'same', 'title': '甲'}, {'id': 'same', 'title': '乙'}],
    [{'id': 'x', 'title': '一、带编号'}], [{'id': '!', 'title': '无效ID'}],
    [{'id': str(i), 'title': f'标题{i}'} for i in range(101)],
])
def test_library_child_validation_keeps_original(isolated, children):
    before = library.get_library(); changed = copy.deepcopy(before)
    changed['modules'][0]['children'] = children
    with pytest.raises(ValueError): library.save_library(before['revision'], changed['modules'], changed['presets'])
    assert library.get_library() == before


def test_new_source_h2_saved_as_empty_without_ai_then_projection_and_noop(isolated):
    plan = outline.preview('p')
    assert plan['source_mode'] == 'explicit' and plan['source_count'] == 1
    source = next(g for g in plan['groups'] if g['origin'] == 'tender')
    assert [c['title'] for c in source['children']] == ['接口范围', '授权检索']
    assert all(c['title_locked'] and c['section_id'] is None for c in source['children'])
    assert not plan['generation']['pending_group_ids']
    assert all(not g['enabled'] for g in plan['groups'] if g['origin'] == 'generic')
    saved = save(plan)
    selected = next(g for g in saved['groups'] if g['id'] == source['id'])
    assert selected['children_managed'] and len(selected['section_ids']) == 2
    assert all(c['section_id'] for c in selected['children'])
    for child in selected['children']:
        section = row(child['section_id'])
        assert section['content'] == '' and section['status'] == 'draft' and section['user_edited'] == 0
        spec = outline.specification(project(), section)
        assert spec['secondary_defined'] and spec['source_refs'] and spec['title'] == child['title']
    assert {s['section_id'] for s in proposal_runtime.blueprint(project())['sections']} == {s['id'] for s in db.all('SELECT * FROM sections WHERE project_id="p"')}
    outline.ensure_generation_ready('p')
    before = db.all('SELECT * FROM sections WHERE project_id="p" ORDER BY ordinal,id')
    repeated = save(saved)
    assert repeated['changed'] is False and repeated['snapshot_id'] is None
    assert db.all('SELECT * FROM sections WHERE project_id="p" ORDER BY ordinal,id') == before
    with pytest.raises(ValueError, match='没有需要'): outline.admit('p', repeated['revision'], True)


def test_existing_body_id_approval_and_owned_requirements_retained_when_adding_suggestions(isolated):
    old_section(); old_section('other-section', pid='other', reqs=())
    old, other = row('old'), row('other-section')
    reqs = db.all('SELECT * FROM requirements ORDER BY id')
    now = db.now()
    db.insert('reviews', {'id': 'audit', 'project_id': 'p', 'fingerprint': 'old', 'status': 'complete', 'findings': ['historical'], 'created_at': now})
    plan = outline.preview('p'); group = next(g for g in plan['groups'] if g['id'] == 'old-group')
    assert len(group['children']) == 1 and group['children'][0]['origin'] == 'existing'
    assert len(group['suggested_children']) == 2
    selected = payload(plan)
    target = next(g for g in selected if g['id'] == group['id'])
    target['enabled'] = True
    target['children'] += [{k: c[k] for k in ('id', 'title', 'enabled')} for c in group['suggested_children']]
    saved = save(plan, selected)
    assert row('old') == old and row('other-section') == other
    assert db.all('SELECT * FROM requirements ORDER BY id') == reqs
    assert db.one('SELECT * FROM reviews WHERE id="audit"')['findings'] == ['historical']
    updated_group = next(g for g in saved['groups'] if g['id'] == group['id'])
    new = [row(c['section_id']) for c in updated_group['children'] if c['section_id'] != 'old']
    assert len(new) == 2 and all(not r['content'] and r['status'] == 'draft' for r in new)
    assert all('r0' not in r['requirement_ids'] for r in new)
    assert next(r for r in new if r['title'] == '授权检索')['requirement_ids'] == ['r1']
    assert {r['id'] for r in outline.projection(project(), db.all('SELECT * FROM sections WHERE project_id="p"'))['active']} == {'old', *[r['id'] for r in new]}


def test_existing_exact_title_is_not_duplicated_as_suggestion_after_rename(isolated):
    old_section(title='接口范围')
    plan = outline.preview('p'); group = next(g for g in plan['groups'] if g['id'] == 'old-group')
    assert [c['title'] for c in group['suggested_children']] == ['授权检索']
    source_refs = group['children'][0]['source_refs']
    selected = payload(plan); chosen = next(g for g in selected if g['id'] == 'old-group')
    chosen['enabled'] = True; chosen['children'][0]['title'] = '人工更准确的接口主题'
    saved = save(plan, selected)
    current = next(g for g in saved['groups'] if g['id'] == 'old-group')
    assert current['children'][0]['section_id'] == 'old' and current['children'][0]['source_refs'] == source_refs
    assert [c['title'] for c in current['suggested_children']] == ['授权检索']
    assert row('old')['status'] == 'draft'


def test_title_rename_resets_only_target_approval_and_undo_restores_exact_rows(isolated):
    old_section(); old_section('old2', title='另一个主题', reqs=('r1',), ordinal=1)
    before = db.all('SELECT * FROM sections WHERE project_id="p" ORDER BY ordinal,id')
    plan = outline.preview('p'); selected = payload(plan)
    group = next(g for g in selected if g['id'] == 'old-group'); group['enabled'] = True
    group['children'].reverse(); group['children'][1]['title'] = '人工修订主题'
    group['children'][0]['enabled'] = False
    saved = save(plan, selected)
    assert saved['renamed_sections'] == 1
    assert row('old')['content'] == before[0]['content'] and row('old')['status'] == 'draft'
    assert row('old2')['status'] == 'approved' and row('old2')['updated_at'] == before[1]['updated_at']
    projected = outline.projection(project(), db.all('SELECT * FROM sections WHERE project_id="p" ORDER BY ordinal,id'))
    assert [s['id'] for s in projected['active']] == ['old'] and [s['id'] for s in projected['retained']] == ['old2']
    assert outline.undo(saved['snapshot_id'], True)['status'] == 'restored'
    assert db.all('SELECT * FROM sections WHERE project_id="p" ORDER BY ordinal,id') == before
    assert outline.undo(saved['snapshot_id'], True)['status'] == 'already_restored'


def test_custom_logical_id_survives_rename_reorder_disable_reenable(isolated):
    old_section(reqs=())
    plan = outline.preview('p'); selected = payload(plan)
    chosen = next(g for g in selected if g['id'] == 'old-group'); chosen['enabled'] = True
    custom = new_child('补充接口流程'); chosen['children'].append(custom)
    saved = save(plan, selected)
    group = next(g for g in saved['groups'] if g['id'] == 'old-group')
    identity = next(c['section_id'] for c in group['children'] if c['id'] == custom['id'])
    assert row(identity)['requirement_ids'] == []
    assert outline.specification(project(), row(identity))['user_selected_module']
    selected = payload(saved); chosen = next(g for g in selected if g['id'] == 'old-group')
    chosen['children'].reverse(); chosen['children'][0].update(title='新的用户主题', enabled=False)
    again = save(saved, selected)
    assert row(identity)['title'] == '新的用户主题' and not outline.is_enabled(project(), row(identity))
    selected = payload(again); chosen = next(g for g in selected if g['id'] == 'old-group'); chosen['children'][0]['enabled'] = True
    final = save(again, selected)
    assert next(c['section_id'] for g in final['groups'] for c in g['children'] if c['id'] == custom['id']) == identity
    assert outline.is_enabled(project(), row(identity))


@pytest.mark.parametrize('attack', ['omit_existing', 'omit_source', 'fake_reference', 'fake_requirement', 'foreign_id', 'cross_parent', 'duplicate_id', 'locked_title', 'duplicate_title'])
def test_secondary_payload_scope_and_source_cannot_be_forged(isolated, attack):
    if attack not in ('omit_source', 'locked_title'): old_section()
    plan = outline.preview('p'); selected = payload(plan)
    chosen = next(g for g in selected if any(x['id'] == g['id'] and x['origin'] == 'tender' for x in plan['groups']))
    chosen['enabled'] = True
    if attack in ('omit_existing', 'omit_source'): chosen['children'].pop(0)
    elif attack == 'fake_reference': chosen['children'][0]['source_refs'] = [{'quote': '伪造'}]
    elif attack == 'fake_requirement': chosen['children'][0]['requirement_ids'] = ['outside']
    elif attack == 'foreign_id': chosen['children'].append({'id': 'source-other', 'title': '外部子项', 'enabled': True})
    elif attack == 'cross_parent':
        target = next(g for g in selected if g['id'] != chosen['id']); target['children'].append(chosen['children'][0]); target['enabled'] = True
    elif attack == 'duplicate_id': chosen['children'].append(copy.deepcopy(chosen['children'][0]))
    elif attack == 'locked_title': chosen['children'][0]['title'] = '模型自改名字'
    else: chosen['children'].append(new_child(chosen['children'][0]['title']))
    before = db.all('SELECT * FROM sections ORDER BY id')
    with pytest.raises(ValueError): save(plan, selected)
    assert db.all('SELECT * FROM sections ORDER BY id') == before
    assert not db.all('SELECT * FROM project_snapshots')


def test_library_freeze_and_old_h1_payload_do_not_silently_create_h2(isolated):
    old_section()
    catalog = library.get_library()
    catalog['modules'][0]['children'] = [{'id': 'library-child', 'title': '旧库二级'}]
    catalog = library.save_library(catalog['revision'], catalog['modules'], catalog['presets'])
    plan = outline.preview('p')
    legacy = [{'id': g['id'], 'enabled': g['id'] == 'old-group'} for g in plan['groups']]
    saved = save(plan, legacy)
    assert len(db.all('SELECT * FROM sections WHERE project_id="p"')) == 1
    assert all(not g.get('children_managed') for g in saved['groups'])
    newer = copy.deepcopy(catalog); newer['modules'][0]['children'][0]['title'] = '最新库二级不应被旧项目采用'
    library.save_library(catalog['revision'], newer['modules'], newer['presets'])
    again = outline.preview('p')
    target = next(g for g in again['groups'] if g['module_id'] == catalog['modules'][0]['id'])
    assert target['children'][0]['title'] == '旧库二级'
    assert again['library_revision'] == catalog['revision']


def test_disabled_new_children_are_not_created_until_selected(isolated):
    plan = outline.preview('p'); selected = payload(plan)
    source = next(g for g in selected if any(x['id'] == g['id'] and x['origin'] == 'tender' for x in plan['groups']))
    source['children'][1]['enabled'] = False
    saved = save(plan, selected)
    group = next(g for g in saved['groups'] if g['id'] == source['id'])
    assert group['children'][0]['section_id'] and group['children'][1]['section_id'] is None
    second = payload(saved); next(g for g in second if g['id'] == source['id'])['children'][1]['enabled'] = True
    final = save(saved, second)
    assert next(g for g in final['groups'] if g['id'] == source['id'])['children'][1]['section_id']


@pytest.mark.parametrize('change', ['content', 'status', 'requirements', 'source', 'busy'])
def test_strict_revision_and_idle_cannot_be_bypassed_by_review_toggles(isolated, change):
    old_section(); plan = outline.preview('p')
    db.set_setting('review_rules', {'operation_revision': False, 'operation_conflict': False})
    if change == 'content': db.update('sections', 'old', {'content': '后续正文'})
    elif change == 'status': db.update('sections', 'old', {'status': 'draft'})
    elif change == 'requirements': db.update('requirements', 'r0', {'text': '新采购要求'})
    elif change == 'source': db.update('documents', 'd', {'sha256': 'c' * 64})
    else: db.insert('jobs', {'id': 'busy', 'project_id': 'p', 'mode': 'generate', 'status': 'running', 'created_at': db.now()})
    before = db.all('SELECT * FROM sections ORDER BY id')
    with pytest.raises(ValueError): save(plan)
    assert db.all('SELECT * FROM sections ORDER BY id') == before


def test_new_empty_chapter_undo_conflict_never_deletes_later_materials(isolated):
    saved = save(outline.preview('p'))
    sid = next(c['section_id'] for g in saved['groups'] for c in g['children'] if c['section_id'])
    db.update('sections', sid, {'content': '后来选择产品功能模块后形成的正文', 'user_edited': 1})
    db.set_setting('review_rules', {'restore_conflict': False})
    result = outline.undo(saved['snapshot_id'], True)
    assert result['status'] == 'conflict' and row(sid)['content'] == '后来选择产品功能模块后形成的正文'


def test_new_empty_sections_and_blueprint_are_removed_only_by_unchanged_operation_undo(isolated):
    before = project()['metadata']
    saved = save(outline.preview('p'))
    assert proposal_runtime.blueprint(project())
    assert outline.undo(saved['snapshot_id'], True)['status'] == 'restored'
    assert db.all('SELECT * FROM sections WHERE project_id="p"') == []
    assert project()['metadata'] == before


def test_scoring_strategy_keeps_full_quote_and_excludes_price_only_group(isolated):
    _, source = isolated
    source.update(mode='scoring', explicit_count=0, scoring_count=2, completeness_reason='依据第二列与第三列', notices=['报价计分独立核对'])
    group = source['groups'][0]; group.update(origin='scoring')
    for child in group['children']:
        child['origin'] = 'scoring'; child['source_text'] = '完整评分第三列正文，应保留评分细则原句与具体响应要求。'
        child['source_refs'][0]['quote'] = child['source_text']
    source['groups'].append({'id': 'price', 'title': '报价得分', 'origin': 'scoring', 'category': 'pricing', 'children': [], 'source_refs': []})
    plan = outline.preview('p')
    assert plan['source_mode'] == 'scoring' and plan['source_count'] == 0 and plan['scoring_count'] == 2
    assert not any(g['title'] == '报价得分' for g in plan['groups'])
    group = next(g for g in plan['groups'] if g['origin'] == 'scoring')
    assert group['children'][0]['source_text'] == source['groups'][0]['children'][0]['source_text']
    selected = payload(plan); target = next(g for g in selected if g['id'] == group['id']); target['children'][0]['title'] = '人工简化响应主题'
    saved = save(plan, selected)
    spec = outline.specification(project(), row(next(g for g in saved['groups'] if g['id'] == group['id'])['children'][0]['section_id']))
    assert any(ref['quote'] == source['groups'][0]['children'][0]['source_text'] for ref in spec['source_refs'])


def test_defined_source_suboutline_remains_below_h2(isolated):
    _, source = isolated
    source['groups'][0]['children'][0]['children'] = [{'id': 'source-h3', 'title': '三级规定', 'children': [{'id': 'source-h4', 'title': '四级规定'}]}]
    saved = save(outline.preview('p'))
    child = next(g for g in saved['groups'] if g['origin'] == 'tender')['children'][0]
    spec = outline.specification(project(), row(child['section_id']))
    assert spec['suboutline'] == [{'id': 'source-h3', 'title': '三级规定', 'children': [{'id': 'source-h4', 'title': '四级规定'}]}]
    assert len(next(g for g in saved['groups'] if g['origin'] == 'tender')['children']) == 2


def test_get_uses_actual_title_without_modifying_saved_child_metadata(isolated):
    old_section(); saved = save(outline.preview('p'))
    metadata = copy.deepcopy(project()['metadata'])
    db.update('sections', 'old', {'title': '外部编辑的新主题'})
    read = outline.preview('p')
    assert next(c for g in read['groups'] for c in g['children'] if c['section_id'] == 'old')['title'] == '外部编辑的新主题'
    assert project()['metadata'] == metadata


def test_unrelated_metadata_changed_after_preview_is_preserved(isolated):
    old_section(); plan = outline.preview('p')
    db.update('projects', 'p', {'metadata': {'user_notes': '后来新增的业务备注'}})
    save(plan)
    assert project()['metadata']['user_notes'] == '后来新增的业务备注'


@pytest.mark.parametrize('category', ['technical', 'format'])
def test_scoring_new_project_owns_actual_requirements_and_response_projection(isolated, category):
    _, source = isolated
    source.update(mode='scoring', explicit_count=0, scoring_count=1)
    source['groups'][0]['origin'] = 'scoring'
    for child in source['groups'][0]['children']: child['origin'] = 'scoring'
    for rid in ('r0', 'r1'):
        db.update('requirements', rid, {'category': category})
    saved = save(outline.preview('p'))
    group = next(g for g in saved['groups'] if g['origin'] == 'scoring')
    sections = [row(child['section_id']) for child in group['children']]
    assert [s['requirement_ids'] for s in sections] == [['r0'], ['r1']]
    p = project(); meta = p['metadata']
    for section in sections:
        result = {'responses': [{'requirement_id': rid, 'response': '本节独立已核对响应', 'evidence_ids': [], 'gap': False} for rid in section['requirement_ids']]}
        meta = proposal_runtime.record_result(meta, section, result)
    db.update('projects', 'p', {'metadata': meta})
    rows = db.all('SELECT * FROM sections WHERE project_id="p" ORDER BY ordinal,id')
    requirements = db.all('SELECT * FROM requirements WHERE project_id="p" ORDER BY id')
    responses, issues = proposal_runtime.project_responses(project(), rows, requirements)
    assert not issues
    assert [r['_proposal_section_id'] for r in responses] == [s['id'] for s in sections]
    assert all(r['response'] == '本节独立已核对响应' for r in responses)
    owned = [rid for s in rows for rid in s['requirement_ids']]
    assert len(owned) == len(set(owned))


@pytest.mark.parametrize('origin', ['scoring', 'generic'])
def test_legacy_h1_client_may_plan_unadopted_suggestions_without_managed_h2(isolated, origin):
    monkeypatch, source = isolated
    if origin == 'scoring':
        source.update(mode='partial', scoring_count=1)
        for child in source['groups'][0]['children']: child['origin'] = 'scoring'
    else:
        source.update(mode='empty', groups=[], explicit_count=0, scoring_count=0)
        catalog = library.get_library()
        catalog['modules'][0]['children'] = [{'id': 'generic-h2', 'title': '仅供选择的通用主题'}]
        library.save_library(catalog['revision'], catalog['modules'], catalog['presets'])
    plan = outline.preview('p')
    target = next(g for g in plan['groups'] if g['children'])
    chosen = [{'id': g['id'], 'enabled': g['id'] == target['id']} for g in plan['groups']]
    saved = outline.save('p', plan['revision'], chosen, True)
    assert db.all('SELECT * FROM sections WHERE project_id="p"') == []
    assert saved['generation']['pending_group_ids'] == [target['id']]
    group = next(g for g in saved['groups'] if g['id'] == target['id'])
    assert not group.get('children_managed') and all(c['section_id'] is None for c in group['children'])
    monkeypatch.setattr(provider, 'key_configured', lambda: True)
    calls = []
    def plan_model(system, prompt, **kwargs):
        data = json.loads(prompt); calls.append(data)
        return {'sections': [{'title': '旧客户端确认的独立规划主题',
                              'requirement_ids': [r['id'] for r in data['requirements']],
                              'suboutline': [{'title': '业务设计', 'children': [{'title': '处理流程'}]}]}]}
    monkeypatch.setattr(provider, 'chat_json', plan_model)
    admitted = outline.admit('p', saved['revision'], True)
    db.insert('jobs', {'id': 'legacy-plan', 'project_id': 'p', 'mode': 'outline', 'status': 'running', 'payload': admitted, 'created_at': db.now()})
    outcome = outline.run('legacy-plan', db.one('SELECT * FROM jobs WHERE id="legacy-plan"'))
    db.update('jobs', 'legacy-plan', {'status': 'succeeded'})
    assert len(calls) == 1 and outcome['results'][0]['status'] == 'planned'
    outline.ensure_generation_ready('p')
    assert not outline.preview('p')['generation']['pending_group_ids']
    assert any(s['title'] == '旧客户端确认的独立规划主题' for s in db.all('SELECT * FROM sections WHERE project_id="p"'))


def test_explicit_procurement_h2_remains_fixed_for_h1_only_client(isolated):
    plan = outline.preview('p')
    legacy = [{'id': g['id'], 'enabled': g['origin'] == 'tender'} for g in plan['groups']]
    saved = outline.save('p', plan['revision'], legacy, True)
    assert not saved['generation']['pending_group_ids']
    with pytest.raises(ValueError, match='保存一、二级目录'):
        outline.ensure_generation_ready('p')
    with pytest.raises(ValueError, match='没有需要'):
        outline.admit('p', saved['revision'], True)
    assert db.all('SELECT * FROM sections WHERE project_id="p"') == []


def test_managed_scoring_h2_never_returns_to_ai_planning(isolated):
    _, source = isolated
    source.update(mode='partial', scoring_count=1)
    for child in source['groups'][0]['children']: child['origin'] = 'scoring'
    saved = save(outline.preview('p'))
    assert not saved['generation']['pending_group_ids']
    outline.ensure_generation_ready('p')
    with pytest.raises(ValueError, match='没有需要'): outline.admit('p', saved['revision'], True)
    before = db.all('SELECT * FROM sections WHERE project_id="p" ORDER BY ordinal,id')
    # A later old client can toggle/reorder H1, but cannot drop adopted H2 scope.
    legacy = [{'id': g['id'], 'enabled': g['enabled']} for g in saved['groups']]
    again = outline.save('p', saved['revision'], legacy, True)
    assert db.all('SELECT * FROM sections WHERE project_id="p" ORDER BY ordinal,id') == before
    assert all(g.get('children_managed') for g in again['groups'])


@pytest.mark.parametrize('h1_origin', ['scoring', 'tender'])
def test_new_scoring_children_are_real_review_index_page_targets(isolated, h1_origin):
    from app import review_index, index_materials
    _, source = isolated
    source.update(mode='scoring' if h1_origin == 'scoring' else 'partial',
                  explicit_count=int(h1_origin == 'tender'), scoring_count=1)
    group = source['groups'][0]; group['origin'] = h1_origin
    description = '针对接口范围给出接口设计方案，并说明授权检索的处理流程。'
    for child in group['children']:
        child['origin'] = 'scoring'
        child['source_refs'] = [{'document_id': 'd', 'chunk_id': 'score-row',
                                 'locator': '正文 / 表格 3 / 第 3 行', 'quote': description, 'column': 3}]
    for identity, ordinal, locator, cells in [
        ('score-header', 1, '正文 / 表格 3 / 第 2 行', ['序号', '评分因素', '评分标准', '分值']),
        ('score-row', 2, '正文 / 表格 3 / 第 3 行', ['1', '系统集成方案', description, '5']),
    ]:
        db.insert('chunks', {'id': identity, 'document_id': 'd', 'ordinal': ordinal, 'kind': 'table_row',
                             'locator': locator, 'text': ' | '.join(cells),
                             'metadata': {'table': {'index': 3, 'cells': cells, 'row': ordinal + 1}}})
    before_requirements = db.all('SELECT * FROM requirements ORDER BY id')
    saved = save(outline.preview('p'))
    chosen = next(g for g in saved['groups'] if g['origin'] == h1_origin)
    section_ids = [c['section_id'] for c in chosen['children']]
    plan = proposal_runtime.blueprint(project())
    assert len(plan['score_factors']) == 1
    factor = plan['score_factors'][0]
    for identity in section_ids:
        spec = next(s for s in plan['sections'] if s['section_id'] == identity)
        assert spec['score_factors'] == [factor]
        db.update('sections', identity, {'content': '接口范围按照实际业务系统列明。授权检索采用业务角色与数据范围结合的方式进行访问管理。', 'user_edited': 1})
    rows = db.all('SELECT * FROM sections WHERE project_id="p" ORDER BY ordinal,id')
    assert all(index_materials.status(s, next(spec for spec in plan['sections'] if spec['section_id'] == s['id']), project())['ready']
               for s in rows if s['id'] in section_ids)
    targets, _ = review_index._owners(factor, plan, rows, project())
    assert targets == section_ids
    assert review_index._pages(targets) == '\n'.join('[[PAGE:' + sid + ']]' for sid in section_ids)
    assert db.all('SELECT * FROM requirements ORDER BY id') == before_requirements


@pytest.mark.parametrize('case,expected', [
    ('chunk', True), ('locator', True), ('split_locator', True), ('hyphen_split', True),
    ('wrong_document', False), ('wrong_row', False), ('missing_source', False),
    ('only_title', False), ('group_ref_only', False), ('generic', False), ('custom', False),
])
def test_score_factor_inheritance_requires_exact_child_source_not_title(case, expected):
    factor = {'number': '1', 'title': '系统集成方案', 'points': 5, 'text': '评分原文',
              'source': {'document_id': 'd', 'chunk_id': 'score-row', 'locator': '正文 / 表格 3 / 第 3 行'}}
    ref = copy.deepcopy(factor['source'])
    if case == 'chunk': ref['locator'] = '另一种兼容定位写法'
    elif case == 'locator': ref['chunk_id'] = 'reparsed-chunk'
    elif case in ('split_locator', 'hyphen_split'):
        ref.update(chunk_id='split-chunk', locator=ref['locator'] + ('（字符 5001–8000）' if case == 'split_locator' else '（字符 5001-8000）'))
    elif case == 'wrong_document': ref['document_id'] = 'other-document'
    elif case == 'wrong_row': ref.update(chunk_id='other-row', locator='正文 / 表格 3 / 第 4 行')
    elif case == 'missing_source': factor['source'] = {}
    group = {'id': 'g', 'title': '系统集成方案', 'source_refs': [copy.deepcopy(factor.get('source', {}))]}
    child = {'id': 'c', 'title': '响应主题', 'origin': case if case in ('generic', 'custom') else 'scoring',
             'source_refs': [] if case in ('only_title', 'group_ref_only') else [ref]}
    base = {'score_factors': [factor], 'sections': [{'group_title': group['title'], 'title': child['title'], 'score_factors': [factor]}]}
    spec = outline._manual_spec({'id': 'p'}, group, child, 'new-s', [], base)
    assert spec['score_factors'] == ([factor] if expected else [])


def test_explicit_tender_matching_spec_keeps_established_score_bindings_and_deduplicates():
    original = {'number': '1', 'title': '系统集成方案', 'points': 5, 'text': '已绑定的真实评分要求',
                'source': {'document_id': 'd', 'chunk_id': 'old-score-row', 'locator': '正文 / 表格 3 / 第 3 行'}}
    additional = {'number': '2', 'title': '接口质量', 'points': 3, 'text': '另一个明确评分要求',
                  'source': {'document_id': 'd', 'chunk_id': 'new-score-row', 'locator': '正文 / 表格 3 / 第 4 行'}}
    group = {'id': 'g', 'title': '系统集成方案', 'source_refs': []}
    child = {'id': 'c', 'title': '接口范围', 'origin': 'tender',
             'source_refs': [{'document_id': 'd', 'chunk_id': 'toc-paragraph', 'locator': '目录 / 第 8 段', 'quote': '接口范围'},
                             copy.deepcopy(additional['source'])]}
    source_spec = {'group_title': group['title'], 'title': child['title'], 'score_factors': [original, additional]}
    base = {'sections': [source_spec], 'score_factors': [original, additional]}
    before = copy.deepcopy(base)
    spec = outline._manual_spec({'id': 'p'}, group, child, 'new-s', [], base)
    assert spec['score_factors'] == [original, additional]
    assert base == before
