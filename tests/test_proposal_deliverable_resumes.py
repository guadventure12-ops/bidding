"""Offline procurement-span reflow; no production records, Word or models."""
from copy import deepcopy

import pytest

from app import proposal_deliverables as delivery


def resume_table():
    # Procurement cells are not the nine expanded grid columns.
    return {'rows':[
        {'cells':['姓名','','年龄','','执业资格证书(或上岗证书)名称(如有要求)',''], 'spans':[1,2,1,1,3,1]},
        {'cells':['职称','','学历','','拟在本项目任职',''], 'spans':[1,2,1,1,3,1]},
        {'cells':['工作年限',''], 'spans':[1,8]},
        {'cells':['驻场方式',''], 'spans':[1,8]},
        {'cells':['毕业学校','年毕业于           学校       专业'], 'spans':[1,8]},
        {'cells':['主要工作经历'], 'spans':[9]},
        {'cells':['时间','参加过的类似项目','担任职务','发包人及联系电话'], 'spans':[2,4,1,2]},
        {'cells':['','','',''], 'spans':[2,4,1,2]},
        {'cells':['','','',''], 'spans':[2,4,1,2]},
        {'cells':['','','',''], 'spans':[2,4,1,2]},
    ]}


def fill_table(table):
    rows=delivery._expanded_rows(table)
    values={(0,1):'候选甲',(0,4):'历史年龄待本次核对',(0,8):'PMP',
            (1,1):'原资料职称',(1,4):'本科',(1,8):'拟任项目经理',
            (2,1):'原资料工作年限',(3,1):'拟阶段驻场',
            (7,0):'2024年',(7,2):'历史项目甲',(7,6):'项目经理',
            (8,0):'2025年',(8,2):'历史项目乙',(8,6):'实施顾问'}
    for (row,col),value in values.items():rows[row][col]=value
    return rows,values


def test_resume_reflow_preserves_all_procurement_labels_values_and_real_blanks():
    table=resume_table();original=deepcopy(table);filled,_=fill_table(table)
    before=deepcopy(filled);result=delivery._resume_tables(table,filled)
    assert result['fields'][0]==['采购字段','候选资料']
    assert result['fields'][1:]==[
        ['姓名','候选甲'],['年龄','历史年龄待本次核对'],
        ['执业资格证书(或上岗证书)名称(如有要求)','PMP'],
        ['职称','原资料职称'],['学历','本科'],['拟在本项目任职','拟任项目经理'],
        ['工作年限','原资料工作年限'],['驻场方式','拟阶段驻场'],
        ['毕业学校','年毕业于           学校       专业']]
    assert result['experience']==[
        ['时间','参加过的类似项目','担任职务','发包人及联系电话'],
        ['2024年','历史项目甲','项目经理',''],
        ['2025年','历史项目乙','实施顾问',''],['','','','']]
    assert table==original and filled==before


def test_resume_uses_actual_span_boundaries_and_keeps_additional_fields():
    table=resume_table()
    # Different merged grid remains valid: no hardcoded six/nine-column rule.
    for row in table['rows']:row['spans']=[span*2 for span in row['spans']]
    table['rows'].insert(3,{'cells':['联系方式','','专业资格补充',''], 'spans':[3,6,3,6]})
    rows=delivery._expanded_rows(table)
    rows[0][2]='候选乙';rows[3][3]='合成联系方式';rows[3][12]=''
    result=delivery._resume_tables(table,rows)
    assert ['姓名','候选乙'] in result['fields']
    assert ['联系方式','合成联系方式'] in result['fields']
    assert ['专业资格补充',''] in result['fields']
    assert len(result['experience'][0])==4


@pytest.mark.parametrize('problem',['continuation_text','continuation_zero','unpaired_field','experience_grid','bad_span','unknown_section'])
def test_ambiguous_layout_is_not_silently_flattened_or_dropped(problem):
    table=resume_table();filled,_=fill_table(table)
    if problem=='continuation_text':filled[0][2]='不得丢弃的实际值'
    elif problem=='continuation_zero':filled[0][2]=0
    elif problem=='unpaired_field':
        table['rows'][4]={'cells':['学校','学校要求','未识别栏'], 'spans':[1,7,1]}
        filled[4]=delivery._expanded_rows({'rows':[table['rows'][4]]})[0]
    elif problem=='experience_grid':table['rows'][-1]['spans']=[1,5,1,2]
    elif problem=='bad_span':table['rows'][0]['spans']=[1,2,1,1,3]
    else:table['rows'][5]['cells']=['其他说明'];filled[5][0]='其他说明'
    before=deepcopy(filled)
    assert delivery._resume_tables(table,filled) is None
    assert filled==before


def test_candidate_integration_splits_tables_and_marks_heading_without_extra_facts(monkeypatch):
    table=resume_table();_,values=fill_table(table)
    schema={'tables':[table],'paragraphs':[]}
    spec={'content_kind':'attachment','volume':'technical','group_title':'人员配置',
          'section_key':'s','form_schema':schema}
    item={'id':'candidate','use':'same_project_candidate','source_refs':[{'chunk_id':'c'}],
          'positions':[list(key) for key in values],
          'cells':[{'value':value,'state':'candidate'} for value in values.values()]}
    selection={'group_title':'人员配置','section_key':'s','template_sha256':delivery.template_digest(schema),
               'tables':[{'schema_table_index':0,'layout':'fixed_cells','rows':[item]}]}
    monkeypatch.setattr(delivery,'load_manifest',lambda p:{'sections':[selection]})
    monkeypatch.setattr(delivery._Validator,'item',lambda self,item:item['source_refs'])
    result,_=delivery.apply_section({'id':'synthetic','metadata':{}},{'id':'s','requirements':[]},spec)
    text=result['content']
    assert text.startswith('### 拟投入候选人员简历\n\n| 采购字段 | 候选资料 |')
    assert '\n\n#### 主要工作经历\n\n| 时间 | 参加过的类似项目 | 担任职务 | 发包人及联系电话 |' in text
    assert '| 执业资格证书(或上岗证书)名称(如有要求) | PMP |' in text
    assert '| 毕业学校 | 年毕业于           学校       专业 |' in text
    assert text.count('候选甲')==1 and result['_filled_cell_count']==len(values)
    assert result['_model_calls']==0
    assert any('未认定已任命或已驻场' in note['message'] for note in result['_internal_notes'])
    assert all('已任命' not in line for line in text.splitlines())
    # A write in a merged continuation retains the complete grid on fallback.
    item['positions'].append([0,2]);item['cells'].append({'value':'未知合并栏内容','state':'candidate'})
    result,_=delivery.apply_section({'id':'synthetic','metadata':{}},{'id':'s','requirements':[]},spec)
    assert '未知合并栏内容' in result['content']
    assert '| 采购字段 | 候选资料 |' not in result['content']


def test_unknown_fixed_form_preserves_source_grid():
    table={'rows':[{'cells':['姓名',''], 'spans':[1,1]}, {'cells':['备注','原采购说明'], 'spans':[1,1]}]}
    rows=delivery._expanded_rows(table);rows[0][1]='候选甲'
    assert delivery._resume_tables(table,rows) is None
