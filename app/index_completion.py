"""Explicitly previewed index gaps reuse single-section generation and snapshots."""
import copy
import hashlib
from . import db,index_materials,section_generation,provider


def admit(project,revision,ids):
    plan=index_materials.preview(project)
    if revision!=plan['revision']:raise ValueError('索引关联正文或资料已变化，请重新预览缺项范围')
    if not ids or len(ids)!=len(set(ids)) or not set(ids)<=set(plan['fillable_ids']):
        raise ValueError('补全范围必须是本次预览明确列出的可补全章节')
    sections=db.all('SELECT * FROM sections WHERE project_id=? ORDER BY ordinal,id',(project['id'],))
    return {'revision':revision,'source_revision':index_materials.input_revision(project,[]),'ids':list(ids),'section_hashes':{s['id']:section_generation.digest(s) for s in sections}}


def run(job_id,job):
    from . import workflow,index_pages,proposal_runtime
    payload=job['payload'];project_id=job['project_id'];request=payload['request_id']
    saved=db.one('SELECT * FROM project_snapshots WHERE project_id=? AND label=?',(project_id,section_generation.KIND+':'+request))
    outcomes=[]
    if not saved:
        plan=payload.get('index_completion_plan')
        if payload.get('complete_index_gaps') and not plan:raise ValueError('缺少已确认的补全范围，未扩大单节任务')
        if plan:
            if index_materials.input_revision(workflow.project(project_id),[])!=plan.get('source_revision'):
                raise ValueError('资料、报价或项目设置已变化，未调用模型，请重新预览补全范围')
            expected=dict(plan['section_hashes'])
            for sid in plan['ids']:
                child_id=hashlib.sha256((request+':'+sid).encode()).hexdigest()[:32]
                prior=db.one('SELECT * FROM project_snapshots WHERE project_id=? AND label=?',(project_id,section_generation.KIND+':'+child_id))
                if prior:
                    expected[sid]=section_generation.digest(prior['payload']['after'])
            actual=db.all('SELECT * FROM sections WHERE project_id=? ORDER BY ordinal,id',(project_id,))
            if {s['id']:section_generation.digest(s) for s in actual}!=expected:
                raise ValueError('已预览章节被修改，保留新内容，请重新预览补全范围')
            for index,sid in enumerate(plan['ids']):
                if index_materials.input_revision(workflow.project(project_id),[])!=plan['source_revision']:
                    raise ValueError('补全期间资料或项目设置变化，已完成内容保留，未继续调用模型')
                if workflow.cancelled(job_id):raise provider.Cancelled('任务已取消，已保存章节和快照保留')
                section,p,reqs,version=section_generation.inputs(sid)
                child_id=hashlib.sha256((request+':'+sid).encode()).hexdigest()[:32]
                workflow.progress(job_id,5+60*index/max(1,len(plan['ids'])),'查找获准资料并补全：'+section['title'])
                instruction=('检索本项目已有认可资料与附件，补全本节实际响应。仅使用真实来源中的内容，保留适用范围和条件。'
                             '费用减免、免费维护、赠送服务、额外人天和增值优惠必须有直接依据，不能从通用产品介绍推导承诺。'
                             '找不到的资料、附件或项目决定集中记录gap_reason，不能用标题、空表或待确认套话充当实质内容。')
                child={'project_id':project_id,'payload':{'section_id':sid,'section_title':section['title'],'revision':version,
                    'outline_revision':section_generation.digest(workflow._outline_identity(section)),'request_id':child_id,'instruction':instruction,
                    '_force_revision_checks':True}}
                try:
                    result=section_generation.run_one(job_id,child)
                    now=db.one('SELECT * FROM sections WHERE id=?',(sid,));spec=proposal_runtime.decorate_section(workflow.project(project_id),now)['_proposal_spec']
                    current_project=workflow.project(project_id)
                    try:assets=index_materials.available_assets(current_project)
                    except ValueError:assets=set()
                    status=index_materials.status(now,spec,current_project,assets)
                    outcomes.append({'section_id':sid,'title':now['title'],'state':'saved','material_ready':status['ready'],
                                     'reason':status['reason'],'snapshot_id':result.get('snapshot_id')})
                    expected[sid]=section_generation.digest(now)
                except provider.Cancelled:raise
                except (ValueError,provider.ProviderError) as exc:
                    outcomes.append({'section_id':sid,'title':section['title'],'state':'failed','material_ready':False,'reason':str(exc)})
                    workflow.progress(job_id,5+60*(index+1)/max(1,len(plan['ids'])),'本节仍有缺项：'+section['title'])
                actual=db.all('SELECT * FROM sections WHERE project_id=? ORDER BY ordinal,id',(project_id,))
                if {s['id']:section_generation.digest(s) for s in actual}!=expected:
                    raise ValueError('补全过程中内容发生变化，已保存快照保留，未覆盖其他修改')
                if index_materials.input_revision(workflow.project(project_id),[])!=plan['source_revision']:
                    raise ValueError('补全期间资料或项目设置变化，请重新预览剩余范围')
        section,p,_,version=section_generation.inputs(payload['section_id'])
        current_payload={**payload,'revision':version if plan else payload['revision'],'_force_revision_checks':True}
        result=section_generation.run_one(job_id,{'project_id':project_id,'payload':current_payload})
    else:
        result={'section_id':payload['section_id'],'snapshot_id':saved['id'],'sections':1,'message':'本次索引已保存，未重复生成'}
        checkpoint=(db.one('SELECT checkpoint FROM jobs WHERE id=?',(job_id,)) or {}).get('checkpoint') or {}
        outcomes=checkpoint.get('index_completion_outcomes',[])
    checkpoint=(db.one('SELECT checkpoint FROM jobs WHERE id=?',(job_id,)) or {}).get('checkpoint') or {}
    workflow.checkpoint(job_id,{**checkpoint,'index_completion_outcomes':outcomes})
    result['completion_results']=outcomes
    if payload.get('update_index_pages'):
        try:
            result.update(index_pages.run_job(job_id,{'project_id':project_id,'payload':{}}))
        except (ValueError,RuntimeError) as exc:
            result.update(pagination_error=str(exc),message='索引正文已保存，但页数更新未完成：'+str(exc)+'。可仅点击“排版并更新页数”重试，无需重复生成正文。')
    return result
