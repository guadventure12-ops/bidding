"""Source-defined proposal plans and revision-bound response records.

No existing project's directory is replaced. The procurement ledger remains
complete while the delivered technical volume follows its response format.
"""
from __future__ import annotations
import copy
import hashlib
import json
import re
from pathlib import Path
from . import db

PROFILE = 'technical_proposal'


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def blueprint(project):
    meta=project.get('metadata') or {}
    value=meta.get('proposal_blueprint') or {}
    return value if meta.get('generation_profile')==PROFILE and (value.get('recognized') or value.get('user_selected')) else None


def default_source_policy(project):
    from . import content_review
    documents=content_review.trusted_documents(project)
    # Selection is not approval. A tender selected in the legacy trust dialog
    # still cannot become an enterprise fact in this generation profile.
    return {'mode':'approved_facts_only','fact_document_ids':sorted(d['id'] for d in documents
            if d.get('source_type')=='knowledge' and d.get('status')=='approved'),
            'reference_document_ids':[],'fact_chunk_ids_by_document':{}}


def matching_recipe(project_id):
    """A local reviewed-input recipe is data, scoped to exact tender versions."""
    path=db.DATA/'proposal-recipes.json'
    if not path.is_file():return None
    if path.stat().st_size>4*1024*1024:raise ValueError('参考资料选择配置过大，请核对配置文件')
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict) or not isinstance(value.get('recipes',[]),list):
        raise ValueError('参考资料选择配置格式无效')
    recipes=value.get('recipes',[])
    documents=db.all("SELECT sha256,parse_status FROM documents WHERE project_id=? AND source_type='tender'",(project_id,))
    if not documents or any(d['parse_status']!='ready' or not re.fullmatch('[a-f0-9]{64}',d['sha256']) for d in documents):return None
    sources={d['sha256'] for d in documents}
    matches=[]
    for recipe in recipes:
        if not isinstance(recipe,dict):raise ValueError('参考资料选择配置中的方案格式无效')
        hashes=recipe.get('tender_sha256s')
        if not isinstance(hashes,list) or not hashes or any(not isinstance(h,str) or not re.fullmatch('[a-f0-9]{64}',h) for h in hashes):
            raise ValueError('参考资料选择须填写完整招标SHA256列表')
        if set(hashes)==sources:matches.append(recipe)
    if len(matches)>1:raise ValueError('同一招标版本存在多个参考资料选择，请明确采用版本')
    return matches[0] if matches else None


def initialize_source_policy(project, metadata):
    """Shared exact-tender source initialization for both directory entry paths."""
    metadata=copy.deepcopy(metadata)
    recipe=matching_recipe(project['id'])
    if recipe and (not isinstance(recipe.get('source_policy'),dict) or not isinstance(recipe.get('id'),str) or not recipe['id']):
        raise ValueError('参考资料选择缺少方案标识或有效来源范围')
    metadata.setdefault('proposal_source_policy',copy.deepcopy(recipe['source_policy']) if recipe else default_source_policy(project))
    if recipe:
        if 'asset_manifest_relative' in recipe or 'asset_manifest_sha256' in recipe:
            relative=recipe.get('asset_manifest_relative');sha=recipe.get('asset_manifest_sha256')
            if not isinstance(relative,str) or not relative or not isinstance(sha,str) or not re.fullmatch('[a-f0-9]{64}',sha):
                raise ValueError('参考图片清单路径或版本格式无效')
            manifest=(db.DATA/relative).resolve()
            if not manifest.is_relative_to((db.DATA/'assets').resolve()):raise ValueError('参考图片目录越界')
            if not manifest.is_file() or manifest.stat().st_size>8*1024*1024 or hashlib.sha256(manifest.read_bytes()).hexdigest()!=sha:
                raise ValueError('参考图片清单缺失或版本不匹配，未建立方案目录')
            metadata.setdefault('proposal_assets',{'manifest_path':str(manifest),'sha256':sha,'scope':'reviewed_draft_illustrations'})
        for recipe_key,metadata_key in (('reference_context','proposal_reference_context'),('deliverables','proposal_deliverables')):
            if recipe_key in recipe:
                if not isinstance(recipe[recipe_key],dict):raise ValueError('参考资料清单配置格式无效')
                metadata.setdefault(metadata_key,copy.deepcopy(recipe[recipe_key]))
        metadata['proposal_recipe']={'id':recipe['id'],'version':recipe.get('version'),'tender_sha256s':recipe['tender_sha256s']}
        from . import proposal_context, proposal_deliverables
        candidate={**project,'metadata':metadata}
        proposal_context.load_context(candidate)
        proposal_deliverables.load_manifest(candidate)
    return metadata


REQUIREMENT_INPUT_FIELDS=('id','project_id','document_id','chunk_id','number','category','title','text','quote','locator','mandatory','score')


def plan_input_revision(project_id, requirements, conn=None):
    """Only planning semantics, not editable response/approval/activity fields."""
    sql="SELECT id,sha256,parse_status FROM documents WHERE project_id=? AND source_type='tender' ORDER BY id"
    documents=[dict(r) for r in conn.execute(sql,(project_id,))] if conn is not None else db.all(sql,(project_id,))
    semantic=[{k:r.get(k) for k in REQUIREMENT_INPUT_FIELDS} for r in sorted(requirements,key=lambda r:r['id'])]
    return {'version':'proposal-plan-inputs-1','tender_documents':documents,'requirements_sha256':digest(semantic)}


def validate_plan_inputs(project, requirements):
    plan=blueprint(project)
    if not plan:return
    expected_ids=[r['requirement_id'] for r in plan.get('ledger',[])]
    actual_ids=[r['id'] for r in requirements]
    if len(expected_ids)!=len(set(expected_ids)) or len(actual_ids)!=len(set(actual_ids)) or set(actual_ids)!=set(expected_ids):
        raise ValueError('方案目录关联的要求已增加、删除或重新解析；旧目录和历史已保留，请恢复对应快照或在新项目重新分析规划')
    saved=plan.get('input_revision')
    if saved:
        if saved!=plan_input_revision(project['id'],requirements):
            raise ValueError('方案目录的招标来源或要求内容版本已变化；未自动错配、删除或重建旧章节')
        return
    # Earlier unreleased profile snapshots did not have an input fingerprint.
    # Their embedded canonical requirements still prove the stored semantics;
    # do not silently enroll an old snapshot into a newly selected recipe.
    originals=plan.get('canonical_requirements',[])
    by_id={r['id']:r for r in requirements}
    covered=set()
    for item in originals:
        original=item.get('requirement') or {}
        fields=('category','text','quote')
        for rid in item.get('alias_ids',[]):
            if rid not in by_id or any(by_id[rid].get(k)!=original.get(k) for k in fields):
                raise ValueError('旧方案规划内容与当前要求不一致；请恢复对应快照或在新项目重新规划')
            covered.add(rid)
        rid=item.get('canonical_id')
        if rid in by_id and any(by_id[rid].get(k)!=original.get(k) for k in REQUIREMENT_INPUT_FIELDS):
            raise ValueError('旧方案规划的原文位置或要求版本已变化，未自动重建')
    if covered!=set(actual_ids):
        raise ValueError('旧方案缺少可核验的规划输入版本；请恢复对应快照或在新项目重新规划')


def _prior_content_history(project_id):
    if db.one("SELECT id FROM exports WHERE project_id=? LIMIT 1",(project_id,)):return True
    if db.one("SELECT id FROM jobs WHERE project_id=? AND mode='generate' AND status='succeeded' LIMIT 1",(project_id,)):return True
    for row in db.all('SELECT payload FROM project_snapshots WHERE project_id=?',(project_id,)):
        payload=row['payload'] or {}
        if payload.get('sections') or payload.get('before_sections') or payload.get('kind') in ('section_regeneration','chapter_outline_migration'):
            return True
    return False


def prepare_plan(project_id, requirements):
    """Opt in a new project only when the source has a recognized response TOC."""
    from . import proposal_blueprint, workflow
    project=db.one('SELECT * FROM projects WHERE id=?',(project_id,))
    if not project:raise ValueError('项目不存在')
    existing=db.all('SELECT * FROM sections WHERE project_id=? ORDER BY ordinal,id',(project_id,))
    plan=blueprint(project)
    if plan:
        return decorate_units(project,existing,requirements)
    if existing:return None
    input_revision=plan_input_revision(project_id,requirements)
    blocks=db.all("SELECT c.* FROM chunks c JOIN documents d ON c.document_id=d.id WHERE d.project_id=? AND d.source_type='tender' ORDER BY d.created_at,d.id,c.ordinal",(project_id,))
    plan=proposal_blueprint.build_blueprint(project_id,requirements,blocks,domain=project['domain'])
    if not plan.get('recognized'):return None
    if _prior_content_history(project_id):
        raise ValueError('此项目已有正文或交付历史，不能自动当作新项目套用目录与参考资料配置；请恢复对应快照或新建项目')
    plan['input_revision']=input_revision
    metadata=copy.deepcopy(project.get('metadata') or {})
    metadata['generation_profile']=PROFILE
    metadata['proposal_blueprint']=plan
    metadata=initialize_source_policy(project,metadata)
    plan=metadata['proposal_blueprint']
    rows=[]
    for index,item in enumerate(plan['sections']):
        identity=digest([project_id,PROFILE,item['section_key']])[:32]
        group_identity='group-'+digest([project_id,PROFILE,item['group_key']])[:20]
        item['section_id']=identity;item['outline_group_id']=group_identity
        rows.append({'id':identity,'project_id':project_id,'ordinal':index,
                     'title':item['title'],'outline_group_id':group_identity,
                     'outline_group_title':item['group_title'],'legacy_title':'',
                     'requirement_ids':item['requirement_ids'],'created_at':db.now(),'updated_at':db.now()})
    # The run may have been admitted before another client populated the editor.
    # Directory establishment is always atomic, independent of review toggles.
    with workflow.JOB_LOCK,db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        current=db.decode(conn.execute('SELECT * FROM projects WHERE id=?',(project_id,)).fetchone())
        if current['metadata']!=project['metadata'] or conn.execute('SELECT id FROM sections WHERE project_id=?',(project_id,)).fetchone():
            raise ValueError('目录规划期间项目或章节已变化，未覆盖新内容')
        current_requirements=[db.decode(r) for r in conn.execute('SELECT * FROM requirements WHERE project_id=?',(project_id,))]
        if plan_input_revision(project_id,current_requirements,conn)!=input_revision:
            raise ValueError('目录规划期间招标来源或要求发生变化，未保存过期目录')
        for row in rows:workflow._tx_insert(conn,'sections',row)
        conn.execute('UPDATE projects SET metadata=? WHERE id=?',(json.dumps(metadata,ensure_ascii=False),project_id))
    project={**project,'metadata':metadata}
    return decorate_units(project,db.all('SELECT * FROM sections WHERE project_id=? ORDER BY ordinal,id',(project_id,)),requirements)


def decorate_units(project, rows, requirements):
    plan=blueprint(project)
    if not plan:return None
    by_id={r['id']:r for r in requirements}
    specifications={s['section_id']:s for s in plan['sections']}
    if set(specifications)!={r['id'] for r in rows}:
        raise ValueError('方案目录与当前章节不一致，请核对变更，未自动删除或重建章节')
    validate_plan_inputs(project,requirements)
    if any(set(row['requirement_ids'])!=set(specifications[row['id']]['requirement_ids']) for row in rows):
        raise ValueError('方案章节的关联要求范围已变化，未自动重新分配旧正文')
    from . import compilation_outline
    rows=compilation_outline.projection(project,rows)['active']
    return [{**row,'requirements':[by_id[id] for id in row['requirement_ids'] if id in by_id],
             '_proposal_spec':display_spec(specifications[row['id']],row)} for row in rows]


def display_spec(spec, section):
    """Current editor labels share the original immutable semantic owner/inputs."""
    from .chapter_outline import effective_suboutline, fixed_format
    return {**spec,'title':section.get('title') or spec['title'],
            'group_title':section.get('outline_group_title') or spec['group_title'],
            'suboutline':effective_suboutline(section,spec),
            'directory_format':'procurement_form' if fixed_format(spec) else spec.get('directory_format','')}


def writing_project(project):
    """A workspace label such as R017验收 must not become the bid project name."""
    if not blueprint(project):return project
    from .documents import cover_identity
    chunks=db.all("SELECT c.* FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.project_id=? AND d.source_type='tender' AND c.ordinal<30 ORDER BY d.created_at,d.id,c.ordinal",(project['id'],))
    identity=cover_identity(project,chunks)
    return {**project,'name':identity['project_name'],'_cover_identity':identity,'_workspace_name':project['name']}


def decorate_section(project, section):
    from . import compilation_outline
    if not compilation_outline.is_enabled(project,section):
        raise ValueError('本节当前未编入目录，请先启用对应一级模块')
    plan=blueprint(project)
    if not plan:
        item=compilation_outline.specification(project,section)
        if not item:return section
        if set(item.get('requirement_ids',[]))!=set(section.get('requirement_ids',[])):
            raise ValueError('本节与已确认目录的要求范围不一致，未重新分配旧正文')
        return {**section,'_proposal_spec':display_spec(item,section)}
    validate_plan_inputs(project,db.all('SELECT * FROM requirements WHERE project_id=?',(project['id'],)))
    item=next((s for s in plan['sections'] if s['section_id']==section['id']),None)
    if item is None or set(item['requirement_ids'])!=set(section['requirement_ids']):
        raise ValueError('方案章节不在当前规划或关联范围已变化，未按旧目录生成')
    return {**section,'_proposal_spec':display_spec(item,section)}


def response_revision(section):
    return digest({k:section.get(k) for k in ('id','content','requirement_ids')})


def candidate_rows(project, ids=None):
    """Load document metadata once; never repeat a huge approval JSON per chunk."""
    from . import evidence_retrieval
    policy=(project.get('metadata') or {}).get('proposal_source_policy') or {}
    selected=policy.get('fact_document_ids') or []
    if not isinstance(selected,list) or not selected or not all(isinstance(x,str) for x in selected):return []
    selected=list(dict.fromkeys(selected))
    marks=','.join('?' for _ in selected)
    documents={d['id']:d for d in db.all('SELECT * FROM documents WHERE id IN ('+marks+')',selected)}
    sql='SELECT * FROM chunks WHERE document_id IN ('+marks+')';values=list(selected)
    if ids is not None:
        if not ids:return []
        sql+=' AND id IN ('+','.join('?' for _ in ids)+')';values.extend(ids)
    rows=[]
    restrict=policy.get('fact_chunk_ids_by_document') or {}
    for row in db.all(sql,values):
        d=documents[row['document_id']]
        if d['id'] in restrict and row['id'] not in restrict[d['id']]:continue
        rows.append({**row,'document_name':d['name'],'source_type':d['source_type'],
          'document_project_id':d['project_id'],'status':d['status'],'document_status':d['status'],
          'scope':d['scope'],'valid_until':d['valid_until'],'parse_status':d['parse_status'],
          'document_sha256':d['sha256'],'document_updated_at':d['updated_at']})
    return evidence_retrieval.filter_proposal_rows(rows,project,{id:d['metadata'] for id,d in documents.items()})[0]


def record_result(metadata, section, result):
    """Keep a response set tied to the exact body that produced it."""
    if metadata.get('generation_profile')!=PROFILE:return metadata
    metadata=copy.deepcopy(metadata)
    records=metadata.setdefault('proposal_section_results',{})
    records[section['id']]={'revision':response_revision(section),
      'responses':copy.deepcopy(result.get('responses',[])),
      'asset_ids':list(result.get('_selected_asset_ids',[])),
      'deliverable_provenance':copy.deepcopy(result.get('_deliverable_provenance',[])),
      'filled_cell_count':result.get('_filled_cell_count',0),
      'procurement_form_audit':copy.deepcopy(result.get('_form_audit',{})),
      'created_at':db.now()}
    if result.get('_review_index_model'):
        from . import review_index
        records[section['id']]['review_index']={'content_sha256':review_index.content_digest(section['content']),
                                               'model':copy.deepcopy(result['_review_index_model'])}
    return metadata


def project_responses(project, sections, requirements):
    """Export cannot silently combine new section prose with old response rows."""
    plan=blueprint(project)
    if not plan:return requirements,[]
    current={s['id']:s for s in sections};records=(project.get('metadata') or {}).get('proposal_section_results',{})
    specs={s['section_key']:s for s in plan['sections']};ledger={r['requirement_id']:r for r in plan['ledger']}
    output=[];issues=[]
    for requirement in requirements:
        item=ledger.get(requirement['id']);spec=specs.get(item.get('owner_section_key')) if item else None
        sid=spec.get('section_id') if spec else None;section=current.get(sid);record=records.get(sid)
        if item and item.get('disposition')=='internal':
            output.append({**requirement,'_proposal_disposition':'internal'});continue
        if (section and requirement.get('status') in ('confirmed','not_applicable')
                and requirement.get('updated_at','')>=section.get('updated_at','')):
            output.append({**requirement,'_proposal_section_id':sid,'_proposal_disposition':item['disposition']})
            continue
        matched=record and section and record['revision']==response_revision(section)
        response=next((r for r in record['responses'] if r['requirement_id']==requirement['id']),None) if matched else None
        if response:
            output.append({**requirement,'response':response['response'],'evidence_ids':response.get('evidence_ids',[]),
                           'status':'gap' if response.get('gap') else 'drafted',
                           '_proposal_section_id':sid,'_proposal_disposition':item['disposition']})
        else:
            # Empty cells are concrete unfinished delivery, never stale content
            # passed off as the result of a later generation/edit.
            output.append({**requirement,'response':'','evidence_ids':[],
                           'status':'gap',
                           '_proposal_section_id':sid,'_proposal_disposition':item.get('disposition') if item else 'unmapped'})
            issues.append({'requirement_id':requirement['id'],'section_id':sid,'kind':'response_revision_missing',
                           'message':'本节正文与响应表版本尚未对应，须重新生成或明确核对本节响应'})
    return output,issues
