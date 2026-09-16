"""Optional AI writing starts from a user's saved module draft, never a live library."""
import copy
import json

MAX_DRAFT_CHARS = 40000


def sources(project, section):
    from . import product_modules
    return product_modules.source_context(section, project)


def context(project, section):
    selected = sources(project, section)
    if not selected:
        return None
    body = section.get('content') or ''
    if len(body) > MAX_DRAFT_CHARS:
        raise ValueError('本章已选素材超过单章 AI 编写输入上限40000字，请缩减本章素材或拆为独立二级主题；原文仍可手工编辑和导出')
    return {'current_saved_body': body,
            'selected_modules': [{k: s.get(k) for k in ('module_id','title','version','scope','content_sha256','source_kind','enterprise_fact')}
                                 for s in selected],
            'role': 'user_authored_draft', 'enterprise_fact_verified': False}


def prepare_unit(project, section):
    from . import proposal_runtime, chapter_outline
    unit = proposal_runtime.decorate_section(project, section)
    draft = context(project, section)
    if not draft:
        return unit
    unit = {**unit, '_product_module_draft': draft}
    if not unit.get('_proposal_spec'):
        # A single user-selected draft is one writing operation. Transport
        # batches must not repeat the entire appended body several times.
        unit['_proposal_spec'] = {
            'content_kind':'narrative', 'volume':'technical', 'title':section['title'],
            'group_title':section.get('outline_group_title') or section['title'],
            'purpose':'在本章已保存的人工素材基础上完善当前二级主题',
            'writing_instruction':'根据用户已选产品模块和本章要求补充正文',
            'user_selected_module':True, 'source_refs':[], 'score_factors':[], 'feature_rows':[],
            'suboutline':chapter_outline.effective_suboutline(section),
            'suggested_subtopics':[], 'requirement_ids':list(section.get('requirement_ids',[]))}
    return unit


def prompt_prefix(draft):
    if not draft:
        return ''
    return ('本章先由用户选择产品功能模块编制，下面是已保存的当前草稿与冻结模块版本。'
            '本次仅完善当前二级章节，结合已有素材补充不充分之处，保持名称、数字、条件和事实含义；'
            '不要把整份草稿按每条要求重复输出，也不要新建章节。'
            '这些是用户编写的素材，不是新增的已核验企业证据；不得将module_id或版本号编造成[E:]引用。'
            '企业能力仍须核对本次已提供企业依据，新增内容不得捏造既成事实、采购要求、报价、人员或签章。'
            '发现素材与采购或企业资料冲突时在内部gap_reason具体说明，不在正文反复插入免责声明。'
            '仅使用下面给出的当前文本，不读取或推测模块库的后续版本。\n'
            '当前章节人工素材数据：\n'+json.dumps(draft,ensure_ascii=False)+'\n\n')


def source_snapshot(metadata, section_id):
    return copy.deepcopy((metadata.get('product_module_sources') or {}).get(section_id, []))
