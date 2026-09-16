import copy
import pytest
from app import index_materials as im,review_index,proposal_export,proposal_runtime,db
from test_review_rules import local
from test_review_index import model_case


EMPTY='| 序号 | 资料项目 | 文件或填写内容 | 页码 |\n| --- | --- | --- | --- |\n| 1 | 增值服务及优惠 | ________________ | |'


def test_label_and_number_in_blank_form_are_not_actual_content():
    assert not im.substantive({'content':EMPTY})
    assert im.substantive({'content':EMPTY.replace('________________','提供已明确范围的服务支持，由双方核对清单。')})


def test_attachment_marker_requires_valid_available_file():
    section={'id':'s','title':'证书','content':'![证书](asset:certificate)'}
    spec={'content_kind':'attachment','volume':'technical'}
    assert not im.status(section,spec)['ready']
    assert not im.status(section,spec,assets=set())['ready']
    assert im.status(section,spec,assets={'certificate'})['ready']


def test_quote_needs_valid_existing_business_confirmation():
    s={'id':'s','content':'报价说明和说明条款。'};spec={'content_kind':'form','volume':'pricing'}
    assert not im.status(s,spec,{'quotation':{'confirmed':True}})['ready']


def test_index_omits_blank_target_but_retains_original_scoring_row(local):
    p,reqs,sections,model=model_case()
    sections[1]['content']=EMPTY
    new=review_index.build(p,sections,blocks=__import__('test_review_index').source())
    assert new['tables'][0]['rows'][2]['values'][-1]==''
    assert new['tables'][0]['rows'][2]['values'][1]=='需求理解'
    sections[0]['content']=review_index.markdown(model);sections[0]['user_edited']=1
    p['metadata']=proposal_runtime.record_result(p['metadata'],sections[0],{'responses':[],'_review_index_model':model})
    projected,_=proposal_export.projected_sections(p,reqs,sections,'technical')
    assert '[[PAGE:' not in projected[0]['content']


def test_input_version_changes_on_body_and_settings_but_not_result_metadata(local):
    p=db.one('SELECT * FROM projects WHERE id=?',('p',))
    first=im.input_revision(p,[])
    p2=copy.deepcopy(p);p2['metadata']['proposal_section_results']={'x':{'new':'generated'}}
    assert im.input_revision(p2,[])==first
    p2['quotation']={'confirmed':True,'total_including_tax':'100'}
    assert im.input_revision(p2,[])!=first
