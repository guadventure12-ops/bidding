from app import proposal_generation,index_materials
from test_proposal_generation import setup,unit,answer


def test_self_defined_scored_form_uses_source_retrieval_and_llm(setup):
    target=unit(kind='form')
    target.update(title='增值服务及优惠编制',outline_group_title='增值服务及优惠')
    target['_proposal_spec'].update(title=target['title'],group_title=target['outline_group_title'],
        score_factors=[{'number':'12','title':'增值服务及优惠','text':'根据实际增值内容评分，格式自拟。'}])
    setup['respond'](answer(target,'按本次提供的服务清单组织实际支持流程。'))
    result,_=proposal_generation.generate_proposal_section('j',setup['p'],target,'op')
    assert len(setup['calls'])==1
    assert result['_generation_mode']=='proposal_whole_section'
    assert '文件或填写内容' not in result['content']
    assert target['_proposal_spec']['content_kind']=='form'  # Stored outline not rewritten.


def test_fixed_source_form_does_not_enter_free_writing(setup):
    target=unit(kind='form');target['_proposal_spec']['score_factors']=[{'number':'1'}]
    target['_proposal_spec']['form_schema']={'tables':[{'rows':[{'cells':['姓名','签字']},{'cells':['','']}]}]}
    assert not index_materials.free_form(target['_proposal_spec'])
    result,_=proposal_generation.generate_proposal_section('j',setup['p'],target,'op')
    assert not setup['calls'] and '| 姓名 | 签字 |' in result['content']
