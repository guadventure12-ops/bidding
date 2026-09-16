"""Synthetic ordered templates; no source-file, database, Word or model operations."""
import copy

import pytest

from app.procurement_forms import render_qualification, render_pricing


def fixture(parts,volume='qualification'):
    schema={'source_refs':[],'paragraphs':[],'tables':[]}
    for index,part in enumerate(parts):
        if isinstance(part,str) or isinstance(part,tuple):
            text,locator=(part,f'正文 / 段落 {index}') if isinstance(part,str) else part
            ref={'document_id':'source','chunk_id':f'p{index}','locator':locator,'quote':text}
            schema['source_refs'].append(ref);schema['paragraphs'].append({'text':text,'source':ref})
        else:
            sources=[];rows=[]
            for ri,values in enumerate(part['rows']):
                ref={'document_id':'source','chunk_id':f't{index}r{ri}','locator':f'正文 / 表格 {index} / 第 {ri+1} 行','quote':' | '.join(values).strip()}
                spans=part.get('spans',[None]*len(part['rows']))[ri] or [1]*len(values)
                sources.append(ref);schema['source_refs'].append(ref)
                rows.append({'row':ri+1,'cells':values,'spans':spans})
            schema['tables'].append({'table_key':['source',index],'rows':rows,'source_refs':sources})
    return {'title':'资格声明与证明资料' if volume=='qualification' else '报价一览与明细','volume':volume,'requirement_ids':['r1'],'form_schema':schema}


def project():
    return {'name':'【内部验收】错误的工作区名称','company_name':'合成供应商','buyer':'合成采购人','project_number':'TEST-2026',
            'actual_tender_name':'已核对电子档案项目','legal_representative':'不应自动填入的姓名','bank_account':'不应填入的账号','date':'不应自动填入的日期'}


def seven_forms():
    names=['响应函','法定代表人/负责人授权书','资格审查资料','采购代理服务费承诺书','诚信承诺书','保密承诺函','供应商控股及管理关系情况申报表']
    blocks=[]
    for i,name in enumerate(names,2):
        blocks += [f'附件{i}：{name}',name,f'我方就本附件{i}承诺完整履行原采购文件约定。',
                   '供应商名称：    （盖章）','法定代表人/负责人或者其委托代理人：    （签字）','日期： 年 月 日']
    return fixture(blocks),names


def test_seven_forms_keep_declarations_separate_signatures_dates_and_source_order():
    spec,names=seven_forms();before=copy.deepcopy(spec)
    result,evidence=render_qualification(project(),spec)
    content=result['content'];audit=result['_form_audit']
    positions=[content.index(f'### 附件{i}：{name}') for i,name in enumerate(names,2)]
    assert positions==sorted(positions)
    assert all(f'我方就本附件{i}承诺完整履行原采购文件约定。' in content for i in range(2,9))
    assert content.count('日期： 年 月 日')==7
    assert content.count('法定代表人/负责人或者其委托代理人：    （签字）')==7
    assert content.count('供应商名称：合成供应商（盖章）')==7
    assert len(audit['forms'])==7 and sum(f['date_fields'] for f in audit['forms'])==7
    assert audit['source_count']==audit['represented_source_count']==42
    assert not audit['declarations_approved'] and not audit['signed'] and not audit['sealed']
    assert evidence=={} and result['_model_calls']==0 and all(r['gap'] for r in result['responses'])
    assert spec==before


def test_tables_and_paragraphs_follow_source_refs_not_paragraph_or_table_arrays():
    spec=fixture(['附件3：授权书','委托事项原正文。',{'rows':[['姓名','','职务',''],['地址','']], 'spans':[[1,1,1,1],[1,3]]},'委托权限原正文。','日期： 年 月 日'])
    spec['form_schema']['paragraphs'].reverse()
    result,_=render_qualification(project(),spec)
    text=result['content']
    assert text.index('委托事项原正文。')<text.index('| 姓名 |')<text.index('委托权限原正文。')<text.index('日期：')
    table=next(b['table'] for b in result['_procurement_form_blocks'] if b['kind']=='table')
    assert table['rows'][1]['spans']==[1,3]
    assert '| 地址 |  |  |  |' in text


def test_only_precise_empty_identity_placeholders_are_filled():
    spec=fixture(['附件3：授权书','我（姓名）系（供应商名称）的负责人，授权（姓名）参加（项目名称）。',
                  '采购人：（采购人名称）','供应商：   （盖章）','账号：','开户银行：','法定代表人：（姓名）','日期： 年 月 日',
                  '采购代理账户：原采购已填账号1000','采购人：原采购已填主体'])
    result,_=render_qualification(project(),spec);text=result['content']
    assert '系合成供应商的负责人' in text and '参加已核对电子档案项目' in text
    assert text.count('（姓名）')==3
    assert '账号：\n' in text and '开户银行：' in text
    assert '原采购已填账号1000' in text and '采购人：原采购已填主体' in text
    assert '内部验收' not in text and '不应自动填入' not in text and '不应填入的账号' not in text


def test_actual_tender_name_comes_from_source_not_workspace_label():
    p=project();p.pop('actual_tender_name')
    spec=fixture(['附件3：授权书','我方参加（项目名称）响应。','项目名称：原采购电子档案项目'])
    result,_=render_qualification(p,spec)
    assert '我方参加原采购电子档案项目响应。' in result['content']
    assert '内部验收' not in result['content']
    spec=fixture(['附件3：授权书','我方参加（项目名称）响应。'])
    assert '（项目名称）' in render_qualification(p,spec)[0]['content']


def test_exact_known_buyer_prefix_reconciles_printed_long_and_short_project_names():
    p=project();p.pop('actual_tender_name')
    spec=fixture(['附件3：授权书','我方参加（项目名称）响应。','项目名称：合成采购人电子档案项目','采购项目：电子档案项目'])
    result,_=render_qualification(p,spec)
    assert '我方参加合成采购人电子档案项目响应。' in result['content']
    spec=fixture(['附件3：授权书','项目名称：原项目甲','采购项目：原项目乙'])
    with pytest.raises(ValueError,match='不同项目名称'):render_qualification(p,spec)


def test_explicit_guidance_goes_to_notes_but_uncertain_semantics_and_numeric_paragraph_stay():
    spec=fixture(['附件8：申报表','我方承担申报不实责任。','注：1.出资比例在50%以上称为控股。','2.如无相关情况，请填写“无”。',
                  '说明：我方承诺按原采购约定履约。','日期： 年 月 日','94',('94','页脚 footer1 / 段落 20')])
    result,_=render_qualification(project(),spec)
    assert '我方承担申报不实责任。' in result['content']
    assert '说明：我方承诺按原采购约定履约。' in result['content']
    assert '50%以上' not in result['content'] and '如无相关情况' not in result['content']
    assert result['content'].count('94')==1
    moved=[n for n in result['_internal_notes'] if n.get('classification')]
    assert len(moved)==3 and any(n.get('classification')=='source_non_body' for n in moved)
    assert all(n['source_refs'] and n['reason'] for n in moved)


def test_pricing_original_item_and_all_signing_fields_survive_without_confirmed_amount():
    spec=fixture(['附件16：初次报价一览表','项目名称：原采购电子档案项目','项目编号：TEST-2026',
                  {'rows':[['报价项目','含税报价（元）','备注'],['电子会计档案项目','','']]},
                  '报价要求：','▲1.供应商报价不得超过最高限价900000元。','2.本报价包含所有阶段税费。',
                  '供应商： （盖章）','供应商法定代表人/负责人或其授权委托人： （签字）','日期： 年 月 日'],volume='pricing')
    p=project();p['quotation']={'confirmed':True,'total_including_tax':'999.99'}
    result,_=render_pricing(p,spec)
    assert '| 报价项目 | 含税报价（元） | 备注 |' in result['content']
    assert '| 电子会计档案项目 |  |  |' in result['content']
    assert '999.99' not in result['content'] and '900000' not in result['content']
    assert '900000' in str(result['_internal_notes']) and '所有阶段税费' in str(result['_internal_notes'])
    assert '供应商法定代表人/负责人或其授权委托人： （签字）' in result['content']
    assert '日期： 年 月 日' in result['content']


def test_confirmed_total_fills_only_original_empty_money_cell_not_item_name():
    spec=fixture(['附件16：报价表',{'rows':[['报价项目','含税报价（元）','备注'],['电子会计档案项目','','']]},'日期： 年 月 日'],volume='pricing')
    quote={'confirmed':True,'total_including_tax':'1234.50','issues':[]}
    result,_=render_pricing(project(),spec,quote)
    assert '| 电子会计档案项目 | 1234.50 |  |' in result['content']
    assert '内部验收' not in result['content']
    quote['confirmed']=False
    assert '1234.50' not in render_pricing(project(),spec,quote)[0]['content']


def test_line_item_amount_mapping_uses_exact_names_and_keeps_original_detail_grid():
    spec=fixture(['附件16：分项报价表',{'rows':[['费用项目','数量','单价（元）','小计（元）','备注'],['软件甲','','','',''],['服务乙','','','',''],['合计','','','','']]}],volume='pricing')
    quote={'confirmed':True,'total_including_tax':'300','issues':[],
           'items':[{'name':'服务乙','quantity':'1','unit_price':'100','subtotal':'100'},{'name':'软件甲','quantity':'2','unit_price':'100','subtotal':'200'}]}
    result,_=render_pricing(project(),spec,quote)
    assert '| 软件甲 | 2 | 100 | 200 |  |' in result['content']
    assert '| 服务乙 | 1 | 100 | 100 |  |' in result['content']
    assert '| 合计 |  |  | 300 |  |' in result['content']
    assert len(result['_procurement_form_blocks'][1]['table']['rows'])==4


def test_unknown_price_column_mapping_does_not_guess_or_distribute_total():
    spec=fixture(['附件16：报价表',{'rows':[['报价项目','含税报价（万元）','备注'],['项目甲','',''],['项目乙','','']]}],volume='pricing')
    result,_=render_pricing(project(),spec,{'confirmed':True,'total_including_tax':'123456','issues':[]})
    assert '123456' not in result['content'] and '| 项目甲 |  |  |' in result['content']
    assert result['_form_audit']['represented_source_count']==4


@pytest.mark.parametrize('defect',['missing_refs','paragraph_mismatch','missing_table_row','bad_spans','interleaved_table'])
def test_ambiguous_or_incomplete_schema_fails_closed(defect):
    spec=fixture(['附件3：授权书',{'rows':[['姓名','职务'],['','']]},'原正文。'])
    schema=spec['form_schema']
    if defect=='missing_refs':schema['source_refs']=[]
    if defect=='paragraph_mismatch':schema['paragraphs'][0]['text']='被替换的原文'
    if defect=='missing_table_row':schema['tables'][0]['rows'].pop()
    if defect=='bad_spans':schema['tables'][0]['rows'][0]['spans']=[0,2]
    if defect=='interleaved_table':schema['source_refs'][2],schema['source_refs'][3]=schema['source_refs'][3],schema['source_refs'][2]
    with pytest.raises(ValueError):render_qualification(project(),spec)
