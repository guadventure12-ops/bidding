import copy
import hashlib
import json
import pytest
from app import db, provider, workflow, chapter_outline as co, proposal_runtime as runtime, proposal_generation as generation
from app import compilation_outline as selection, proposal_context, proposal_deliverables
from app import outline_library
from test_compilation_outline import isolated, tender, old_section, select, job, mock_tree


def planned():
    return {'section_id': 's', 'title': '接口方案', 'group_title': '系统集成', 'content_kind': 'narrative',
            'suboutline': [{'id': 'topic-a', 'title': '业务设计', 'children': [{'id': 'topic-b', 'title': '接口流程'}, {'id': 'topic-c', 'title': '异常处理'}]},
                           {'id': 'topic-d', 'title': '交付验收', 'children': [{'id': 'topic-e', 'title': '验收方法'}]}]}


def body(h3='###', h4='####'):
    return f'{h3} 业务设计\n依据已批准材料组织方案，范围及事实由项目资料确认。\n{h4} 接口流程\n说明流程。\n{h4} 异常处理\n说明补偿。\n{h3} 交付验收\n说明验收范围。\n{h4} 验收方法\n组织成果核对。'


def test_user_selected_blueprint_is_adopted_without_falsely_marking_tender():
    p = {'metadata': {'generation_profile': runtime.PROFILE, 'proposal_blueprint': {'user_selected': True, 'recognized': False, 'sections': []}}}
    assert runtime.blueprint(p) is p['metadata']['proposal_blueprint']
    assert runtime.blueprint(p)['recognized'] is False


def test_full_blueprint_validation_precedes_disabled_projection(isolated):
    tender(); db.execute('DELETE FROM sections')
    units = runtime.prepare_plan('p', db.all('SELECT * FROM requirements'))
    p = workflow.project('p'); before = db.all('SELECT * FROM sections ORDER BY ordinal,id')
    plan = selection.preview('p'); save = select(plan, [])
    p = workflow.project('p'); rows = db.all('SELECT * FROM sections ORDER BY ordinal,id')
    assert len(runtime.decorate_units(p, rows, db.all('SELECT * FROM requirements'))) == 3
    disabled = next(s for s in rows if not selection.is_enabled(p, s))
    modified = copy.deepcopy(rows); next(s for s in modified if s['id'] == disabled['id'])['requirement_ids'] = ['bad']
    with pytest.raises(ValueError, match='要求范围'): runtime.decorate_units(p, modified, db.all('SELECT * FROM requirements'))
    with pytest.raises(ValueError, match='目录与当前章节'): runtime.decorate_units(p, [r for r in rows if r['id'] != disabled['id']], db.all('SELECT * FROM requirements'))
    with pytest.raises(ValueError, match='未编入'): runtime.decorate_section(p, disabled)


def test_confirmed_legacy_plan_never_adds_missing_requirement_category(isolated):
    tender(False); old_section(reqs=['r0'])
    saved = select(selection.preview('p'), ['old'])
    before = db.all('SELECT * FROM sections ORDER BY ordinal,id')
    plan = co.generation_plan('p', db.all('SELECT * FROM requirements'), workflow.CATEGORY_LABELS)
    assert [u['id'] for u in plan] == ['s']
    assert db.all('SELECT * FROM sections ORDER BY ordinal,id') == before
    assert {r['id'] for r in db.all('SELECT * FROM requirements')} == {'r0', 'r1'}


def test_new_generic_legacy_leaf_receives_its_saved_spec(isolated):
    old_section(); initial = selection.preview('p'); chosen = select(initial, ['old', initial['groups'][0]['id']])
    isolated.setattr(provider, 'chat_json', mock_tree); jid, row = job(chosen); selection.run(jid, row)
    db.update('jobs', jid, {'status': 'succeeded'})
    project = workflow.project('p'); created = next(s for s in db.all('SELECT * FROM sections') if s['id'] != 's')
    decorated = runtime.decorate_section(project, created)
    assert decorated['_proposal_spec']['user_selected_module'] and decorated['_proposal_spec']['suboutline']
    assert decorated['_proposal_spec']['requirement_ids'] == [] and not decorated['_proposal_spec']['source_refs']
    assert not runtime.blueprint(project)


def test_empty_leaf_uses_plan_and_existing_manual_body_uses_visible_headings_without_write(isolated):
    spec = planned(); empty = {'id': 's', 'title': spec['title'], 'content': ''}
    assert co.effective_suboutline(empty, spec) == spec['suboutline']
    edited = {**empty, 'user_edited': 1, 'content': '# 接口方案\n\n## 业务设计\n```markdown\n# 伪造代码标题\n```\n### 接口流程\n实际正文\n## 自定义交付\n### 自定义校验'}
    old = copy.deepcopy(edited); source_spec = copy.deepcopy(spec)
    tree = co.effective_suboutline(edited, spec)
    assert [node['title'] for node in tree] == ['业务设计', '自定义交付']
    assert tree[0]['id'] == 'topic-a' and tree[0]['children'][0]['id'] == 'topic-b'
    assert tree[1]['children'][0]['title'] == '自定义校验'
    assert edited == old and spec == source_spec
    assert runtime.display_spec(spec, edited)['suboutline'] == tree


def test_relative_heading_levels_match_word_and_ignore_fences_and_exact_self_labels():
    spec = planned()
    for first, second in [('###', '####'), ('##', '###'), ('#', '###')]:
        co.validate_body_structure(spec, body(first, second))
    wrapped = '# 系统集成\n## 接口方案\n' + body() + '\n~~~markdown\n# 代码标题\n## 代码标题二\n~~~'
    co.validate_body_structure(spec, wrapped)
    assert co.body_headings(spec, wrapped) == co.body_headings(spec, body())
    duplicate_inside = body() + '\n### 接口方案\n真实但多余的内部标题'
    with pytest.raises(provider.ProviderError): co.validate_body_structure(spec, duplicate_inside)


@pytest.mark.parametrize('case', ['missing', 'extra', 'reorder', 'h5', 'no_headings', 'incorrect_level'])
def test_body_structure_rejects_plan_divergence(case):
    content = body()
    if case == 'missing': content = content.replace('#### 异常处理\n说明补偿。\n', '')
    if case == 'extra': content += '\n### 额外事项'
    if case == 'reorder': content = content.replace('接口流程', '临时').replace('异常处理', '接口流程').replace('临时', '异常处理')
    if case == 'h5': content = content.replace('#### 异常处理', '##### 异常处理')
    if case == 'no_headings': content = '只有普通正文，未按确认目录编写。'
    if case == 'incorrect_level': content = content.replace('#### 接口流程', '### 接口流程')
    with pytest.raises(provider.ProviderError): co.validate_body_structure(planned(), content)


def test_fixed_procurement_format_is_exempt_and_never_gets_invented_suboutline():
    spec = {**planned(), 'directory_format': 'procurement_form', 'suboutline': []}
    co.validate_body_structure(spec, '|条款|响应|\n|---|---|\n|原表|保留|')
    assert co.effective_suboutline({'id': 's', 'content': '## 原采购附件表头'}, spec) == []
    legacy = {'title': '采购原表', 'group_title': '技术偏离', 'content_kind': 'form', 'volume': 'technical', 'form_schema': {'tables': [{'rows': []}]}}
    displayed = runtime.display_spec(legacy, {'id': 's', 'content': '### 旧版内部标题\n原表内容'})
    assert displayed['suboutline'] == [] and displayed['directory_format'] == 'procurement_form'


def test_editor_and_next_generation_use_same_manual_outline(isolated):
    old_section(title='接口方案'); content = body().replace('异常处理', '项目异常管理')
    db.update('sections', 's', {'content': content})
    metadata = workflow.project('p')['metadata']; metadata[selection.KEY] = {'confirmed': True, 'groups': [{'id': 'old', 'enabled': True, 'section_ids': ['s']}], 'fixed_groups': [], 'specifications': {'s': {**planned(), 'requirement_ids': []}}}
    db.update('projects', 'p', {'metadata': metadata})
    p = workflow.project('p'); row = db.one('SELECT * FROM sections WHERE id="s"'); original = copy.deepcopy(row)
    view = co.view('p')['groups'][0]['sections'][0]
    decorated = runtime.decorate_section(p, row)
    assert view['children'] == decorated['_proposal_spec']['suboutline']
    assert view['children'][0]['children'][1]['title'] == '项目异常管理'
    assert db.one('SELECT * FROM sections WHERE id="s"') == original


def test_generic_writing_intent_with_zero_requirements_uses_model_and_separate_fact_context(isolated):
    target = {'id': 's', 'project_id': 'p', 'title': '接口方案', 'outline_group_title': '系统集成', 'requirements': [],
              '_proposal_spec': {**planned(), 'user_selected_module': True, 'writing_instruction': '说明交付步骤', 'source_refs': [], 'feature_rows': [], 'score_factors': []}}
    calls = []
    isolated.setattr(workflow, '_prepare_generation_request', lambda *a, **k: ('证据数据：[]', {}, False))
    def respond(job_id, key, unit, evidence, prompt, cancel=None):
        calls.append(prompt); return {'content': body(), 'responses': []}
    isolated.setattr(workflow, '_generate_with_repairs', respond)
    result, evidence = generation.generate_proposal_section('j', workflow.project('p'), target, 'op')
    assert len(calls) == 1 and result['responses'] == [] and evidence == {}
    assert '"user_selected_writing_scope": "说明交付步骤"' in calls[0]
    assert '"suboutline"' in calls[0] and '不是采购要求或企业事实证据' in calls[0]
    assert target['_proposal_spec']['source_refs'] == []


def test_existing_body_proposal_generator_rejects_extra_or_missing_planned_headings(isolated):
    target = {'id': 's', 'project_id': 'p', 'title': '接口方案', 'outline_group_title': '系统集成', 'requirements': [],
              '_proposal_spec': {**planned(), 'user_selected_module': True, 'writing_instruction': '说明交付步骤'}}
    isolated.setattr(workflow, '_prepare_generation_request', lambda *a, **k: ('证据数据：[]', {}, False))
    isolated.setattr(workflow, '_generate_with_repairs', lambda *a, **k: {'content': '### 未授权的目录主题\n正文。', 'responses': []})
    with pytest.raises(provider.ProviderError, match='标题或顺序'): generation.generate_proposal_section('j', workflow.project('p'), target, 'op')


def test_shared_recipe_initializer_runs_for_selected_plan_and_preserves_verified_inputs(isolated):
    tender(False)
    assets = db.DATA / 'assets'; assets.mkdir(); manifest = assets / 'reviewed.json'; manifest.write_text('{"assets":[]}', encoding='utf-8')
    recipe = {'id': 'exact-fixture', 'version': 'synthetic-1', 'tender_sha256s': ['a' * 64],
              'source_policy': {'fact_document_ids': ['approved-doc'], 'reference_document_ids': []},
              'asset_manifest_relative': 'assets/reviewed.json', 'asset_manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
              'reference_context': {'fixture': 'reference'}, 'deliverables': {'fixture': 'delivery'}}
    (db.DATA / 'proposal-recipes.json').write_text(json.dumps({'recipes': [recipe]}), encoding='utf-8')
    seen = []
    isolated.setattr(proposal_context, 'load_context', lambda p: seen.append(('reference', copy.deepcopy(p['metadata']))))
    isolated.setattr(proposal_deliverables, 'load_manifest', lambda p: seen.append(('delivery', copy.deepcopy(p['metadata']))))
    isolated.setattr(provider, 'chat_json', mock_tree)
    p = selection.preview('p'); chosen = select(p, [p['groups'][0]['id']]); jid, record = job(chosen); selection.run(jid, record)
    meta = workflow.project('p')['metadata']
    assert meta['proposal_source_policy'] == recipe['source_policy'] and meta['proposal_recipe']['id'] == 'exact-fixture'
    assert meta['proposal_assets']['manifest_path'] == str(manifest.resolve())
    assert meta['proposal_assets']['sha256'] == recipe['asset_manifest_sha256']
    assert meta['proposal_reference_context'] == recipe['reference_context'] and meta['proposal_deliverables'] == recipe['deliverables']
    assert [label for label, _ in seen] == ['reference', 'delivery']
    assert len(meta['proposal_blueprint']['sections']) == len(db.all('SELECT * FROM sections'))
    for _, data in seen: assert data['proposal_source_policy'] == recipe['source_policy']


@pytest.mark.parametrize('bad', ['missing_hash', 'outside_assets', 'bad_hash', 'bad_source_policy'])
def test_shared_recipe_validation_keeps_original_fail_closed_rules(isolated, bad):
    tender(False)
    recipe = {'id': 'r', 'tender_sha256s': ['a' * 64], 'source_policy': {'fact_document_ids': []}, 'asset_manifest_relative': 'assets/missing.json', 'asset_manifest_sha256': 'a' * 64}
    if bad == 'missing_hash': recipe.pop('asset_manifest_sha256')
    if bad == 'outside_assets': recipe['asset_manifest_relative'] = '../outside.json'
    if bad == 'bad_source_policy': recipe['source_policy'] = []
    (db.DATA / 'proposal-recipes.json').write_text(json.dumps({'recipes': [recipe]}), encoding='utf-8')
    before = workflow.project('p')
    with pytest.raises(ValueError): runtime.initialize_source_policy(before, {})
    assert workflow.project('p') == before and not db.all('SELECT * FROM sections')


@pytest.mark.parametrize('reserved', ['p', None, '__workspace_settings__'])
def test_directory_save_admit_and_undo_respect_edit_reservation_with_rule_off(isolated, reserved):
    old_section(); db.set_setting('review_rules', {'operation_conflict': False})
    initial = selection.preview('p')
    isolated.setattr(workflow, 'EDITING', {reserved})
    with pytest.raises(ValueError, match='正在保存'): select(initial, ['old'])
    workflow.EDITING.clear()
    chosen = select(initial, ['old', initial['groups'][0]['id']])
    workflow.EDITING.add(reserved)
    with pytest.raises(ValueError, match='正在保存'): selection.admit('p', chosen['revision'], True)
    with pytest.raises(ValueError, match='正在保存'): selection.undo(chosen['snapshot_id'], True)
    assert not db.one('SELECT * FROM project_snapshots WHERE id=?', (chosen['snapshot_id'],))['payload']['restored']


def test_directory_commit_blocks_save_that_started_during_model_even_if_rule_off(isolated):
    old_section(); db.set_setting('review_rules', {'operation_conflict': False})
    p = selection.preview('p'); chosen = select(p, ['old', p['groups'][0]['id']]); jid, record = job(chosen)
    isolated.setattr(workflow, 'EDITING', set())
    def racing_model(*args, **kwargs):
        workflow.EDITING.add('p'); return mock_tree(*args, **kwargs)
    isolated.setattr(provider, 'chat_json', racing_model)
    before = db.all('SELECT * FROM sections')
    with pytest.raises(ValueError, match='正在保存'): selection.run(jid, record)
    assert db.all('SELECT * FROM sections') == before


def test_disabled_old_owner_is_never_reassigned_to_new_generic_module(isolated):
    tender()
    runtime.prepare_plan('p', db.all('SELECT * FROM requirements'))
    p = workflow.project('p'); ledger = copy.deepcopy(p['metadata']['proposal_blueprint']['ledger'])
    original = db.all('SELECT * FROM sections ORDER BY ordinal,id')
    catalog = outline_library.get_library(); modules = copy.deepcopy(catalog['modules'])
    modules.append({'id': 'module-extra', 'title': '运维补充模块', 'description': '提供补充运维设计', 'domains': ['archive'], 'keywords': ['运维', '售后']})
    outline_library.save_library(catalog['revision'], modules, catalog['presets'])
    preview = selection.preview('p'); gid = next(g['id'] for g in preview['groups'] if g.get('module_id') == 'module-extra')
    chosen = select(preview, [gid]); isolated.setattr(provider, 'chat_json', mock_tree)
    jid, record = job(chosen); selection.run(jid, record)
    current = workflow.project('p')['metadata']['proposal_blueprint']
    assert current['ledger'] == ledger
    new = next(s for s in current['sections'] if s.get('user_selected_module'))
    assert new['requirement_ids'] == []
    for row in original:
        stored = db.one('SELECT * FROM sections WHERE id=?', (row['id'],))
        assert stored['requirement_ids'] == row['requirement_ids'] and stored['content'] == row['content'] and stored['status'] == row['status']
