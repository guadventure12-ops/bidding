"""One explicitly selected chapter per job; reuse evidence and model validation."""
import copy
import hashlib
import json
from datetime import date

from . import db, provider, content_review, bid_body, review_rules

KIND = 'section_regeneration'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def target_todos(items, section):
    return sorted([{**t, 'section_ids':[section['id']],
                    'requirement_ids':[r for r in t.get('requirement_ids', []) if r in section['requirement_ids']]}
                   for t in items if section['id'] in t.get('section_ids', [])], key=lambda t:t['id'])


@review_rules.governed
def inputs(section_id):
    from . import workflow, proposal_runtime, compilation_outline, module_drafting
    section = db.one('SELECT * FROM sections WHERE id=?', (section_id,))
    if not section:
        raise ValueError('章节不存在')
    p = workflow.project(section['project_id'])
    if not compilation_outline.is_enabled(p, section):
        raise ValueError('本节未编入当前目录，请先在“调整编制目录”启用所属模块；未调用模型')
    tender = workflow.fingerprint(p['id'])
    if review_rules.active("generation_prerequisites") and (p['analysis_status'] not in ('complete', 'partial') or p['analysis_fingerprint'] != tender):
        raise ValueError('当前招标文件的分析已失效，请先完成分析；未调用模型')
    ids = section['requirement_ids']
    specification=module_drafting.prepare_unit(p,section).get('_proposal_spec')
    reqs = [db.one('SELECT * FROM requirements WHERE id=? AND project_id=?', (id, p['id'])) for id in ids]
    if any(r is None for r in reqs) or (review_rules.active("generation_prerequisites") and ((not ids and not specification) or len(set(ids)) != len(ids))):
        raise ValueError('本章缺少有效的关联招标要求，不能推测生成范围')
    if not provider.key_configured():
        raise ValueError('请先配置 DeepSeek 密钥；未调用模型')
    if review_rules.active("generation_prerequisites") and not workflow._generation_source_exists(p):
        raise ValueError('没有可用的已批准企业资料；未调用模型')
    # Exclude activity timestamps: polling must not invalidate a pending preview.
    version = digest([section, reqs, {k:p.get(k) for k in ('name','domain','company_name','project_number','buyer','deadline','quotation')},
                      p.get('metadata', {}).get('trusted_sources'), specification,
                      module_drafting.source_snapshot(p.get('metadata',{}),section_id),
                      {k:p.get('metadata',{}).get(k) for k in ('proposal_source_policy','proposal_assets','proposal_reference_context','proposal_deliverables','outline_selection')},
                      tender, workflow.knowledge_fingerprint(), {k:v for k,v in db.get_settings().items() if k!='outline_library'}, date.today().isoformat()])
    return section, p, reqs, version


@review_rules.governed
def preview(section_id):
    section, p, reqs, version = inputs(section_id)
    from . import workflow
    from . import proposal_runtime
    from . import module_drafting
    specification=module_drafting.prepare_unit(p,section).get('_proposal_spec')
    from . import index_materials
    batch_count=int(specification['content_kind']=='narrative' or index_materials.free_form(specification)) if specification else len(workflow.generation_batches(reqs))
    from . import review_index
    index_notice=('评审索引默认使用评分细则原表并新增页数列；确实无表时采用通用五列表。'
                  '仅重建索引、不补正文且不填补充要求时不调用模型；填写补充要求后调用DeepSeek一次解释布局（网络连接最多3次尝试，布局校验失败不自动重试）。'
                  '评分原文与分值由程序保留。可同时确认下方缺项补全范围；补全只处理列出的章节，逐节保留原文快照，不自动批准。'
                  '默认随后对当前技术标真实排版并回填页数，可能需要数分钟，排版不调用模型。') if review_index.is_index(specification) else None
    materials=index_materials.preview(p) if index_notice else None
    return {'section_id':section_id, 'project_id':p['id'], 'title':section['title'],
            'uses_saved_module_draft':bool(module_drafting.sources(p,section)),
            'saved_draft_chars':len(section.get('content') or ''),
            'outline_group_id':section.get('outline_group_id',''), 'outline_group_title':section.get('outline_group_title',''),
            'outline_revision':digest(workflow._outline_identity(section)), 'batch_count':batch_count,
            'revision':version, 'requirement_count':len(reqs), 'status':section['status'],
            'user_edited':bool(section['user_edited']),
            'index_materials':materials,
            'instruction_placeholder':('例如：把序号改为编号、增加响应章节列，或调整评分项顺序。原评分标准与分值保留。' if index_notice else None),
            'notice':index_notice or (('仅处理当前二级主题，并保存同版本正文与响应表记录；原文可恢复，其他章节和旧导出不变。'
                       + ('本节按完整写作计划调用DeepSeek一次主请求，校验失败最多2次修复，网络连接按原有最多3次尝试。' if batch_count else '本节为采购表单或交付资料槽，按原表结构重建，不调用模型，不填造未确认事实。')) if specification else
                      f'仅替换当前二级章节正文，成功后回到草稿并保存原文快照，目录保持固定。其他章节、逐条响应和已导出文件不变。将调用 DeepSeek：本节分{batch_count}批，每批1次生成、校验失败最多2次修复；网络失败沿用最多3次尝试。所有批次成功后一次保存本节，失败保留原文，不自动重跑整本。')}


@review_rules.governed
def admit(section_id, revision, instruction, request_id, outline_revision=None, *, complete_index_gaps=False, completion_revision=None, completion_ids=None, update_index_pages=False):
    """Called under workflow.JOB_LOCK before inserting a job."""
    from . import workflow
    section, p, _, current = inputs(section_id)
    outline_current = digest(workflow._outline_identity(section))
    if section.get('outline_group_id') and outline_revision is None:
        raise ValueError('请刷新页面后重新预览当前二级章节目录')
    if outline_revision is not None and outline_revision != outline_current:
        raise ValueError('章节目录或关联范围已变化，请重新预览当前二级章节')
    if (review_rules.active("operation_revision") or p.get('metadata', {}).get('outline_selection') or p.get('metadata',{}).get('product_module_sources',{}).get(section_id)) and current != revision:
        raise ValueError('章节、招标要求、企业资料或设置已变化，请重新预览后确认')
    result={'section_id':section_id, 'section_title':section['title'], 'revision':revision,
            'instruction':instruction, 'request_id':request_id, 'outline_revision':outline_current}
    if complete_index_gaps or update_index_pages:
        from . import proposal_runtime,review_index,index_completion
        if not review_index.is_index(proposal_runtime.decorate_section(p,section).get('_proposal_spec')):
            raise ValueError('补全评分缺项及索引排版仅适用于评审索引章节')
        if complete_index_gaps:result['index_completion_plan']=index_completion.admit(p,completion_revision,completion_ids or [])
    return result


@review_rules.governed
def run(job_id, job):
    if job['payload'].get('complete_index_gaps') or job['payload'].get('update_index_pages'):
        from . import index_completion
        return index_completion.run(job_id,job)
    return run_one(job_id,job)


@review_rules.governed
def run_one(job_id, job):
    from . import workflow, tender_context
    payload = job['payload']
    enforce_revision=review_rules.active('operation_revision') or payload.get('_force_revision_checks',False)
    # A retry after commit must not generate or overwrite a second time.
    saved = db.one("SELECT * FROM project_snapshots WHERE project_id=? AND label=?", (job['project_id'], KIND + ':' + payload['request_id']))
    if saved:
        if saved['payload'].get('section_id') != payload['section_id']:
            raise ValueError('任务编号对应的章节不一致')
        workflow.review_project(job['project_id'])
        return {'message':'本次单章生成已保存，未重复生成', 'section_id':payload['section_id'], 'snapshot_id':saved['id']}
    section, p, reqs, revision = inputs(payload['section_id'])
    enforce_revision = bool(enforce_revision or p.get('metadata', {}).get('outline_selection') or p.get('metadata',{}).get('product_module_sources',{}).get(section['id']))
    if (p['id'] != job['project_id'] or
        (payload.get('outline_revision') and payload['outline_revision'] != digest(workflow._outline_identity(section))) or
        (enforce_revision and revision != payload['revision'])):
        raise ValueError('预览后的内容或依据已变化，保留现有正文，请重新发起单章生成')
    p['_tender_context'] = tender_context.build_context(p['id'])
    unit = {**section, 'requirements':reqs, '_instruction':payload['instruction']}
    workflow.progress(job_id, 5, '仅生成本节：' + section['title'])
    result, evidence = workflow.generate_section_batches(job_id,p,unit,payload['request_id'],cancel=lambda:workflow.cancelled(job_id))
    content, _, _, _ = workflow._validate_generation_result(unit,evidence,result,allow_internal_empty=True)
    # Compare all inputs again after the paid request, then atomically preserve and replace.
    after_section, _, _, after_version = inputs(section['id'])
    if workflow._outline_identity(after_section) != workflow._outline_identity(section):
        raise ValueError('生成期间章节目录或关联范围发生变化，未覆盖当前章节')
    if enforce_revision and after_version != revision:
        raise ValueError('生成期间正文或依据发生变化，未覆盖新修改；模型结果保存在执行诊断中')
    updated = {**section, 'content':content, 'status':'draft', 'user_edited':1,
               'evidence_ids':sorted(set(workflow._generation_citation_values(content))), 'updated_at':db.now()}
    snapshot_id = db.uid()
    with workflow.JOB_LOCK, db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        current = db.decode(conn.execute('SELECT * FROM sections WHERE id=?', (section['id'],)).fetchone())
        if current is None or workflow._outline_identity(current) != workflow._outline_identity(section) or (enforce_revision and current != section):
            raise ValueError('本章已被修改，未覆盖新正文')
        if current != section:
            # Revision checks may be disabled, but the rollback snapshot must
            # still reflect the row actually replaced in this transaction.
            section = current
            updated = {**current, 'content':content, 'status':'draft', 'user_edited':1,
                       'evidence_ids':sorted(set(workflow._generation_citation_values(content))), 'updated_at':db.now()}
        if workflow.cancelled(job_id):
            raise provider.Cancelled('任务已取消，原正文已保留')
        current_project = db.decode(conn.execute('SELECT * FROM projects WHERE id=?', (p['id'],)).fetchone())
        before_todos = copy.deepcopy(current_project['metadata'].get('content_todos', []))
        meta = content_review.generation_metadata(current_project, updated, result)
        from . import module_drafting
        workflow._tx_insert(conn, 'project_snapshots', {'id':snapshot_id, 'project_id':p['id'],
            'label':KIND + ':' + payload['request_id'], 'created_at':db.now(),
            'payload':{'kind':KIND, 'section_id':section['id'], 'before':section, 'after':updated,
                       'before_proposal_record':copy.deepcopy(current_project['metadata'].get('proposal_section_results',{}).get(section['id'])),
                       'after_proposal_record':copy.deepcopy(meta.get('proposal_section_results',{}).get(section['id'])),
                       'before_product_module_sources':module_drafting.source_snapshot(current_project['metadata'],section['id']),
                       'after_product_module_sources':module_drafting.source_snapshot(meta,section['id']),
                       'before_todos':target_todos(before_todos, section),
                       'after_todos':target_todos(meta.get('content_todos', []), updated)}})
        conn.execute('UPDATE sections SET content=?,status=?,user_edited=?,evidence_ids=?,updated_at=? WHERE id=?',
                     (content,'draft',1,json.dumps(updated['evidence_ids']),updated['updated_at'],section['id']))
        conn.execute('UPDATE projects SET metadata=?,status=?,updated_at=? WHERE id=?', (json.dumps(meta,ensure_ascii=False),'review',db.now(),p['id']))
        job_checkpoint=db.decode(conn.execute('SELECT checkpoint FROM jobs WHERE id=?',(job_id,)).fetchone())['checkpoint'] or {}
        conn.execute('UPDATE jobs SET checkpoint=? WHERE id=?', (json.dumps({**job_checkpoint,'single_saved':snapshot_id}),job_id))
    # Existing AI audit records stay historical; their fingerprints no longer match.
    workflow.review_project(p['id'])
    return {'message':'已重新生成本章并保存为草稿：' + section['title'], 'section_id':section['id'], 'snapshot_id':snapshot_id, 'sections':1}


@review_rules.governed
def restore(snapshot_id):
    from . import workflow, chapter_outline
    snapshot = db.one('SELECT * FROM project_snapshots WHERE id=?', (snapshot_id,))
    if not snapshot or snapshot['payload'].get('kind') != KIND:
        raise ValueError('单章原文快照不存在')
    saved = snapshot['payload']
    with workflow.editing(snapshot['project_id']):
        with db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            current = db.decode(conn.execute('SELECT * FROM sections WHERE id=?', (saved['section_id'],)).fetchone())
            if current is None or (review_rules.active('restore_conflict') and not chapter_outline.snapshot_matches(current,saved['after'])):
                raise ValueError('章节已被编辑、批准或再次生成，不能覆盖后续修改；可查看原文快照')
            original = saved['before']
            conn.execute('UPDATE sections SET content=?,evidence_ids=?,status=?,user_edited=?,updated_at=? WHERE id=?',
                (original['content'],json.dumps(original['evidence_ids']),'draft',1,db.now(),original['id']))
            p = db.decode(conn.execute('SELECT * FROM projects WHERE id=?', (snapshot['project_id'],)).fetchone())
            meta = p['metadata']
            if 'after_product_module_sources' in saved:
                from . import module_drafting
                if module_drafting.source_snapshot(meta,original['id']) != saved['after_product_module_sources']:
                    raise ValueError('本章已选产品模块来源发生变化，未覆盖后续编制操作')
                if saved['before_product_module_sources']:
                    meta.setdefault('product_module_sources',{})[original['id']]=copy.deepcopy(saved['before_product_module_sources'])
                elif 'product_module_sources' in meta:
                    meta['product_module_sources'].pop(original['id'],None)
            if review_rules.active('restore_conflict') and target_todos(meta.get('content_todos', []), current) != saved['after_todos']:
                raise ValueError('本章待办已被后续核对或修改，未覆盖后续决定')
            retained = []
            for todo in meta.get('content_todos', []):
                others = [id for id in todo.get('section_ids', []) if id != original['id']]
                if original['id'] not in todo.get('section_ids', []) or others:
                    retained.append({**todo, 'section_ids':others} if original['id'] in todo.get('section_ids', []) else todo)
            for todo in saved['before_todos']:
                existing = next((t for t in retained if t['id']==todo['id']), None)
                if review_rules.active("restore_conflict") and existing and any(existing.get(k)!=todo.get(k) for k in ('kind','message','status','origin','blocks_content','blocks_delivery')):
                    raise ValueError('关联待办已被后续修改，未覆盖其他章节的处理结果')
            meta['content_todos'] = content_review.merge_todos(retained + saved['before_todos'])
            if 'after_proposal_record' in saved and meta.get('generation_profile')=='technical_proposal':
                records=meta.setdefault('proposal_section_results',{})
                if records.get(original['id'])!=saved['after_proposal_record']:
                    raise ValueError('本节响应表已被后续操作修改，未覆盖后续决定')
                if saved.get('before_proposal_record') is None:records.pop(original['id'],None)
                else:records[original['id']]=saved['before_proposal_record']
            conn.execute('UPDATE projects SET metadata=?,status=?,updated_at=? WHERE id=?', (json.dumps(meta,ensure_ascii=False),'review',db.now(),p['id']))
        workflow.review_project(snapshot['project_id'])
    return {'message':'已恢复本章原文为草稿，请重新审核；其他章节未改变'}
