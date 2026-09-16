import copy
import pytest
from app import db, provider, workflow, compilation_outline as outline, outline_library as library, proposal_runtime


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data')
    monkeypatch.setenv('MX_TESTING', '1'); monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    monkeypatch.setattr(provider, 'key_configured', lambda: True)
    monkeypatch.setattr(provider, 'chat_json', lambda *a, **k: pytest.fail('no paid model'))
    db.init(); now = db.now()
    db.insert('projects', {'id': 'p', 'name': '隔离目录验收', 'domain': 'archive', 'company_name': '测试企业', 'created_at': now, 'updated_at': now})
    return monkeypatch


def tender(explicit=True):
    now = db.now()
    db.insert('documents', {'id': 'd', 'project_id': 'p', 'name': '采购.docx', 'path': 'unused.docx', 'source_type': 'tender', 'sha256': 'a' * 64, 'parse_status': 'ready', 'created_at': now, 'updated_at': now})
    texts = ['商务、技术文件目录', '1.系统集成方案', '2.售后运维服务方案', '附件1：表单'] if explicit else ['普通采购要求']
    for i, text in enumerate(texts): db.insert('chunks', {'id': f'c{i}', 'document_id': 'd', 'ordinal': i, 'locator': f'段落 {i}', 'text': text})
    for i, (title, category) in enumerate([('接口集成要求', 'technical'), ('售后运维要求', 'service')]):
        db.insert('requirements', {'id': f'r{i}', 'project_id': 'p', 'document_id': 'd', 'chunk_id': 'c0', 'title': title, 'category': category, 'text': title, 'quote': title, 'created_at': now, 'updated_at': now})


def old_section(identity='s', group='old', name='原目录', title='原主题', ordinal=0, reqs=()):
    now = db.now()
    db.insert('sections', {'id': identity, 'project_id': 'p', 'title': title, 'outline_group_id': group, 'outline_group_title': name,
                          'ordinal': ordinal, 'content': '已有实际正文，含数字 123 和表格。', 'status': 'approved', 'user_edited': 1,
                          'requirement_ids': list(reqs), 'created_at': now, 'updated_at': now})


def select(plan, enabled_ids):
    return outline.save('p', plan['revision'], [{'id': g['id'], 'enabled': g['id'] in enabled_ids} for g in plan['groups']], True)


def mock_tree(*args, **kwargs):
    import json
    data = json.loads(args[1]); ids = [r['id'] for r in data['requirements']]
    names = data['source_required_children'] or [data['module']['title'] + '编制主题']
    return {'sections': [{'title': name, 'requirement_ids': ids if index == 0 else [],
            'suboutline': [{'title': '业务设计', 'children': [{'title': '流程说明'}, {'title': '验收方式'}]}]} for index, name in enumerate(names)], '_usage': {'total_tokens': 100}}


def job(plan):
    payload = outline.admit('p', plan['revision'], True)
    identity = db.uid(); db.insert('jobs', {'id': identity, 'project_id': 'p', 'mode': 'outline', 'status': 'running', 'payload': payload, 'created_at': db.now()})
    return identity, db.one('SELECT * FROM jobs WHERE id=?', (identity,))


def test_default_source_order_enabled_generic_all_disabled(isolated):
    tender(); plan = outline.preview('p')
    source = [g for g in plan['groups'] if g['origin'] == 'tender']
    assert [g['title'] for g in source] == ['系统集成方案', '售后运维服务方案']
    assert all(g['enabled'] and g['source_refs'] for g in source)
    assert all(not g['enabled'] for g in plan['groups'] if g['origin'] == 'generic')
    assert plan['source_count'] == 2
    assert db.one('SELECT * FROM projects WHERE id="p"')['metadata'] == {}


def test_no_tender_outline_still_all_generic_disabled_and_real_keyword_score(isolated):
    tender(False); plan = outline.preview('p')
    assert plan['source_count'] == 0 and not any(g['enabled'] for g in plan['groups'])
    integration = next(g for g in plan['groups'] if g['module_id'] == 'module-integration')
    assert integration['relevance'] > 0 and '集成' in integration['matched_keywords']
    assert not integration['source_refs']


def test_legacy_sections_are_not_forced_to_adopt_new_source_recommendations(isolated):
    tender(); old_section()
    plan=outline.preview('p')
    assert plan['legacy_existing'] and plan['confirmed']
    assert plan['source_count']==2 and not plan['generation']['pending_group_ids']
    outline.ensure_generation_ready('p')
    with pytest.raises(ValueError,match='先保存'):
        outline.admit('p',plan['revision'],True)


def test_same_title_source_wins_and_no_duplicate_activation(isolated):
    tender(); p = outline.preview('p'); dupe = next(g for g in p['groups'] if g.get('module_id') == 'module-integration')
    assert dupe['duplicate_of'] and not dupe['section_ids']
    with pytest.raises(ValueError, match='同名'): select(p, [dupe['id']])


def test_selection_reorder_retains_old_body_approval_reqids_and_undo(isolated):
    old_section(); old_section('s2', 'other', '其他原目录', '其他主题', 1)
    before = db.all('SELECT * FROM sections ORDER BY ordinal,id')
    initial = outline.preview('p'); ids = [g['id'] for g in reversed(initial['groups'])]
    chosen = [{'id': gid, 'enabled': gid == 'other'} for gid in ids]
    saved = outline.save('p', initial['revision'], chosen, True)
    rows = db.all('SELECT * FROM sections ORDER BY ordinal,id'); projection = outline.projection(workflow.project('p'), rows)
    assert [s['id'] for s in projection['active']] == ['s2'] and [s['id'] for s in projection['retained']] == ['s']
    for row in rows:
        original = next(s for s in before if s['id'] == row['id'])
        assert {k: v for k, v in row.items() if k not in outline.DIR_FIELDS} == {k: v for k, v in original.items() if k not in outline.DIR_FIELDS}
    assert outline.undo(saved['snapshot_id'], True)['status'] == 'restored'
    assert db.all('SELECT * FROM sections ORDER BY ordinal,id') == before
    assert outline.undo(saved['snapshot_id'], True)['status'] == 'already_restored'


def test_changes_since_preview_and_explicit_ids_are_checked(isolated):
    old_section(); p = outline.preview('p')
    db.update('sections', 's', {'content': '后续编辑'})
    with pytest.raises(ValueError, match='变化'): select(p, ['old'])
    fresh = outline.preview('p')
    with pytest.raises(ValueError, match='范围'): outline.save('p', fresh['revision'], [{'id': 'wrong', 'enabled': True}], True)
    assert workflow.project('p')['metadata'] == {}


def test_global_library_change_does_not_change_saved_project_snapshot(isolated):
    p = outline.preview('p'); saved = select(p, [p['groups'][0]['id']])
    catalog = library.get_library(); modules = copy.deepcopy(catalog['modules']); modules.reverse(); modules[0]['title'] = '新库标题'
    library.save_library(catalog['revision'], modules, catalog['presets'])
    again = outline.preview('p')
    assert again['groups'] == saved['groups'] and again['presets'] == saved['presets']
    assert again['library_revision'] == saved['library_revision']


def test_noop_selection_has_no_extra_snapshot(isolated):
    old_section(); p = outline.preview('p'); saved = select(p, ['old'])
    again = select(saved, ['old'])
    assert again['snapshot_id'] is None and not again['changed']
    assert len(db.all('SELECT * FROM project_snapshots')) == 1


@pytest.mark.parametrize('state', ['queued', 'running'])
def test_active_jobs_block_save_even_if_review_conflict_disabled(isolated, state):
    old_section(); db.set_setting('review_rules', {'operation_conflict': False})
    p = outline.preview('p'); db.insert('jobs', {'id': 'busy', 'project_id': 'p', 'mode': 'generate', 'status': state, 'created_at': db.now()})
    with pytest.raises(ValueError, match='任务'): select(p, ['old'])


def test_undo_never_overwrites_later_body_or_decision(isolated):
    old_section(); saved = select(outline.preview('p'), ['old'])
    db.update('sections', 's', {'content': '后续人工正文'})
    result = outline.undo(saved['snapshot_id'], True)
    assert result['status'] == 'conflict' and result['skipped'] == 1
    assert db.one('SELECT * FROM sections WHERE id="s"')['content'] == '后续人工正文'


def test_old_project_compatibility_but_new_selected_pending_blocks_generation(isolated):
    with pytest.raises(ValueError, match='选择'): outline.ensure_generation_ready('p')
    old_section(); outline.ensure_generation_ready('p')
    p = outline.preview('p'); saved = select(p, ['old', p['groups'][0]['id']])
    with pytest.raises(ValueError, match='尚未规划'): outline.ensure_generation_ready('p')


def test_r019_existing_directory_is_confirmed_without_selection_write(isolated):
    tender()
    old_section('s0', 'existing-integration', '系统集成方案', '原接口主题')
    old_section('s1', 'existing-service', '售后运维服务方案', '原运维主题', 1)
    before = db.all('SELECT * FROM sections ORDER BY ordinal,id')
    plan = outline.preview('p'); projected = outline.projection(workflow.project('p'), before)
    assert plan['confirmed'] and plan['legacy_existing']
    assert projected['confirmed'] and projected['legacy_existing']
    assert projected['active'] == before and not projected['retained']
    assert not plan['generation']['pending_group_ids']
    outline.ensure_generation_ready('p')
    assert workflow.project('p')['metadata'] == {}
    assert db.all('SELECT * FROM sections ORDER BY ordinal,id') == before
    with pytest.raises(ValueError, match='先保存'): outline.admit('p', plan['revision'], True)


def test_legacy_confirmation_does_not_admit_unsaved_new_module_selection(isolated):
    tender(); old_section()
    plan = outline.preview('p')
    assert plan['confirmed'] and not plan['generation']['pending_group_ids']
    assert any(g['origin']=='tender' and not g['section_ids'] for g in plan['groups'])
    with pytest.raises(ValueError, match='先保存'): outline.admit('p', plan['revision'], True)
    assert outline.projection({'metadata': {}}, [])['confirmed'] is False


def test_fixed_procurement_form_keeps_source_schema_without_invented_subheadings(isolated):
    import json
    now = db.now()
    db.insert('documents', {'id': 'd', 'project_id': 'p', 'name': '原表.docx', 'path': 'unused.docx', 'sha256': 'b' * 64,
                          'parse_status': 'ready', 'source_type': 'tender', 'created_at': now, 'updated_at': now})
    for i, text in enumerate(['商务技术文件目录', '技术偏离表', '附件12：技术偏离表']):
        db.insert('chunks', {'id': f'c{i}', 'document_id': 'd', 'ordinal': i, 'text': text, 'locator': f'正文 {i}'})
    for i, cells in enumerate([['技术条款', '响应', '偏离说明'], ['', '', '']], 3):
        db.insert('chunks', {'id': f'c{i}', 'document_id': 'd', 'ordinal': i, 'text': ' | '.join(cells), 'locator': f'表格 1 行 {i-2}',
                            'kind': 'table_row', 'metadata': {'table': {'index': 1, 'row': i-3, 'cells': cells, 'spans': [1, 1, 1]}}})
    source = outline._base(workflow.project('p'), [], outline._state('p')[3])
    schema = next(s['form_schema'] for s in source['sections'] if s['group_title'] == '技术偏离表')
    calls = []
    def fixed_model(system, prompt, **kwargs):
        data = json.loads(prompt); calls.append(data)
        assert data['fixed_procurement_format'] and data['response_schema']['sections'][0]['suboutline'] == []
        return {'sections': [{'title': '技术偏离表编制', 'requirement_ids': [], 'suboutline': []}]}
    isolated.setattr(provider, 'chat_json', fixed_model)
    plan = outline.preview('p'); chosen = select(plan, [g['id'] for g in plan['groups'] if g['origin'] == 'tender'])
    jid, record = job(chosen); outline.run(jid, record)
    spec = next(s for s in workflow.project('p')['metadata']['proposal_blueprint']['sections'] if s['group_title'] == '技术偏离表')
    assert len(calls) == 1 and spec['suboutline'] == [] and spec['directory_format'] == 'procurement_form'
    assert spec['form_schema'] == schema
    assert spec['form_schema']['tables'][0]['rows'][1]['cells'] == ['', '', '']


@pytest.mark.parametrize('kind', ['attachment', 'form'])
def test_fixed_form_or_attachment_model_allows_empty_tree(isolated, kind):
    import json
    seen = []
    def model(system, prompt, **kwargs):
        data = json.loads(prompt); seen.append(data)
        return {'sections': [{'title': '固定交付资料', 'requirement_ids': [], 'suboutline': []}]}
    isolated.setattr(provider, 'chat_json', model)
    group = {'title': '交付资料', 'origin': 'tender', 'source_refs': []}
    specs = [{'content_kind': kind, 'volume': 'technical', 'form_schema': {'tables': [], 'paragraphs': []}}]
    tree, _ = outline._model_tree('unused', group, specs, [], [], lambda: False)
    assert tree[0]['suboutline'] == [] and tree[0]['directory_format'] == 'procurement_form'
    assert seen[0]['fixed_procurement_format'] is True


def test_free_form_scoring_plan_still_requires_real_h3_h4(isolated):
    import json
    def model(system, prompt, **kwargs):
        data = json.loads(prompt)
        assert not data['fixed_procurement_format']
        return {'sections': [{'title': '自拟增值方案', 'requirement_ids': [], 'suboutline': [{'title': '实施安排', 'children': [{'title': '成果核验'}]}]}]}
    isolated.setattr(provider, 'chat_json', model)
    group = {'title': '增值优惠', 'origin': 'tender', 'source_refs': []}
    specs = [{'content_kind': 'form', 'volume': 'technical', 'score_factors': [{'text': '格式自拟'}], 'form_schema': {'tables': [], 'paragraphs': []}}]
    tree, _ = outline._model_tree('unused', group, specs, [], [], lambda: False)
    assert tree[0]['suboutline'][0]['children'][0]['title'] == '成果核验'
    with pytest.raises(ValueError, match='须包含三级'):
        outline.validate_tree({'sections': [{'title': '自拟方案', 'requirement_ids': [], 'suboutline': []}]}, [])
    with pytest.raises(ValueError, match='沿用原格式'):
        outline.validate_tree({'sections': tree}, [], fixed_form=True)


def test_ai_plan_new_project_has_unique_owners_hierarchy_and_all_ids(isolated):
    tender(); p = outline.preview('p'); selected = select(p, [g['id'] for g in p['groups'] if g['origin'] == 'tender'])
    isolated.setattr(provider, 'chat_json', mock_tree); jid, record = job(selected)
    result = outline.run(jid, record); db.update('jobs', jid, {'status': 'succeeded'})
    assert len(result['results']) == 2 and all(r['model_requests'] == 1 for r in result['results'])
    p = workflow.project('p'); plan = p['metadata']['proposal_blueprint']; rows = db.all('SELECT * FROM sections WHERE project_id="p"')
    assert plan['user_selected'] and plan['recognized']
    assert {s['section_id'] for s in plan['sections']} == {s['id'] for s in rows}
    assert {entry['requirement_id'] for entry in plan['ledger']} == {'r0', 'r1'}
    assert len(rows) == 3 and all(not s['content'] and s['status'] == 'draft' for s in rows)
    proposal_runtime.validate_plan_inputs(p, db.all('SELECT * FROM requirements'))
    generated = [s for s in plan['sections'] if s.get('suboutline')]
    assert len(generated) == 2 and all(s['suboutline'][0]['children'][0]['id'] for s in generated)
    assert not outline.preview('p')['generation']['pending_group_ids']
    outline.ensure_generation_ready('p')
    before = copy.deepcopy(rows); rerun = outline.run(jid, db.one('SELECT * FROM jobs WHERE id=?', (jid,)))
    assert all(r['status'] == 'already_planned' for r in rerun['results'])
    assert db.all('SELECT * FROM sections WHERE project_id="p"') == before


def test_generic_only_new_project_not_falsely_marked_tender(isolated):
    tender(False); p = outline.preview('p'); selected = select(p, [p['groups'][0]['id']])
    isolated.setattr(provider, 'chat_json', mock_tree); jid, record = job(selected); outline.run(jid, record)
    p = workflow.project('p'); plan = p['metadata']['proposal_blueprint']
    assert plan['user_selected'] and not plan['recognized']
    spec = next(s for s in plan['sections'] if s.get('user_selected_module'))
    assert spec['writing_instruction'] and spec['requirement_ids'] == [] and not spec['source_refs']


def test_existing_legacy_ai_addition_never_rewrites_old_row_or_old_response(isolated):
    tender(False); old_section(reqs=['r0']); before = db.one('SELECT * FROM sections WHERE id="s"')
    p = outline.preview('p'); service = next(g['id'] for g in p['groups'] if g.get('module_id') == 'module-service')
    selected = select(p, ['old', service]); selected_before = db.one('SELECT * FROM sections WHERE id="s"')
    isolated.setattr(provider, 'chat_json', mock_tree); jid, record = job(selected); outline.run(jid, record)
    after = db.one('SELECT * FROM sections WHERE id="s"')
    assert {k: v for k, v in after.items() if k != 'ordinal'} == {k: v for k, v in selected_before.items() if k != 'ordinal'}
    assert not workflow.project('p')['metadata'].get('generation_profile')
    new = next(s for s in db.all('SELECT * FROM sections') if s['id'] != 's')
    assert new['requirement_ids'] == ['r1'] and outline.specification(workflow.project('p'), new)['suboutline']


def test_bounded_format_repair_and_failed_tree_never_inserts_chapter(isolated):
    p = outline.preview('p'); selected = select(p, [p['groups'][0]['id']]); calls = []
    isolated.setattr(provider, 'chat_json', lambda *a, **k: calls.append(1) or {'sections': []})
    jid, record = job(selected)
    with pytest.raises(provider.ProviderError, match='补正'): outline.run(jid, record)
    assert len(calls) == 2 and not db.all('SELECT * FROM sections')
    assert not workflow.project('p')['metadata'].get('proposal_blueprint')


def test_ai_generation_concurrent_edit_is_not_overwritten(isolated):
    old_section(); p = outline.preview('p'); selected = select(p, [p['groups'][0]['id']])
    def model(*args, **kwargs):
        db.update('sections', 's', {'content': '模型执行中手动修改'})
        return mock_tree(*args, **kwargs)
    isolated.setattr(provider, 'chat_json', model); jid, record = job(selected)
    with pytest.raises(ValueError, match='变化'): outline.run(jid, record)
    assert len(db.all('SELECT * FROM sections')) == 1
    assert db.one('SELECT * FROM sections WHERE id="s"')['content'] == '模型执行中手动修改'


def test_partial_success_retry_only_plans_remaining_module(isolated):
    p = outline.preview('p'); selected = select(p, [g['id'] for g in p['groups'][:2]])
    calls = []
    def first(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2: raise provider.ProviderError('模拟连接失败')
        return mock_tree(*args, **kwargs)
    isolated.setattr(provider, 'chat_json', first); jid, record = job(selected)
    with pytest.raises(provider.ProviderError): outline.run(jid, record)
    saved = db.all('SELECT * FROM sections ORDER BY ordinal,id')
    assert len(saved) == 2 and len(outline.preview('p')['generation']['pending_group_ids']) == 1
    isolated.setattr(provider, 'chat_json', lambda *a, **k: calls.append(1) or mock_tree(*a, **k))
    result = outline.run(jid, db.one('SELECT * FROM jobs WHERE id=?', (jid,)))
    assert len(calls) == 3 and result['results'][0]['status'] == 'already_planned'
    for old in saved:
        current = db.one('SELECT * FROM sections WHERE id=?', (old['id'],))
        assert {k: v for k, v in current.items() if k != 'ordinal'} == {k: v for k, v in old.items() if k != 'ordinal'}


def test_partial_retry_does_not_accept_later_changed_selection(isolated):
    p = outline.preview('p'); selected = select(p, [g['id'] for g in p['groups'][:2]])
    calls = []
    def first(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2: raise provider.ProviderError('模拟失败')
        return mock_tree(*args, **kwargs)
    isolated.setattr(provider, 'chat_json', first); jid, record = job(selected)
    with pytest.raises(provider.ProviderError): outline.run(jid, record)
    db.update('jobs', jid, {'status': 'failed'})
    current = outline.preview('p')
    outline.save('p', current['revision'], [{'id': g['id'], 'enabled': g['enabled']} for g in reversed(current['groups'])], True)
    with pytest.raises(ValueError, match='变化'): outline.run(jid, db.one('SELECT * FROM jobs WHERE id=?', (jid,)))
    assert len(calls) == 2


def test_generated_empty_directory_undo_preserves_original_sections(isolated):
    old_section(); p = outline.preview('p'); selected = select(p, ['old', p['groups'][0]['id']])
    original = db.one('SELECT * FROM sections WHERE id="s"')
    isolated.setattr(provider, 'chat_json', mock_tree); jid, record = job(selected)
    result = outline.run(jid, record); db.update('jobs', jid, {'status': 'succeeded'})
    restored = outline.undo(result['results'][0]['snapshot_id'], True)
    assert restored['status'] == 'restored' and db.all('SELECT * FROM sections') == [original]
    assert len(outline.preview('p')['generation']['pending_group_ids']) == 1


def test_fixed_delivery_groups_remain_active_when_technical_disabled(isolated):
    tender()
    for i,text in enumerate(('附件3：法定代表人授权书','被授权人：________（签名）','附件16：报价一览表','报价金额：________'),4):
        db.insert('chunks',{'id':f'c{i}','document_id':'d','ordinal':i,'text':text,'locator':f'正文 / 段落 {i}'})
    p = outline.preview('p'); selected = select(p, [g['id'] for g in p['groups'] if g['origin'] == 'tender'])
    isolated.setattr(provider, 'chat_json', mock_tree); jid, record = job(selected)
    outline.run(jid, record); db.update('jobs', jid, {'status': 'succeeded'})
    before = db.all('SELECT * FROM sections ORDER BY ordinal,id')
    chosen = select(outline.preview('p'), [])
    projected = outline.projection(workflow.project('p'), db.all('SELECT * FROM sections ORDER BY ordinal,id'))
    assert len(projected['active']) == 3 and len(projected['retained']) == 2
    assert {g['title'] for g in chosen['fixed_groups']} == {'资格文件', '报价文件', '内部响应与交付台账'}
    for row in projected['active'] + projected['retained']:
        original = next(s for s in before if s['id'] == row['id'])
        assert row['content'] == original['content'] and row['status'] == original['status'] and row['requirement_ids'] == original['requirement_ids']


def test_without_procurement_forms_only_real_internal_delivery_slot_is_created(isolated):
    tender(False)
    for identity,category,text in [('qual','qualification','供应商须满足资格条件'),('price','pricing','应报含税总金额')]:
        db.insert('requirements',{'id':identity,'project_id':'p','document_id':'d','chunk_id':'c0','category':category,'title':text,'text':text,'quote':text,'created_at':db.now(),'updated_at':db.now()})
    p=outline.preview('p'); chosen=select(p,[p['groups'][0]['id']]); isolated.setattr(provider,'chat_json',mock_tree)
    jid,record=job(chosen); outline.run(jid,record)
    project=workflow.project('p'); plan=project['metadata']['proposal_blueprint']
    assert not any(s['volume'] in ('qualification','pricing') for s in plan['sections'])
    internal=next(s for s in plan['sections'] if s['volume']=='internal')
    assert {'qual','price'}<=set(internal['requirement_ids'])
    assert all(r['owner_section_key']==internal['section_key'] and r['disposition']=='internal' for r in plan['ledger'] if r['requirement_id'] in ('qual','price'))
    assert {i['volume'] for i in plan['issues'] if i['type']=='delivery_format_unspecified'}=={'qualification','pricing'}
    assert [g['title'] for g in outline.preview('p')['fixed_groups']]==['内部响应与交付台账']
    from app import proposal_generation
    unit={**db.one('SELECT * FROM sections WHERE id=?',(internal['section_id'],)),
          'requirements':[db.one('SELECT * FROM requirements WHERE id=?',(rid,)) for rid in internal['requirement_ids']], '_proposal_spec':internal}
    result,_=proposal_generation.generate_proposal_section('no-model',project,unit,'op')
    assert result['_generation_mode']=='deterministic_internal' and result['_model_calls']==0
    assert '供应商须满足资格条件' in result['content'] and '应报含税总金额' in result['content']


def test_actual_fixed_form_schema_preserved_and_invalid_source_not_silently_skipped(isolated):
    tender(False)
    for i,text in enumerate(('附件3：法定代表人授权书','被授权人：________（签名）','附件16：报价一览表','含税总价：________'),1):
        db.insert('chunks',{'id':f'c{i}','document_id':'d','ordinal':i,'text':text,'locator':f'正文 / 段落 {i}'})
    p=workflow.project('p'); requirements=db.all('SELECT * FROM requirements'); base=outline._base(p,requirements,outline._state('p')[3])
    schemas={s['volume']:copy.deepcopy(s['form_schema']) for s in base['sections'] if s['volume'] in ('qualification','pricing')}
    chosen=select(outline.preview('p'),[outline.preview('p')['groups'][0]['id']]); isolated.setattr(provider,'chat_json',mock_tree)
    jid,record=job(chosen); outline.run(jid,record)
    plan=workflow.project('p')['metadata']['proposal_blueprint']
    for spec in plan['sections']:
        if spec['volume'] in schemas: assert spec['form_schema']==schemas[spec['volume']]
    assert {s['volume'] for s in plan['sections']}=={'qualification','pricing','internal','technical'}
    broken=copy.deepcopy(base)
    target=next(s for s in broken['sections'] if s['volume']=='qualification')
    target['form_schema']['source_refs']=[]
    with pytest.raises(ValueError,match='已有结构但缺少完整来源'):
        outline._fixed_rows(p,broken,{outline.KEY:{'groups':[]}},requirements)
    target['form_schema']=copy.deepcopy(schemas['qualification'])
    target['form_schema']['source_refs'].append(copy.deepcopy(target['form_schema']['source_refs'][0]))
    with pytest.raises(ValueError,match='来源位置重复'):
        outline._fixed_rows(p,broken,{outline.KEY:{'groups':[]}},requirements)


@pytest.mark.parametrize('bad', ['requirement', 'duplicate', 'fifth', 'numbered', 'missing_source_child'])
def test_strict_tree_validation(bad):
    tree = {'sections': [{'title': '主题', 'requirement_ids': ['r'], 'suboutline': [{'title': '设计', 'children': [{'title': '流程'}]}]}]}
    required = []
    if bad == 'requirement': tree['sections'][0]['requirement_ids'] = ['other']
    if bad == 'duplicate': tree['sections'].append(copy.deepcopy(tree['sections'][0]))
    if bad == 'fifth': tree['sections'][0]['suboutline'][0]['children'][0]['children'] = [{'title': '第五级'}]
    if bad == 'numbered': tree['sections'][0]['title'] = '一、主题'
    if bad == 'missing_source_child': required = ['采购指定子项']
    with pytest.raises(ValueError): outline.validate_tree(tree, ['r'], required)
