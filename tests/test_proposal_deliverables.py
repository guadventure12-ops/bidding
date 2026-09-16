"""Offline selected delivery facts, not model-filled identity/qualification claims."""
from copy import deepcopy
from datetime import date
import hashlib
import json
import socket

import pytest
from PIL import Image

from app import db, provider, proposal_deliverables as delivery
from app.document_assets import extract_document_assets
from app.proposal_generation import generate_proposal_section


def sha(value):return hashlib.sha256(value if isinstance(value,bytes) else value.encode()).hexdigest()


@pytest.fixture
def case(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path/'isolated');db.init()
    def forbidden(*args,**kwargs):raise AssertionError('No network or model for delivery forms')
    monkeypatch.setattr(socket.socket,'connect',forbidden);monkeypatch.setattr(socket,'getaddrinfo',forbidden)
    monkeypatch.setattr(provider,'chat_json',forbidden)
    p={'id':'p','name':'同一项目','company_name':'合成企业','domain':'archive','deadline':'2026年7月20日9时30分','metadata':{}}
    db.insert('projects',{**p,'created_at':db.now(),'updated_at':db.now()})
    src=db.DATA/'documents/source.md'
    body='已认可历史记录：甲方甲，电子档案项目，合同650000元。\n![界面](picture.png)'
    src.write_text(body,encoding='utf-8');Image.new('RGB',(20,10),'white').save(src.with_name('picture.png'))
    raw_sha=sha(src.read_bytes());entry_sha=sha(body)
    approval={'source_sha256':raw_sha,'mode':'parsed','entries':[{'id':'chunk','sha256':entry_sha,'text':body}]}
    db.insert('documents',{'id':'source','name':'同项目历史技术标_Final.docx','path':str(src),'sha256':raw_sha,
       'source_type':'knowledge','status':'approved','scope':'general','parse_status':'ready',
       'metadata':{'knowledge_approval':approval},'created_at':db.now(),'updated_at':db.now()})
    db.insert('chunks',{'id':'chunk','document_id':'source','ordinal':0,'text':body,'locator':'正文/历史业绩'})
    db.insert('documents',{'id':'tender','project_id':'p','name':'招标.docx','path':'not-read','sha256':'a'*64,
       'source_type':'tender','created_at':db.now(),'updated_at':db.now()})
    ref={'document_id':'source','source_sha256':raw_sha,'chunk_id':'chunk','chunk_sha256':entry_sha,'approved_text_sha256':entry_sha}
    schema={'tables':[{'rows':[{'cells':['客户','项目','金额'],'spans':[1,1,1]}, {'cells':['','',''],'spans':[1,1,1]}]}],'paragraphs':[]}
    spec={'title':'类似业绩资料','group_title':'类似业绩','section_key':'key','content_kind':'attachment','volume':'technical','form_schema':schema}
    unit={'id':'s','title':'类似业绩资料','requirements':[{'id':'r','text':'提供类似业绩'}],'_proposal_spec':spec}
    row={'id':'case1','use':'historical_fact','source_refs':[ref],
         'cells':[{'value':'甲方甲','state':'verified'},{'value':'电子档案项目','state':'verified'}, {'value':'65','state':'verified'}]}
    selected={'group_title':'类似业绩','section_key':'key','template_sha256':delivery.template_digest(schema),
              'tables':[{'schema_table_index':0,'header_rows':1,'rows':[row]}],'assets':[]}
    manifest={'version':delivery.VERSION,'tender_sha256s':['a'*64],'domain':'archive','bidder_name':'合成企业',
              'reference_date':'2026-07-20','personnel_use':'same_tender_candidates_only','sections':[selected]}
    path=db.DATA/'assets/manifest.json';path.parent.mkdir(parents=True,exist_ok=True)
    def save():
        path.write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
        p['metadata']['proposal_deliverables']={'manifest_path':str(path),'sha256':sha(path.read_bytes())}
    save()
    return {'p':p,'unit':unit,'spec':spec,'manifest':manifest,'selected':selected,'row':row,'ref':ref,'save':save,'path':path,'source':src}


def test_reviewed_fields_fill_procurement_table_without_model_or_business_writes(case):
    before={t:db.all('SELECT * FROM '+t) for t in ('projects','sections','documents','chunks','checks','reviews','exports')}
    result,evidence=generate_proposal_section('job',case['p'],case['unit'],'op')
    assert '| 甲方甲 | 电子档案项目 | 65 |' in result['content']
    assert result['_filled_cell_count']==3 and result['_model_calls']==0
    assert result['_deliverable_provenance'][0]['source_refs']==[case['ref']]
    assert evidence=={} and all(r['evidence_ids']==[] for r in result['responses'])
    assert all(r['gap'] for r in result['responses'])
    assert before=={t:db.all('SELECT * FROM '+t) for t in before}


def test_foreign_project_leaf_key_cannot_silently_drop_selected_delivery(case):
    case['spec']['section_key']='fresh-project-leaf'
    with pytest.raises(ValueError,match='其他项目的章节键'):
        delivery.apply_section(case['p'],case['unit'],case['spec'])
    # A portable recipe intentionally binds the exact tender and form instead.
    case['selected'].pop('section_key');case['save']()
    result,_=delivery.apply_section(case['p'],case['unit'],case['spec'])
    assert result['_filled_cell_count']==3


def test_portable_same_tender_manifest_applies_to_new_project_id_only_with_same_binding(case):
    case['selected'].pop('section_key');case['save']()
    other={**case['p'],'id':'new-project'}
    db.insert('projects',{**other,'created_at':db.now(),'updated_at':db.now()})
    db.insert('documents',{'id':'new-tender','project_id':other['id'],'name':'same-source.docx','path':'not-read',
        'sha256':'a'*64,'source_type':'tender','created_at':db.now(),'updated_at':db.now()})
    section={**case['spec'],'section_key':'new-project-semantic-key'}
    result,_=delivery.apply_section(other,{**case['unit'],'id':'new-leaf'},section)
    assert result['_filled_cell_count']==3 and result['_deliverable_provenance']
    db.update('documents','new-tender',{'sha256':'b'*64})
    with pytest.raises(ValueError,match='招标原件不匹配'):delivery.apply_section(other,case['unit'],section)


@pytest.mark.parametrize('mode',['manifest','tender','bidder','domain','date','template'])
def test_frozen_selection_and_project_binding_are_not_silently_expanded(case,mode):
    if mode=='manifest':case['path'].write_text('{}',encoding='utf-8')
    elif mode=='tender':case['manifest']['tender_sha256s']=['b'*64];case['save']()
    elif mode=='bidder':case['p']['company_name']='其他企业'
    elif mode=='domain':case['p']['domain']='expense'
    elif mode=='date':case['p']['deadline']='2026-08-20'
    else:case['spec']['form_schema']['tables'][0]['rows'][0]['cells'][0]='采购表已改'
    with pytest.raises(ValueError):delivery.apply_section(case['p'],case['unit'],case['spec'])


@pytest.mark.parametrize('change',['status','parse','scope','expiry','file','chunk','approved_range','origin','project_scope'])
def test_source_current_state_version_and_actual_approved_entry_checked(case,change):
    d=db.one('SELECT * FROM documents WHERE id=?',('source',));meta=d['metadata']
    if change=='file':case['source'].write_text('new source',encoding='utf-8')
    elif change=='chunk':db.update('chunks','chunk',{'text':'new parsed content'})
    elif change=='approved_range':
        meta['knowledge_approval']['entries'][0]['text']='已认可';db.update('documents','source',{'metadata':meta})
    elif change=='origin':meta['ai_generated']=True;db.update('documents','source',{'metadata':meta})
    elif change=='project_scope':meta['project_ids']=['different-project'];db.update('documents','source',{'metadata':meta})
    else:db.update('documents','source',{ {'status':'status','parse':'parse_status','scope':'scope','expiry':'valid_until'}[change]:
                                        {'status':'pending','parse':'failed','scope':'expense','expiry':'2020-01-01'}[change]})
    result,_=delivery.apply_section(case['p'],case['unit'],case['spec'])
    assert '甲方甲' not in result['content'] and result['_filled_cell_count']==0
    assert any(n.get('item_id')=='case1' and n['blocks_content'] for n in result['_internal_notes'])


def test_partial_conflicts_keep_supported_fields_and_one_precise_internal_issue(case):
    case['row']['cells'][2]={'value':'75','state':'conflict','reason':'表总额75万与图示73.65万有差异；缺少组成页'}
    case['save']();result,_=delivery.apply_section(case['p'],case['unit'],case['spec'])
    assert '| 甲方甲 | 电子档案项目 |  |' in result['content']
    assert '75' not in result['content']
    issue=next(n for n in result['_internal_notes'] if n['field']=='2')
    assert '73.65' in issue['message'] and issue['source_refs']==[case['ref']]


def test_certificate_expiry_uses_bound_historical_procurement_date_not_today(case):
    case['row']['use']='certificate_fact'
    case['row']['conditions']={'subject':'合成企业','valid_until':'2026-08-01','as_of':'tender_date'}
    case['save']();result,_=delivery.apply_section(case['p'],case['unit'],case['spec'])
    assert result['_filled_cell_count']==3
    case['row']['conditions']['valid_until']='2025-08-01';case['save']()
    result,_=delivery.apply_section(case['p'],case['unit'],case['spec'])
    assert result['_filled_cell_count']==0
    assert any('期限' in n['message'] for n in result['_internal_notes'])


def test_wrong_subject_certificate_never_becomes_bidder_certificate(case):
    case['row'].update(use='certificate_fact',conditions={'subject':'其他企业'})
    case['save']();result,_=delivery.apply_section(case['p'],case['unit'],case['spec'])
    assert result['_filled_cell_count']==0 and any('主体' in n['message'] for n in result['_internal_notes'])


def test_people_are_same_tender_candidates_not_current_appointments(case):
    case['row']['use']='same_project_candidate';case['row']['cells'][0]={'value':'拟任项目经理','state':'candidate'}
    case['save']();result,_=delivery.apply_section(case['p'],case['unit'],case['spec'])
    assert '拟投入人员候选方案' in result['content'] and '拟任项目经理' in result['content']
    assert any('未认定已任命或已驻场' in n['message'] for n in result['_internal_notes'])
    case['manifest'].pop('personnel_use');case['save']()
    result,_=delivery.apply_section(case['p'],case['unit'],case['spec'])
    assert result['_filled_cell_count']==0


def test_other_customer_exclusive_people_not_moved_into_this_project(case):
    case['row']['use']='same_project_candidate';case['save']()
    doc=db.one('SELECT * FROM documents WHERE id=?',('source',))
    doc['metadata']['other_customer_only']=True;db.update('documents','source',{'metadata':doc['metadata']})
    result,_=delivery.apply_section(case['p'],case['unit'],case['spec'])
    assert result['_filled_cell_count']==0
    assert any('其他客户专属' in n['message'] for n in result['_internal_notes'])


def test_selected_proof_images_use_manifest_and_remain_separate_from_attachment_completion(case):
    asset=extract_document_assets(case['source'],document_id='source',output_dir=db.DATA/'assets/proofs')['assets'][0]
    selection={'id':'proof','use':'historical_fact','source_refs':[case['ref']],'asset':asset,
               'review_status':'reviewed_for_draft','include_in_draft':True,'caption':'历史合同关键页'}
    case['selected']['assets']=[selection];case['save']()
    result,_=delivery.apply_section(case['p'],case['unit'],case['spec'])
    assert f'(asset:{asset["asset_id"]})' in result['content']
    assert result['_selected_asset_ids']==[asset['asset_id']]
    assert len(delivery.assets_for_export(case['p']))==1
    assert any('实际装入以本次导出记录为准' in n['message'] for n in result['_internal_notes'])
    assert '已附' not in result['content'] and '_attached' not in result
    selection['include_in_draft']=False;case['save']()
    result,_=delivery.apply_section(case['p'],case['unit'],case['spec'])
    assert result['_selected_asset_ids']==[]


def test_changed_picture_does_not_get_inserted_or_silently_replaced(case):
    asset=extract_document_assets(case['source'],document_id='source',output_dir=db.DATA/'assets/proofs')['assets'][0]
    case['selected']['assets']=[{'id':'proof','use':'historical_fact','source_refs':[case['ref']],'asset':asset,
        'review_status':'reviewed_for_draft','include_in_draft':True}];case['save']()
    from pathlib import Path
    Path(asset['path']).write_bytes(b'changed')
    result,_=delivery.apply_section(case['p'],case['unit'],case['spec'])
    assert result['_selected_asset_ids']==[] and any('候选图片proof未选入' in n['message'] for n in result['_internal_notes'])


def test_fixed_cells_can_fill_blanks_but_cannot_overwrite_labels(case):
    schema={'tables':[{'rows':[{'cells':['姓名',''],'spans':[1,1]},{'cells':['职务',''],'spans':[1,1]}]}],'paragraphs':[]}
    case['spec']['form_schema']=schema;case['selected']['template_sha256']=delivery.template_digest(schema)
    row={**case['row'],'positions':[[0,1],[1,1]],'cells':[{'value':'候选甲','state':'candidate'},{'value':'拟任经理','state':'candidate'}]}
    case['selected']['tables']=[{'schema_table_index':0,'layout':'fixed_cells','rows':[row]}];case['save']()
    result,_=delivery.apply_section(case['p'],case['unit'],case['spec'])
    assert '| 姓名 | 候选甲 |' in result['content'] and '| 职务 | 拟任经理 |' in result['content']
    row['positions'][0]=[0,0];case['save']()
    with pytest.raises(ValueError,match='标签'):delivery.apply_section(case['p'],case['unit'],case['spec'])


def test_merged_personnel_headers_map_all_eight_fields_without_column_shift(case):
    schema={'paragraphs':[],'tables':[{'rows':[
        {'cells':['本项目任职','姓名','职称','专业','执业或职业资格证明(如有)','驻场方式'],'spans':[1,1,1,1,3,1]},
        {'cells':['','','','','证书名称','级别','证号',''],'spans':[1]*8},
        {'cells':['']*8,'spans':[1]*8}]}]}
    case['spec']['form_schema']=schema;case['selected']['template_sha256']=delivery.template_digest(schema)
    labels=['拟任经理','候选甲','职称甲','专业甲','PMP','等级甲','','拟阶段驻场']
    case['row']['use']='same_project_candidate';case['row']['cells']=[{'value':v,'state':'candidate'} for v in labels]
    case['selected']['tables'][0]['header_rows']=2;case['save']()
    result,_=delivery.apply_section(case['p'],case['unit'],case['spec'])
    assert '| 本项目任职 | 姓名 | 职称 | 专业 | 执业证书名称 | 级别 | 证号 | 驻场方式 |' in result['content']
    assert '| '+' | '.join(labels)+' |' in result['content']
    case['p']['metadata'].pop('proposal_deliverables')
    result,_=generate_proposal_section('job',case['p'],case['unit'],'op')
    assert '| 本项目任职 | 姓名 | 职称 | 专业 | 执业证书名称 | 级别 | 证号 | 驻场方式 |' in result['content']


def test_qualification_declaration_keeps_procurement_words_as_proposed_text(case):
    case['p']['metadata'].pop('proposal_deliverables')
    case['spec'].update(group_title='资格文件',content_kind='form',form_schema={'tables':[],'paragraphs':[
        {'text':'资格审查资料承诺书'},{'text':'我方不存在下列情形之一：'},{'text':'（1）为采购人不具有独立法人资格的附属机构；'},
        {'text':'（2）被责令停业或破产状态的；'},{'text':'承诺单位：____（盖章）'},{'text':'附件5：其他文件'}]})
    result,_=generate_proposal_section('job',case['p'],case['unit'],'op')
    assert '我方不存在下列情形之一' in result['content'] and '附属机构' in result['content']
    assert '拟签文本' in result['content']
    assert any('集中确认' in n['message'] and '不逐子条款' in n['message'] for n in result['_internal_notes'])


@pytest.mark.parametrize('name',['商务条款偏离表','技术条款偏离表'])
def test_deviation_template_requires_explicit_conclusion(case,name):
    case['p']['metadata'].pop('proposal_deliverables');case['spec'].update(group_title=name,content_kind='form')
    result,_=generate_proposal_section('job',case['p'],case['unit'],'op')
    assert any('空表具有接受条款' in n['message'] and n['blocks_content'] for n in result['_internal_notes'])
    assert '无偏离' not in result['content']


def test_review_index_uses_export_shared_function_and_no_model(case,monkeypatch):
    from app import proposal_export
    case['p']['metadata'].pop('proposal_deliverables');case['spec'].update(group_title='评审索引表',content_kind='form')
    shared=proposal_export.index_content(case['p'])
    assert shared.startswith('| 序号 | 评审项目 | 评分标准 | 分值 | 页数 |')
    result,_=generate_proposal_section('job',case['p'],case['unit'],'op')
    assert result['content']==shared and result['_model_calls']==0


def test_new_import_ids_do_not_change_template_identity_but_content_does(case):
    a=deepcopy(case['spec']['form_schema']);b=deepcopy(a)
    a['source_refs']=[{'chunk_id':'old'}];b['source_refs']=[{'chunk_id':'new'}]
    assert delivery.template_digest(a)==delivery.template_digest(b)
    b['tables'][0]['rows'][0]['cells'][0]='different'
    assert delivery.template_digest(a)!=delivery.template_digest(b)


def test_review_context_uses_current_image_verified_amount_with_hash_and_not_original_claim(case):
    asset=extract_document_assets(case['source'],document_id='source',output_dir=db.DATA/'assets/proofs')['assets'][0]
    case['selected']['assets']=[{'id':'proof','use':'historical_fact','source_refs':[case['ref']],'asset':asset,
        'review_status':'reviewed_for_draft','include_in_draft':True,'caption':'历史合同金额页'}]
    case['row']['cells'][2]={'value':'73.65','state':'verified','basis':'reviewed_image_field'}
    case['row']['image_asset_ids']=[asset['asset_id']];case['save']()
    context=delivery.review_context(case['p'],case['spec'])
    amount=next(f for f in context['fields'] if f['field']=='金额')
    assert amount['value']=='73.65' and amount['basis']=='reviewed_image_field'
    assert amount['image_refs'][0]['asset_id']==asset['asset_id'] and amount['image_refs'][0]['sha256']==asset['sha256']
    assert 'path' not in json.dumps(context) and 'bytes' not in json.dumps(context)
    from pathlib import Path
    Path(asset['path']).write_bytes(b'changed')
    assert not any(f['field']=='金额' for f in delivery.review_context(case['p'],case['spec'])['fields'])
    result,_=delivery.apply_section(case['p'],case['unit'],case['spec'])
    assert '73.65' not in result['content'] and result['_typed_gap_notes_complete'] is True
    assert len(result['responses'][0]['gap_reason'])<80


def test_review_context_excludes_private_number_columns(case):
    case['spec']['form_schema']['tables'][0]['rows'][0]['cells']=['姓名','证号','电话']
    case['selected']['template_sha256']=delivery.template_digest(case['spec']['form_schema'])
    for cell,value in zip(case['row']['cells'],['候选甲','123456789012345678','13900001111']):cell['value']=value
    case['save']();context=delivery.review_context(case['p'],case['spec'])
    text=json.dumps(context,ensure_ascii=False)
    assert '候选甲' in text and '123456789012345678' not in text and '13900001111' not in text


def test_exact_empty_identity_fields_fill_only_known_project_values(case):
    from app.proposal_generation import _template_fields
    p={**case['p'],'project_number':'QA-2026','buyer':'采购单位甲'}
    texts=['供应商：   （盖章）','项目编号：____','项目名称：','采购人名称：',
           '法定代表人：____（签字）','日期： 年 月 日','供应商：原有公司（盖章）','供应商不存在的情形：____']
    result=_template_fields({'paragraphs':[{'text':t} for t in texts]},p)
    assert '供应商：合成企业（盖章）' in result and '项目编号：QA-2026' in result
    assert '项目名称：同一项目' in result and '采购人名称：采购单位甲' in result
    assert '法定代表人：____（签字）' in result and '日期： 年 月 日' in result
    assert '供应商不存在的情形：____' in result
    assert _template_fields({'paragraphs':[{'text':'项目编号：____'}]}, {})==['项目编号：____']
    schema={'paragraphs':[{'text':'资格审查资料承诺书'},{'text':'（采购人名称）：'},
        {'text':'我方在（项目名称）中做如下承诺：'},{'text':'（供应商名称）未发生____；'}, {'text':'日期： 年 月 日'}]}
    declaration=delivery.procurement_declaration(schema,p)
    assert '采购单位甲：' in declaration and '我方在同一项目中做如下承诺：' in declaration
    assert '合成企业未发生____；' in declaration and '日期： 年 月 日' in declaration
