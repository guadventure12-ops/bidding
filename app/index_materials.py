"""Identify missing index targets and search existing authorized material scopes."""
from __future__ import annotations
import hashlib
import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from . import db, content_review, proposal_runtime, compilation_outline


@lru_cache(maxsize=1)
def render_revision():
    folder=Path(__file__).resolve().parent
    names=('documents.py','proposal_export.py','proposal_pagination.py','review_index.py','export_outline.py','index_materials.py',
           'compilation_outline.py','outline_library.py','proposal_assets.py')
    return hashlib.sha256(b''.join((folder/name).read_bytes() for name in names)).hexdigest()


def free_form(spec):
    schema=spec.get('form_schema') or {}
    return (spec.get('content_kind')=='form' and spec.get('volume')=='technical'
            and bool(spec.get('score_factors'))
            and not re.search('评审.*索引',spec.get('group_title',''))
            and not schema.get('tables') and not schema.get('paragraphs'))


def substantive(section):
    text=section.get('content') or ''
    # A filled title, item number and blank value are a template, not a response.
    cleaned=[];placeholder_table=False
    for line in text.splitlines():
        if line.lstrip().startswith('|'):
            cells=[c.strip() for c in line.strip().strip('|').split('|')]
            if any(c in ('文件或填写内容','填写内容','响应内容','实际内容') for c in cells):
                placeholder_table=cells.index(next(c for c in cells if c in ('文件或填写内容','填写内容','响应内容','实际内容')))
                continue
            if placeholder_table is not False:
                value=cells[placeholder_table] if placeholder_table<len(cells) else ''
                if not re.sub(r'[_＿\s\-:：]','',value):continue
                cleaned.append(value);continue
        else:placeholder_table=False
        cleaned.append(line)
    return content_review.substantive('\n'.join(cleaned))


def status(section,spec,project=None,assets=None):
    body=substantive(section)
    result={'section_id':section['id'],'title':section.get('title',''),'group_title':section.get('outline_group_title',spec.get('group_title','')),
            'volume':spec.get('volume'),'ready':body,'kind':'ready' if body else 'missing_content',
            'reason':'已有实际正文' if body else '本节为空或只有待填写模板，不能用章节标题充当内容页数'}
    from . import workflow
    if spec.get('volume')=='pricing' and project is not None and not workflow.has_confirmed_quotation(project):
        result.update(ready=False,kind='project_data',reason='报价尚未人工确认，模型不能代为决定价格')
    if spec.get('content_kind')=='attachment':
        ids=re.findall(r'!\[[^\]]*\]\(asset:([A-Za-z0-9_-]+)\)',section.get('content') or '')
        if not ids:
            result.update(ready=False,kind='missing_attachment',reason='本节尚未装入实际附件，资料介绍不等于证明附件已装入')
        elif any(asset not in (assets or set()) for asset in ids):
            result.update(ready=False,kind='missing_attachment',reason='正文引用的附件有缺失或失效，需核对获准附件')
        else:result.update(ready=True,kind='ready',reason='实际附件已在本节引用；签章、时效与最终提交仍独立核对')
    return result


def available_assets(project):
    from . import proposal_assets,proposal_deliverables
    return {a['asset_id'] for a in proposal_assets.load_manifest(project)+proposal_deliverables.assets_for_export(project)}


def input_revision(project,sections=None):
    from . import workflow
    if sections is None:sections=db.all('SELECT * FROM sections WHERE project_id=? ORDER BY ordinal,id',(project['id'],))
    sections=sorted(sections,key=lambda s:(s.get('ordinal',0),s['id']))
    fields=('id','title','outline_group_id','outline_group_title','ordinal','content','requirement_ids','evidence_ids','status','user_edited','updated_at')
    value={'renderer':render_revision(),'date':date.today().isoformat(),'project':{k:project.get(k) for k in ('id','name','company_name','domain','project_number','buyer','deadline','quotation')},
           'sections':[{k:s.get(k) for k in fields} for s in sections],
           'sources':workflow.knowledge_fingerprint(),'tender':workflow.fingerprint(project['id']),
           'metadata':{k:(project.get('metadata') or {}).get(k) for k in ('proposal_blueprint','proposal_source_policy','proposal_assets','proposal_reference_context','proposal_deliverables','trusted_sources','outline_selection')},
           # The shared library is a source for future selections. Each project
           # owns its frozen copy; editing that library does not change its pages.
           'settings':{k:v for k,v in db.get_settings().items() if k!='outline_library'}}
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def scoped_sections(project):
    plan=proposal_runtime.blueprint(project) or {};all_sections=db.all('SELECT * FROM sections WHERE project_id=? ORDER BY ordinal,id',(project['id'],))
    all_sections=compilation_outline.projection(project,all_sections)['active']
    specs={s['section_id']:s for s in plan.get('sections',[])}
    has_price=any(re.search('报价|价格',str(f.get('title',''))) for f in plan.get('score_factors',[]))
    return [(s,specs[s['id']]) for s in all_sections if s['id'] in specs and
            (specs[s['id']].get('score_factors') or has_price and specs[s['id']].get('volume')=='pricing')]


def preview(project):
    from . import workflow,proposal_context,proposal_deliverables
    rows=[];asset_error=''
    try:assets=available_assets(project)
    except ValueError as exc:assets=set();asset_error=str(exc)
    for section,spec in scoped_sections(project):
        row=status(section,spec,project,assets)
        if row['ready']:continue
        row.update(can_fill=False,model_requests=0,candidates=[])
        if row['kind']=='project_data':rows.append(row);continue
        query=' '.join([spec.get('group_title',''),section['title'],*[f.get('text','') for f in spec.get('score_factors',[])]])
        hits=workflow.search_evidence(query,project.get('domain','general'),limit=6,project_id=project['id'])
        try:
            refs=proposal_context.search_context(project,section,limit=3)['evidence']
            hits += [r for r in refs if r.get('reference_role')!='procurement_requirement']
        except ValueError as exc:row['source_warning']=str(exc)
        seen=set()
        for hit in hits:
            did=hit.get('document_id');locator=hit.get('locator','')
            if (did,locator) in seen:continue
            seen.add((did,locator))
            document=db.one('SELECT name FROM documents WHERE id=?',(did,)) or {}
            row['candidates'].append({'id':hit.get('id'),'document_id':did,'name':hit.get('document_name') or document.get('name','已认可来源'),
                                      'locator':locator,'excerpt':str(hit.get('text',''))[:180],
                                      'role':hit.get('reference_role','approved_fact')})
            if len(row['candidates'])>=6:break
        if spec.get('content_kind')=='narrative' or free_form(spec):
            row.update(can_fill=bool(hits),model_requests=1 if hits else 0,
                       action='检索已有认可资料后，由本节模型补写；资料不足仍保留具体缺项' if hits else '已检索，但没有获准的可用来源，需补充资料')
        else:
            unit={**section,'requirements':[]}
            try:
                candidate=proposal_deliverables.apply_section(project,unit,spec)
                if candidate:
                    after=status({**section,'content':candidate[0]['content']},spec,project,assets)
                    row['can_fill']=after['ready']
                row['action']='重新装入本项目已认可、已选用的附件' if row['can_fill'] else '未找到可装入的获准附件；保留缺项，不扩大原选用范围'
            except ValueError as exc:row['action']=str(exc)
        if asset_error and row['kind']=='missing_attachment':row['source_warning']=asset_error
        rows.append(row)
    return {'revision':input_revision(project),'items':rows,'fillable_ids':[r['section_id'] for r in rows if r['can_fill']],
            'model_requests':sum(r['model_requests'] for r in rows if r['can_fill'])}
