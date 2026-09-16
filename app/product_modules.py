"""Human-authored reusable drafting material, distinct from approved evidence.

Appending copies exact versioned text into one existing section. Module changes
never rewrite old sections, approval records, procurement responses or exports.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from contextlib import contextmanager

from . import db

KIND = 'product_module_append'
SOURCE_KEY = 'product_module_sources'
SCOPES = {'general', 'archive', 'expense'}
MAX_MODULE_CHARS = 250000
MAX_BODY_CHARS = 500000


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def initialize(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS product_modules (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
        scope TEXT NOT NULL DEFAULT 'general', version INTEGER NOT NULL DEFAULT 1,
        metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, deleted_at TEXT NOT NULL DEFAULT '')""")


def _module(conn, identity, *, deleted=False):
    row = db.decode(conn.execute('SELECT * FROM product_modules WHERE id=?', (identity,)).fetchone())
    if not row or (row['deleted_at'] and not deleted):
        raise ValueError('产品功能模块不存在或已删除，请刷新列表')
    return row


def _public(row):
    return {**row, 'revision': digest(row)}


def list_modules(include_deleted=False):
    sql = 'SELECT * FROM product_modules' + ('' if include_deleted else " WHERE deleted_at=''")
    rows = db.all(sql + ' ORDER BY created_at,id')
    return {'modules': [_public(row) for row in rows], 'revision': digest(rows)}


def get_module(identity):
    with db.connect() as conn:
        return _public(_module(conn, identity))


def _values(values, current=None):
    if not isinstance(values, dict) or set(values) - {'title', 'content', 'scope'}:
        raise ValueError('产品功能模块仅支持名称、正文和产品范围')
    merged = {**({'scope': 'general'} if current is None else current), **values}
    title, content, scope = merged.get('title'), merged.get('content'), merged.get('scope')
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
        raise ValueError('模块名称须为1至160个字符')
    if not isinstance(content, str) or not content.strip() or len(content) > MAX_MODULE_CHARS:
        raise ValueError('模块正文不能为空，且不能超过250000个字符')
    if scope not in SCOPES:
        raise ValueError('产品范围仅支持通用、会计电子档案和费控')
    return {'title': title.strip(), 'content': content, 'scope': scope}


def _strict_idle(conn, project_id, *, library=False):
    from . import workflow
    # Direct editing reservations and active jobs remain protected even when
    # the user disables optional risk-review switches.
    if library:
        busy = conn.execute("SELECT id FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone()
    else:
        busy = conn.execute("SELECT id FROM jobs WHERE status IN ('queued','running') AND (project_id=? OR project_id IS NULL) LIMIT 1", (project_id,)).fetchone()
    if busy:
        raise ValueError('相关任务正在运行，请完成或取消后再修改产品素材或章节')
    owned = workflow.SETTINGS_EDIT if library else project_id
    if any(key != owned for key in workflow.EDITING if library or key in (None, workflow.SETTINGS_EDIT, project_id)):
        raise ValueError('相关资料或章节正在保存，请稍后重试')


@contextmanager
def _editing(project_id=None, *, library=False):
    from . import workflow, review_rules
    # Hold admission lock through commit; an edit cannot race new model work.
    # JOB_LOCK is deliberately non-reentrant. Do not nest workflow.editing(),
    # which takes this same lock when it claims its reservation.
    with workflow.JOB_LOCK, review_rules.scope():
        if library and workflow.EDITING:
            raise ValueError('相关资料或章节正在保存，请稍后重试')
        if not library and any(key in (None, workflow.SETTINGS_EDIT, project_id) for key in workflow.EDITING):
            raise ValueError('相关资料或章节正在保存，请稍后重试')
        key = workflow.SETTINGS_EDIT if library else project_id
        workflow.EDITING.add(key)
        try:
            with db.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                _strict_idle(conn, project_id, library=library)
                yield conn
        finally:
            workflow.EDITING.discard(key)


def create(values):
    values = _values(values)
    with _editing(library=True) as conn:
        now = db.now()
        row = {'id': db.uid(), **values, 'version': 1,
               'metadata': {'source_kind': 'user_authored', 'enterprise_fact': False},
               'created_at': now, 'updated_at': now, 'deleted_at': ''}
        from .workflow import _tx_insert
        _tx_insert(conn, 'product_modules', row)
        return _public(row)


def update(identity, revision, values):
    with _editing(library=True) as conn:
        row = _module(conn, identity)
        if revision != digest(row):
            raise ValueError('模块已被修改，请刷新后核对新版本')
        new_values = _values(values, row)
        if all(row[key] == value for key, value in new_values.items()):
            return _public(row)
        row = {**row, **new_values, 'version': row['version'] + 1, 'updated_at': db.now()}
        conn.execute('UPDATE product_modules SET title=?,content=?,scope=?,version=?,updated_at=? WHERE id=?',
                     (row['title'], row['content'], row['scope'], row['version'], row['updated_at'], identity))
        return _public(row)


def delete(identity, revision, confirmed=False):
    if confirmed is not True:
        raise ValueError('请确认删除此产品功能模块；已编入的章节正文仍保留')
    with _editing(library=True) as conn:
        row = _module(conn, identity, deleted=True)
        if row['deleted_at']:
            if revision not in (digest(row), row['metadata'].get('deleted_from_revision')):
                raise ValueError('模块已变化，请刷新后核对')
            return {'id': identity, 'deleted': True, 'already_deleted': True}
        if revision != digest(row):
            raise ValueError('模块已变化，请刷新后核对')
        metadata = {**row['metadata'], 'deleted_from_revision': revision}
        now = db.now()
        conn.execute('UPDATE product_modules SET deleted_at=?,updated_at=?,version=version+1,metadata=? WHERE id=?',
                     (now, now, json.dumps(metadata, ensure_ascii=False), identity))
        return {'id': identity, 'deleted': True, 'already_deleted': False}


def _selection(module_ids):
    if not isinstance(module_ids, list) or not module_ids or len(module_ids) > 100:
        raise ValueError('请选择1至100个明确的产品功能模块')
    if any(not isinstance(identity, str) or not identity for identity in module_ids) or len(set(module_ids)) != len(module_ids):
        raise ValueError('模块ID须明确且不能重复')
    return module_ids


def _section(conn, section_id):
    from . import compilation_outline
    section = db.decode(conn.execute('SELECT * FROM sections WHERE id=?', (section_id,)).fetchone())
    if not section:
        raise ValueError('章节不存在')
    project = db.decode(conn.execute('SELECT * FROM projects WHERE id=?', (section['project_id'],)).fetchone())
    if not project:
        raise ValueError('项目不存在')
    if not compilation_outline.is_enabled(project, section):
        raise ValueError('本节未编入当前目录，请先启用所属模块')
    return section, project


def _source_list(project, section_id):
    return copy.deepcopy((project.get('metadata') or {}).get(SOURCE_KEY, {}).get(section_id, []))


def _plan(conn, section_id, module_ids):
    _selection(module_ids)
    section, project = _section(conn, section_id)
    modules = [_module(conn, identity) for identity in module_ids]
    sources = _source_list(project, section_id)
    applied = {(row['module_id'], row['version']) for row in sources}
    body = section['content']
    items, appended = [], []
    for module in modules:
        if module['scope'] not in ('general', project['domain']):
            raise ValueError('所选模块“' + module['title'] + '”的产品范围不适用于当前项目')
        already = (module['id'], module['version']) in applied
        items.append({'id': module['id'], 'title': module['title'], 'scope': module['scope'],
                      'version': module['version'], 'characters': len(module['content']),
                      'status': 'skipped' if already else 'eligible',
                      'reason': '此模块的同一版本已编入本节，跳过重复追加' if already else '按选定顺序原样追加，保留现有正文'})
        if not already:
            body += ('\n\n' if body else '') + module['content']
            appended.append(module)
    if len(body) > MAX_BODY_CHARS:
        raise ValueError('追加后正文超过500000个字符，请减少本次模块范围')
    revision = digest({'section': section, 'modules': modules, 'sources': sources,
                       'domain': project['domain'], 'outline': project['metadata'].get('outline_selection'),
                       'database': str(db.DATA.resolve())})
    public = {'section_id': section_id, 'title': section['title'], 'revision': revision,
              'module_ids': module_ids, 'modules': items, 'before': section['content'], 'after': body,
              'appended_count': len(appended), 'skipped_count': len(modules) - len(appended),
              'notice': '将选中模块的当前版本原样追加到本节并保存为草稿，不调用AI。产品素材是人工编制内容，不自动视为已核验企业事实；不批准章节，不完成签章或附件。'}
    return public, section, project, sources, appended


def preview(section_id, module_ids):
    with db.connect() as conn:
        conn.execute('BEGIN')
        return _plan(conn, section_id, module_ids)[0]


def _request(request_id, section_id, module_ids, revision):
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', request_id):
        raise ValueError('请提供有效的本次操作编号')
    return digest({'section_id': section_id, 'module_ids': module_ids, 'revision': revision})


def _operation(conn, request_id):
    row = db.decode(conn.execute('SELECT * FROM project_snapshots WHERE label=? AND json_extract(payload,\'$.kind\')=?',
                                 (KIND + ':' + request_id, KIND)).fetchone())
    return row


def apply(section_id, module_ids, revision, request_id, confirmed=False):
    if confirmed is not True:
        raise ValueError('请确认预览中的模块版本及本节追加内容')
    _selection(module_ids)
    request_hash = _request(request_id, section_id, module_ids, revision)
    # An executed request can be replayed after module deletion or later edits;
    # it reports the original result and never re-applies the copied material.
    with db.connect() as conn:
        prior = _operation(conn, request_id)
        section = db.decode(conn.execute('SELECT * FROM sections WHERE id=?', (section_id,)).fetchone())
    if prior:
        if prior['payload'].get('request_hash') != request_hash:
            raise ValueError('操作编号已用于其他范围，请重新预览并使用新操作编号')
        return prior['payload']['result']
    if not section:
        raise ValueError('章节不存在')
    with _editing(section['project_id']) as conn:
        prior = _operation(conn, request_id)
        if prior:
            if prior['payload'].get('request_hash') != request_hash:
                raise ValueError('操作编号已用于其他范围，请使用新操作编号')
            return prior['payload']['result']
        plan, section, project, before_sources, modules = _plan(conn, section_id, module_ids)
        if revision != plan['revision']:
            raise ValueError('章节、模块内容版本、产品范围或目录已变化，请重新预览；未覆盖新修改')
        snapshot_id, now = db.uid(), db.now()
        after, sources = copy.deepcopy(section), copy.deepcopy(before_sources)
        if modules:
            # Genuine citation IDs may be carried from an existing enterprise
            # source. A custom module itself never creates an evidence record.
            from . import workflow
            candidates = list(dict.fromkeys(identity for module in modules for identity in workflow._generation_citation_values(module['content'])))
            valid = workflow._valid_evidence(candidates, project['domain'], project['id']) if candidates else []
            after.update(content=plan['after'], status='draft', user_edited=1, updated_at=now,
                         evidence_ids=list(dict.fromkeys(section['evidence_ids'] + [row['id'] for row in valid])))
            for module in modules:
                sources.append({'module_id': module['id'], 'title': module['title'], 'version': module['version'],
                                'scope': module['scope'], 'source_kind': 'user_authored', 'enterprise_fact': False,
                                'content_sha256': hashlib.sha256(module['content'].encode()).hexdigest(),
                                'content': module['content'], 'snapshot_id': snapshot_id,
                                'operation_id': snapshot_id, 'applied_at': now})
            metadata = copy.deepcopy(project['metadata'])
            metadata.setdefault(SOURCE_KEY, {})[section_id] = sources
            conn.execute('UPDATE sections SET content=?,status=?,user_edited=?,evidence_ids=?,updated_at=? WHERE id=?',
                         (after['content'], 'draft', 1, json.dumps(after['evidence_ids']), now, section_id))
            conn.execute('UPDATE projects SET metadata=?,status=?,updated_at=? WHERE id=?',
                         (json.dumps(metadata, ensure_ascii=False), 'review', now, project['id']))
        result = {'section_id': section_id, 'appended_count': len(modules), 'skipped_count': plan['skipped_count'],
                  'snapshot_id': snapshot_id, 'section': after,
                  'results': [{**item, 'status': 'appended' if item['status'] == 'eligible' else 'skipped'} for item in plan['modules']],
                  'message': (f'已将{len(modules)}份模块原样追加到本节并保存为草稿；未调用AI，其他章节不变' if modules else '所选模块同一版本已编入，未重复修改正文')}
        from .workflow import _tx_insert
        _tx_insert(conn, 'project_snapshots', {'id': snapshot_id, 'project_id': project['id'],
                   'label': KIND + ':' + request_id, 'created_at': now,
                   'payload': {'kind': KIND, 'request_hash': request_hash, 'section_id': section_id,
                               'before': section, 'after': after, 'before_sources': before_sources,
                               'after_sources': sources, 'before_sources_present': section_id in project['metadata'].get(SOURCE_KEY, {}),
                               'changed': bool(modules), 'restored': False, 'result': result}})
        return result


def undo(snapshot_id, confirmed=False):
    if confirmed is not True:
        raise ValueError('请确认撤销本次模块追加；只恢复尚未再修改的本节正文')
    snapshot = db.one('SELECT * FROM project_snapshots WHERE id=?', (snapshot_id,))
    if not snapshot or snapshot['payload'].get('kind') != KIND:
        raise ValueError('产品模块追加快照不存在')
    with _editing(snapshot['project_id']) as conn:
        snapshot = db.decode(conn.execute('SELECT * FROM project_snapshots WHERE id=?', (snapshot_id,)).fetchone())
        payload = snapshot['payload']
        if payload.get('restored'):
            return {'section_id': payload['section_id'], 'status': 'already_restored', 'message': '本次追加已撤销，未重复修改正文'}
        current, project = _section(conn, payload['section_id'])
        if payload['changed'] and (current != payload['after'] or _source_list(project, current['id']) != payload['after_sources']):
            raise ValueError('本节已编辑、批准、再次生成或来源记录已变化，不能覆盖后续决定；原文快照仍保留')
        if payload['changed']:
            original = payload['before']
            # Approval needs a new human decision after a restore, matching the
            # existing single-generation restore behavior.
            conn.execute('UPDATE sections SET content=?,evidence_ids=?,status=?,user_edited=?,updated_at=? WHERE id=?',
                         (original['content'], json.dumps(original['evidence_ids']), 'draft', 1, db.now(), original['id']))
            metadata = copy.deepcopy(project['metadata'])
            if payload['before_sources_present']:
                metadata.setdefault(SOURCE_KEY, {})[current['id']] = payload['before_sources']
            else:
                metadata.get(SOURCE_KEY, {}).pop(current['id'], None)
                if not metadata.get(SOURCE_KEY):
                    metadata.pop(SOURCE_KEY, None)
            conn.execute('UPDATE projects SET metadata=?,status=?,updated_at=? WHERE id=?',
                         (json.dumps(metadata, ensure_ascii=False), 'review', db.now(), project['id']))
        payload['restored'], payload['restored_at'] = True, db.now()
        conn.execute('UPDATE project_snapshots SET payload=? WHERE id=?', (json.dumps(payload, ensure_ascii=False), snapshot_id))
        return {'section_id': payload['section_id'], 'status': 'restored', 'message': '已撤销本次模块追加，原正文恢复为草稿；其他章节、产品模块与旧导出不变'}


def source_context(section, project):
    """Frozen human drafting inputs, never retrieved or approved enterprise facts."""
    sources = _source_list(project, section['id'])
    for source in sources:
        if (not isinstance(source.get('content'), str)
                or hashlib.sha256(source['content'].encode()).hexdigest() != source.get('content_sha256')):
            raise ValueError('本节已选模块快照内容与版本不一致，请核对历史快照')
    return sources
