"""Version-bound index page receipts; page numbers never come from a model."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from . import db, index_materials, proposal_runtime


def save_receipt(output,project,sections,report,revision):
    path=Path(output)
    volumes=report.get('volumes',{})
    pages={sid:{'page':page,'volume':volume} for volume,row in volumes.items()
           if row.get('page_numbers_refreshed') for sid,page in row.get('section_pages',{}).items()}
    if not pages:return
    receipt={'version':'index-pages-1','project_id':project['id'],'revision':revision,
             'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'section_pages':pages,
             'volume_pages':{k:v.get('page_count') for k,v in volumes.items()},'created_at':db.now()}
    path.with_name(path.stem+'.index-pages.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')


def current(project):
    if not proposal_runtime.blueprint(project):return None
    revision=index_materials.input_revision(project)
    receipt=None;export=None;reason='尚未对当前内容完成真实排版'
    for row in db.all('SELECT * FROM exports WHERE project_id=? ORDER BY created_at DESC LIMIT 20',(project['id'],)):
        file=Path(row['path']);sidecar=file.with_name(file.stem+'.index-pages.json')
        if not sidecar.is_file():continue
        try:data=json.loads(sidecar.read_text(encoding='utf-8'))
        except (ValueError,OSError):continue
        if data.get('project_id')!=project['id'] or data.get('version')!='index-pages-1':continue
        if data.get('revision')!=revision:
            reason='正文、附件范围或项目设置已变化，旧页数已失效，请重新排版';continue
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=data.get('file_sha256'):
            reason='对应导出文件缺失或被修改，请重新排版';continue
        receipt=data;export={'id':row['id'],'name':row['name'],'url':f"/api/exports/{row['id']}/download",'created_at':row['created_at']};break
    rows=[]
    try:assets=index_materials.available_assets(project)
    except ValueError:assets=set()
    for section,spec in index_materials.scoped_sections(project):
        row=index_materials.status(section,spec,project,assets)
        page=(receipt or {}).get('section_pages',{}).get(section['id'])
        row['page']=page.get('page') if page and row['ready'] else None
        row['page_status']='已按实际文件定位' if row['page'] is not None else row['reason'] if not row['ready'] else '另册或尚未排版'
        rows.append(row)
    return {'revision':revision,'valid':receipt is not None,'reason':'页数对应当前导出版本' if receipt else reason,
            'export':export,'items':rows,'volume_pages':(receipt or {}).get('volume_pages',{})}


def run_job(job_id,job):
    from . import workflow
    project=workflow.project(job['project_id'])
    expected=job.get('payload',{}).get('revision')
    if expected and expected!=index_materials.input_revision(project):raise ValueError('排版前项目内容已变化，请重新更新页数')
    current_job=db.one('SELECT * FROM jobs WHERE id=?',(job_id,))
    saved=(current_job.get('checkpoint') or {}).get('index_export')
    if saved and db.one('SELECT id FROM exports WHERE id=? AND project_id=?',(saved,project['id'])):
        state=current(project)
        if state and state['valid'] and state['export']['id']==saved:
            return {'message':'本次页数已按实际文件更新，未重复导出','export':state['export'],'index_pages':state}
    workflow.progress(job_id,90,'按当前正文和已装入附件排版，更新目录与索引页数（不调用模型）')
    exported=workflow.export_project(project['id'],'docx',False)
    checkpoint=(db.one('SELECT checkpoint FROM jobs WHERE id=?',(job_id,)) or {}).get('checkpoint') or {}
    workflow.checkpoint(job_id,{**checkpoint,'index_export':exported['id']})
    state=current(workflow.project(project['id']))
    if not state or not state['valid']:raise ValueError('已生成文件，但当前内容发生变化或页数未验证，请仅重新排版，不需再次生成正文')
    return {'message':'已按实际正文和附件更新页数；缺项和另册内容在页数明细中保留','export':state['export'],'index_pages':state}
