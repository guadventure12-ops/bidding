import copy
import pytest
from test_review_rules import local
from app import db, chapter_outline, proposal_runtime as pr, workflow, content_review


def tender():
    db.insert('documents',{'id':'t','project_id':'p','name':'采购文件.docx','path':'unused.docx','sha256':'a'*64,
      'source_type':'tender','parse_status':'ready','created_at':db.now(),'updated_at':db.now()})
    for index,text in enumerate(('商务、技术文件目录','1.需求理解与解决方案','2.功能模块设计及系统功能演示','附件10：评审索引表')):
        db.insert('chunks',{'id':f'c{index}','document_id':'t','ordinal':index,'text':text,'locator':f'正文 / 段落 {index+1}'})
    db.insert('requirements',{'id':'r','project_id':'p','document_id':'t','chunk_id':'c1','category':'technical',
      'title':'需求理解','text':'提供需求理解方案','quote':'需求理解与解决方案','locator':'正文 / 段落 2','created_at':db.now(),'updated_at':db.now()})
    return db.all('SELECT * FROM requirements')


def test_existing_ids_body_approval_and_metadata_are_never_replanned(local):
    reqs=tender();db.update('sections','s',{'status':'approved'})
    old=db.one('SELECT * FROM sections WHERE id="s"');p=workflow.project('p')
    assert pr.prepare_plan('p',reqs) is None
    assert db.one('SELECT * FROM sections WHERE id="s"')==old
    assert workflow.project('p')==p


def test_source_directory_is_persisted_before_generation_and_repeat_reuses_ids(local):
    reqs=tender();db.execute('DELETE FROM sections')
    first=chapter_outline.generation_plan('p',reqs,workflow.CATEGORY_LABELS)
    p=workflow.project('p');assert pr.blueprint(p)['recognized']
    assert all(row.get('_proposal_spec') for row in first)
    assert all(row['content']=='' and row['status']=='draft' for row in first)
    assert {row['requirement_id'] for row in pr.blueprint(p)['ledger']}=={'r'}
    assert chapter_outline.generation_plan('p',reqs,workflow.CATEGORY_LABELS)==first
    assert not db.all('SELECT * FROM jobs')
    assert local.get('/api/projects/p').json()['chapter_outline']['section_count']==len(first)


def test_unknown_source_keeps_the_legacy_path(local):
    db.execute('DELETE FROM sections')
    assert pr.prepare_plan('p',[]) is None
    assert 'generation_profile' not in workflow.project('p')['metadata']


def proposal_state():
    section={'id':'s','content':'新正文','requirement_ids':['r']}
    plan={'recognized':True,'sections':[{'section_id':'s','section_key':'leaf'}],
      'ledger':[{'requirement_id':'r','owner_section_key':'leaf','disposition':'narrative'}]}
    metadata={'generation_profile':pr.PROFILE,'proposal_blueprint':plan}
    result={'responses':[{'requirement_id':'r','response':'与新正文对应的响应','evidence_ids':['e'],'gap':False}]}
    metadata=pr.record_result(metadata,section,result)
    return {'metadata':metadata},section,[{'id':'r','response':'旧响应','evidence_ids':['old']}]


def test_new_body_and_response_are_bound_to_the_same_revision():
    p,s,reqs=proposal_state();before=copy.deepcopy(reqs)
    rows,issues=pr.project_responses(p,[s],reqs)
    assert not issues and rows[0]['response']=='与新正文对应的响应'
    assert reqs==before
    rows,issues=pr.project_responses(p,[{**s,'content':'后续人工编辑'}],reqs)
    assert rows[0]['response']=='' and rows[0]['evidence_ids']==[]
    assert issues[0]['section_id']=='s'


def test_recording_one_leaf_does_not_replace_another_record():
    p,s,reqs=proposal_state();before=copy.deepcopy(p['metadata'])
    updated=pr.record_result(before,{'id':'other','content':'另一节','requirement_ids':[]},{'responses':[]})
    assert updated['proposal_section_results']['s']==before['proposal_section_results']['s']
    assert 'other' not in before['proposal_section_results']


def test_legacy_export_and_metadata_do_not_change():
    rows=[{'id':'r','response':'原响应'}];p={'metadata':{}}
    assert pr.project_responses(p,[],rows)==(rows,[])
    assert pr.record_result(p['metadata'],{}, {})=={}


def test_strict_sql_candidate_path_does_not_admit_selected_tender(local):
    tender();p=workflow.project('p');p['metadata'].update(generation_profile=pr.PROFILE,
      proposal_source_policy={'mode':'approved_facts_only','fact_document_ids':['t'],'reference_document_ids':[]})
    db.update('documents','t',{'status':'approved'})
    assert pr.candidate_rows(p)==[]
