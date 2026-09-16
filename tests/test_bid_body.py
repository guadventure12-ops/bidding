"""BUG-2: internal notes leave prose, but never become fulfilled facts."""
import copy
import pytest
from docx import Document
from fastapi.testclient import TestClient
from app import bid_body as body, content_review as cr, db, workflow, provider
from app.documents import compose_docx
from app.main import app


@pytest.mark.parametrize('note',[
    '【待补充：营业执照复印件，并加盖响应人公章】',
    '### 七、待补充事项汇总\n1. 营业执照复印件（加盖公章）。\n2. 正式签署件。',
    '七、待补充事项汇总\n1. 股权结构说明。',
    '**编制说明**\n请由企业补齐材料后提交。',
    'TODO: 核对人员姓名',
    '待确认：项目负责人姓名',
    '### 本章节待补充材料清单\n| 材料 | 要求 |\n|---|---|\n|营业执照|盖章|',
    '### 8.13 待确认事项\n1. 负责人资料。',
    '### 十、待澄清与待补充事项汇总\n1. 上线日期。',
    '**待补充事项汇总**：\n1. 报价资料。',
    '## 一、本章编制说明与项目级生效选项\n编制时需核对选项。',
    '本章涉及的待补充事项汇总如下：\n1. 证明资料。',
    '- 【待企业确认的承诺模板】我方未被责令停业。',
    '【待采购人澄清：实际上线顺序和日期】',
    '【待企业及实施确认：实际交付版本及适用性】',
])
def test_internal_notes_leave_body(note):
    formal='# 正文\n正式技术说明，数量为123。\n'
    result=body.separate(formal+note+'\n## 后续方案\n保留后续正文。')
    assert note not in result['content']
    assert formal in result['content'] and '## 后续方案\n保留后续正文。' in result['content']
    assert result['items'] and note in ''.join(i['raw'] for i in result['items'])
    assert body.separate(result['content'])['removed']==[]


def test_preserve_legitimate_business_words_values_and_images():
    text='# 功能说明\n系统支持待确认状态、待办任务管理及缺失材料提醒。\n| 参数 | 值 |\n|---|---|\n| 比例 | 【10】% |\n![结构图](x.png)\n## 材料清单\n1. 交付文件。\n'
    assert body.separate(text)['content']==text


def test_mixed_claim_not_left_as_verified_fact():
    text='系统已实现XBRL。 【待补充：该能力未核实】\n正常正文保留。'
    result=body.separate(text)
    assert '系统已实现XBRL' not in result['content']
    assert result['uncertain'] and result['items'][0]['blocks_content']
    assert '系统已实现XBRL' in result['items'][0]['raw']


def test_table_shape_and_real_price_preserved():
    text='| 名称 | 人员 | 金额 |\n|---|---|---|\n| 实施 | 【待确认：人员姓名】 | 12000 |\n'
    result=body.separate(text)
    assert result['content'].count('|')==text.count('|')
    assert '12000' in result['content'] and '待确认' not in result['content']
    assert '人员姓名' in result['items'][0]['message']


@pytest.mark.parametrize('text', [
    '本章接口对接方案未提供，需补齐。',
    '以下为待企业确认的300万元报价及交付日期。',
    '【待补充：法定代表人姓名、日期及签章】',
    '【待确认：产品版本号及附件扫描件】',
    '【待补充：报价\n金额】',
])
def test_real_missing_facts_always_block_content(text):
    split=body.separate(text)
    assert split['items'] and all(i['blocks_content'] for i in split['items'])
    assert not split['content'].strip()


def test_numbered_attachment_is_delivery_only():
    split=body.separate('1. 【待补充：营业执照复印件】')
    assert not split['items'][0]['blocks_content'] and split['items'][0]['blocks_delivery']


def test_literal_technical_example_preserved():
    text='```json\n{"description":"【待确认：测试状态】","version":123}\n```\n'
    assert body.separate(text)['content']==text


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path/'data')
    monkeypatch.setenv('MX_TESTING','1')
    monkeypatch.setenv('LANGFUSE_ENABLED','false')
    monkeypatch.setattr(provider,'chat_json',lambda *a,**k:pytest.fail('paid request forbidden'))
    db.init()
    db.insert('projects',{'id':'p','name':'合成项目','company_name':'合成企业','created_at':db.now(),'updated_at':db.now()})
    for id in ('s','other'):
        db.insert('sections',{'id':id,'project_id':'p','ordinal':0 if id=='s' else 1,'title':'章节'+id,
             'content':'# 说明\n保留实质内容及数字123。\n【待补充：报价金额】\n','created_at':db.now(),'updated_at':db.now()})
    return TestClient(app),tmp_path


def test_save_routes_notes_and_does_not_approve(isolated):
    client,_=isolated
    response=client.patch('/api/sections/s',json={'content':'正式正文123。\n【待补充：报价金额】','status':'draft'})
    assert response.status_code==200,response.text
    assert response.json()['moved_notes']==1
    section=db.one('SELECT * FROM sections WHERE id="s"')
    assert '待补充' not in section['content']
    todos=cr.assessments('p')[1]
    assert any('报价金额' in t['message'] and t['blocks_content'] for t in todos)
    assert client.patch('/api/sections/s',json={'status':'approved'}).status_code==400
    repeat=client.patch('/api/sections/s',json={'content':section['content'],'status':'draft'})
    assert repeat.json()['moved_notes']==0


def test_all_prompt_body_remains_empty_and_blocked(isolated):
    client,_=isolated
    response=client.patch('/api/sections/s',json={'content':'# 标题\nTODO: 填写报价','status':'draft'})
    assert response.status_code==200
    assert not cr.substantive(response.json()['section']['content'])
    assert client.patch('/api/sections/s',json={'status':'approved'}).status_code==400


def test_batch_preview_apply_scope_undo(isolated):
    _,_=isolated
    before=db.one('SELECT * FROM sections WHERE id="s"')
    other=db.one('SELECT * FROM sections WHERE id="other"')
    plan=cr.preview('p',['s'],'separate_notes')
    assert db.one('SELECT * FROM sections WHERE id="s"')==before
    assert plan['migrated_todos'] and not plan['assessments'][0]['eligible']
    with pytest.raises(ValueError):cr.execute('p',['s'],'separate_notes',plan['token'],False)
    result=cr.execute('p',['s'],'separate_notes',plan['token'],True)
    assert result['changed']==1 and result['approved']==0
    assert db.one('SELECT * FROM sections WHERE id="other"')==other
    assert '待补充' not in db.one('SELECT * FROM sections WHERE id="s"')['content']
    assert cr.preview('p',['s'],'separate_notes')['summary']['affected']==0
    merged=cr.assessments('p')[1]
    amount=[t for t in merged if '报价金额' in t['message']]
    assert len(amount)==1 and set(amount[0]['section_ids'])=={'s','other'}
    cr.undo(result['snapshot_id'],True)
    assert db.one('SELECT * FROM sections WHERE id="s"')==before


def test_generator_keeps_gaps_outside_response(isolated):
    result={'content':'# 正文\n实质内容。\n【待确认：报价金额】','responses':[{'requirement_id':'r','response':'TODO: 完成附件签章','evidence_ids':[],'gap':False}]}
    output=body.normalize_result(result)
    assert '待确认' not in output['content'] and 'TODO' not in output['responses'][0]['response']
    assert output['responses'][0]['gap']
    assert output['_internal_notes']
    assert body.normalize_result(output)==output
    section=db.one('SELECT * FROM sections WHERE id="s"')
    metadata=cr.generation_metadata(workflow.project('p'),section,output)
    assert any(t['blocks_content'] for t in metadata['content_todos'])


def test_docx_body_no_reinjection_and_sources_untouched(tmp_path):
    chapters=[{'title':'正文','content':'## 正文\n保留数字123。\n【待补充：营业执照复印件】\n### 七、待补充事项汇总\n1. 签章附件。'}]
    original=copy.deepcopy(chapters)
    output=tmp_path/'sample.docx'
    result=compose_docx({'name':'合成项目','company_name':'企业'},[],chapters,str(output))
    doc=Document(output)
    text='\n'.join(p.text for p in doc.paragraphs)
    assert '待补充' not in text and '待补充事项汇总' not in text and '保留数字123' in text
    assert result['internal_notes'] and chapters==original


def test_md_export_does_not_mutate_source(isolated):
    _,folder=isolated
    old=db.one('SELECT * FROM sections WHERE id="s"')
    result=workflow.export_project('p','md',False)
    from pathlib import Path
    text=Path(result['path']).read_text(encoding='utf-8')
    assert '【待补充' not in text and '123' in text
    assert db.one('SELECT * FROM sections WHERE id="s"')==old
