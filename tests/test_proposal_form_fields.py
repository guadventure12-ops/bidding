"""Precise neutral procurement identity/date fields; no real source/model data."""
from copy import deepcopy
import pytest
from app.proposal_generation import _template_fields,_deterministic_result


@pytest.mark.parametrize('line',['年   月   日','年 月 日','____年____月____日','＿＿年＿＿月＿＿日'])
def test_standalone_empty_date_line_is_retained_but_not_filled(line):
    p={'name':'测试项目','company_name':'测试企业','buyer':'测试采购人','project_number':'QA-001'}
    assert _template_fields({'paragraphs':[{'text':line}]},p)==[line]


def test_exact_original_identity_line_is_retained_without_rewriting_existing_source_value():
    schema={'paragraphs':[{'text':t} for t in ['采购项目：源文件项目','项目编号：SOURCE-001','供应商：源文件单位（盖章）']]}
    p={'name':'调试项目名称','project_number':'DIFFERENT','company_name':'当前单位'}
    assert _template_fields(schema,p)==['采购项目：源文件项目','项目编号：SOURCE-001','供应商：源文件单位（盖章）']


def test_only_exact_blank_identity_values_use_known_project_fields():
    schema={'paragraphs':[{'text':t} for t in ['采购项目：____','项目编号：','供应商： （盖章）',
                '法定代表人： （签字）','年 月 日','不存在违规情形的年份：____','备注：年月日仅为填写说明'] ]}
    p={'name':'调试名称','_cover_identity':{'project_name':'已确认项目名'},'project_number':'QA-002','company_name':'已确认企业'}
    result=_template_fields(schema,p)
    assert '采购项目：已确认项目名' in result and '项目编号：QA-002' in result
    assert '供应商：已确认企业（盖章）' in result
    assert '法定代表人： （签字）' in result and '年 月 日' in result
    assert '不存在违规情形的年份：____' in result
    assert not any('填写说明' in line for line in result)
    assert all('2026' not in line for line in result)


@pytest.mark.parametrize('title',['商务条款偏离表','技术条款偏离表'])
def test_deviation_identity_precedes_table_and_blank_signature_date_follows(title,monkeypatch):
    from app import proposal_deliverables
    monkeypatch.setattr(proposal_deliverables,'apply_section',lambda *args:None)
    schema={'tables':[{'rows':[{'cells':['序号','采购要求','响应','说明']},{'cells':['','','','']}]}],
            'paragraphs':[{'text':t} for t in ['采购项目：源文件项目','项目编号：SOURCE-001',
               '注：完全响应时应填写无。','供应商： （盖章）','法定代表人： （签字）','年   月   日']]}
    spec={'content_kind':'form','volume':'technical','group_title':title,'form_schema':schema}
    p={'name':'源文件项目','company_name':'当前企业','project_number':'SOURCE-001'}
    unit={'title':title,'requirements':[{'id':'r1'}]};before=deepcopy((p,unit,spec))
    result,_=_deterministic_result(p,unit,spec)
    content=result['content']
    assert content.index('采购项目：源文件项目') < content.index('| 序号 |')
    assert content.index('项目编号：SOURCE-001') < content.index('| 序号 |')
    assert content.index('供应商：当前企业（盖章）') > content.index('| 序号 |')
    assert content.endswith('年   月   日') and content.count('年   月   日')==1
    assert '无偏离' not in content and '应填写无' not in content
    assert result['_model_calls']==0 and (p,unit,spec)==before
