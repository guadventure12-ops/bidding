"""Current document context, not automatic approval; isolated and model-free."""
import copy
import json
import socket

import pytest

from app import db, provider, workflow
from app.proposal_numeric_review import unsupported_numbers


@pytest.mark.parametrize('text',['### 6.3 项目现状理解','#### 5.5 日志审计','#### 4.3 日志追踪',
                                '通过1V1人工客服提供支持','### 3. 人员配置'])
def test_outline_and_service_identifiers_are_not_business_quantities(text):
    assert unsupported_numbers(text,[])==[]


def test_numbers_in_heading_and_next_to_identifiers_remain_checked():
    text='### 2.1 2026年7月计划：50万元、99.9%、3人、32GB、16核\n通过1V1人工客服，实际投入1人。'
    issues=unsupported_numbers(text,[])
    assert {r['value'] for r in issues}=={'2026年7月','50万元','99.9%','3人','32GB','16核','1人'}
    assert all(text[r['start']:r['end']]==r['value'] for r in issues)
    assert unsupported_numbers('### 2026.07.20 项目开始',[])[0]['value']=='2026.07.20'
    assert unsupported_numbers('### 3人团队',[])[0]['value']=='3人'


@pytest.mark.parametrize(('text','expected'),[
    ('### 50.0 万元报价','50.0 万元'),
    ('### 2.5 小时恢复目标','2.5 小时'),
    ('### 99.9 % 可用率','99.9 %'),
    ('### 2.5 日恢复目标','2.5 日'),
    ('### 3.5 人投入','3.5 人'),
    ('### 32.0 GB容量','32.0 GB'),
    ('### 16.0 核资源','16.0 核'),
])
def test_decimal_quantity_heading_is_not_silently_treated_as_outline(text,expected):
    issues=unsupported_numbers(text,[])
    assert [row['value'] for row in issues]==[expected]
    assert text[issues[0]['start']:issues[0]['end']]==expected


def test_outline_masks_only_prefix_and_keeps_following_real_heading_claims():
    text='### 6.3 项目报价50万元\n#### 5.5 日志保存90天\n### 2.1 恢复目标2.5小时'
    assert {row['value'] for row in unsupported_numbers(text,[])}=={'50万元','90天','2.5小时'}


def context_case(kind='form'):
    req={'id':'r','title':'初次报价一览表项目编号','text':'表中应载明项目编号。','response':'',
         '_proposal_disposition':'deliverable' if kind!='internal' else 'internal'}
    section={'id':'s','title':'报价一览与明细','status':'draft',
             'content':'项目编号：TEST-2026-7\n\n| 报价项目 | 含税报价（元） | 备注 |\n| --- | --- | --- |\n| 系统 |  |  |\n\n供应商：合成企业（盖章）\n日期： 年 月 日'}
    spec={'section_id':'s','content_kind':kind,'volume':'pricing','requirement_ids':['r']}
    return req,section,spec


def test_filled_identity_is_visible_but_empty_quote_signatures_are_not_filled_or_approved():
    req,section,spec=context_case();before=copy.deepcopy((req,section,spec))
    result=workflow._review_requirement_document_context(req,section,spec)
    assert '项目编号：TEST-2026-7' in str(result['excerpts'])
    assert result['section_status']=='draft' and result['scope']=='current_compilation_only_not_execution_or_approval'
    req2={**req,'title':'含税报价及签字盖章','text':'填写含税报价金额、日期并盖章。'}
    value=workflow._review_requirement_document_context(req2,section,spec)
    text='\n'.join(row['text'] for row in value['excerpts'])
    assert '| 系统 |  |  |' in text and '日期： 年 月 日' in text
    assert (req,section,spec)==before and req['response']==''


def test_internal_context_finds_own_ledger_row_after_hundreds_of_unrelated_rows():
    req,section,spec=context_case('internal');spec['volume']='internal'
    req.update(title='评审小组成员更换规则',text='评审小组应按规定更换成员。')
    section['content']='| 要求ID | 采购条款 | 来源 |\n| --- | --- | --- |\n'+''.join(f'| unrelated-{i} | 其他采购定义 | 段落{i} |\n' for i in range(400))+'| r | 评审小组应按规定更换成员。 | 原段落40 |'
    result=workflow._review_requirement_document_context(req,section,spec,max_chars=300)
    assert result['exact_requirement_ledger_row_present']
    assert '| r | 评审小组应按规定更换成员。 | 原段落40 |' in str(result['excerpts'])
    assert result['omitted_nonempty_lines']>300
    assert all('unrelated-' not in row['text'] for row in result['excerpts'])


def test_missing_owner_or_other_requirement_cannot_supply_unrelated_context():
    req,section,spec=context_case()
    assert workflow._review_requirement_document_context(req,None,spec) is None
    assert workflow._review_requirement_document_context(req,section,{**spec,'requirement_ids':['different']}) is None
    assert workflow._review_requirement_document_context(req,section,{**spec,'content_kind':'narrative'}) is None
    assert workflow._review_requirement_document_context(req,section,{**spec,'section_id':'other'}) is None
    section['content']=''
    result=workflow._review_requirement_document_context(req,section,spec)
    assert result['excerpts']==[] and not result['exact_requirement_ledger_row_present']


def test_current_edit_changes_context_hash_and_retains_real_blank_value():
    req,section,spec=context_case()
    before=workflow._review_requirement_document_context(req,section,spec)
    section['content']=section['content'].replace('TEST-2026-7','')
    after=workflow._review_requirement_document_context(req,section,spec)
    assert before['content_sha256']!=after['content_sha256']
    assert 'TEST-2026-7' not in str(after['excerpts'])
    assert any(row['text']=='项目编号：' for row in after['excerpts'])


def test_bounded_current_fields_do_not_expand_to_unrelated_items():
    req,section,spec=context_case();req.update(title='证书名称',text='提供证书名称。')
    fields=[{'field':'证书名称','value':'ISO9001','state':'verified','use':'certificate_fact'},
            {'field':'电话','value':'not-relevant','state':'missing'}]
    dc={'scope':'this_section_reviewed_delivery_fields_only','fields':fields,'review_basis':'synthetic field'}
    before=copy.deepcopy(dc)
    result=workflow._review_requirement_document_context(req,section,spec,dc,max_chars=120)
    assert result['reviewed_delivery_fields']['fields']==[fields[0]]
    assert result['reviewed_delivery_fields']['omitted_count']==1 and dc==before
    assert sum(len(r['text']) for r in result['excerpts'])<=120


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path/'isolated-review-context');db.init()
    def forbidden(*args,**kwargs):raise AssertionError('Network/model forbidden')
    monkeypatch.setattr(provider,'chat_json',forbidden)
    monkeypatch.setattr(socket.socket,'connect',forbidden);monkeypatch.setattr(socket,'getaddrinfo',forbidden)
    return monkeypatch


def test_review_target_wiring_keeps_all_targets_and_exposes_current_forms_and_ledger(isolated):
    from app import proposal_context,proposal_deliverables,proposal_runtime
    monkeypatch=isolated
    now=db.now();p={'id':'p','name':'内部工作区','company_name':'合成企业','domain':'archive','project_number':'TEST-2026-7','buyer':'采购单位','deadline':'2026-07-20'}
    form={'id':'s','project_id':'p','title':'报价一览与明细','ordinal':0,'content':context_case()[1]['content'],
          'requirement_ids':['r'],'evidence_ids':[],'created_at':now,'updated_at':now}
    internal={'id':'i','project_id':'p','title':'内部核对','ordinal':1,
              'content':'| 要求ID | 采购条款 | 来源 |\n| --- | --- | --- |\n| ir | 评审小组应按规定更换成员。 | 原段落40 |',
              'requirement_ids':['ir'],'evidence_ids':[],'created_at':now,'updated_at':now}
    specs=[{'section_id':'s','section_key':'ks','content_kind':'form','volume':'pricing','requirement_ids':['r']},
           {'section_id':'i','section_key':'ki','content_kind':'internal','volume':'internal','requirement_ids':['ir']}]
    ledger=[{'requirement_id':'r','owner_section_key':'ks','disposition':'deliverable'},
            {'requirement_id':'ir','owner_section_key':'ki','disposition':'internal'}]
    p['metadata']={'generation_profile':'technical_proposal','proposal_blueprint':{'recognized':True,'sections':specs,'ledger':ledger},
       'proposal_section_results':{'s':{'revision':proposal_runtime.response_revision(form),'responses':[{'requirement_id':'r','response':'','gap':True,'evidence_ids':[]}]}}}
    db.insert('projects',{**p,'created_at':now,'updated_at':now})
    db.insert('sections',form);db.insert('sections',internal)
    for rid,title,text in [('r','初次报价一览表项目编号','初次报价一览表载明项目编号TEST-2026-7。'),('ir','评审小组成员更换规则','评审小组应按规定更换成员。')]:
        db.insert('requirements',{'id':rid,'project_id':'p','title':title,'text':text,'quote':text,'category':'format','created_at':now,'updated_at':now})
    monkeypatch.setattr(workflow,'refresh_project_basics',lambda pid:db.one('SELECT * FROM projects WHERE id=?',(pid,)))
    monkeypatch.setattr(workflow,'review_fingerprint',lambda pid:'context-input-version')
    monkeypatch.setattr(workflow,'review_project',lambda pid:[])
    monkeypatch.setattr(workflow,'_requirement_source_context',lambda req:{})
    monkeypatch.setattr(workflow,'_valid_evidence',lambda *a,**kw:[])
    monkeypatch.setattr(provider,'key_configured',lambda:True)
    monkeypatch.setattr(proposal_context,'load_context',lambda p:{'enabled':False})
    monkeypatch.setattr(proposal_deliverables,'review_context',lambda *a:{'fields':[]})
    captured=[]
    def review(job,key,batch,prompt,project_id,cancel=None):
        captured.extend(copy.deepcopy(batch))
        assert '不能仅因独立response栏为空' in prompt
        return {'assessments':[{'target_id':t['target_id'],'verdict':'uncertain','reason':'offline capture only; no automatic approval'} for t in batch]}
    monkeypatch.setattr(workflow,'_review_with_repairs',review)
    before={table:db.all('SELECT * FROM '+table+' ORDER BY id') for table in ('projects','sections','requirements','exports','project_snapshots')}
    db.insert('jobs',{'id':'test-review','project_id':'p','mode':'review','status':'running','created_at':now})
    result=workflow.run_review('test-review',db.one('SELECT * FROM jobs WHERE id="test-review"'))
    assert result['reviewed_targets']==4
    target=next(t for t in captured if t['target_id']=='R:r')
    assert target['response']=='' and 'TEST-2026-7' in str(target['current_document_context']['excerpts'])
    assert next(t for t in captured if t['target_id']=='R:ir')['current_document_context']['exact_requirement_ledger_row_present']
    assert before=={table:db.all('SELECT * FROM '+table+' ORDER BY id') for table in before}
    assert len(db.one('SELECT * FROM reviews WHERE project_id="p"')['findings'])==4


def test_review_prompt_reuses_resolved_writing_identity_without_mutating_workspace(isolated):
    from app import proposal_runtime
    p={'id':'p','name':'内部工作区标签','project_number':'','buyer':'采购方','company_name':'合成企业','deadline':'',
       'metadata':{'generation_profile':'technical_proposal','field_conflicts':{'project_number':['source-A','source-B']}}}
    before=copy.deepcopy(p)
    isolated.setattr(proposal_runtime,'writing_project',lambda original:{**original,'name':'原采购项目名称','_workspace_name':original['name']})
    prompt=workflow._review_prompt(p,[])
    assert '原采购项目名称' in prompt and '内部工作区标签' not in prompt
    assert '"project_number": ""' in prompt and p==before
