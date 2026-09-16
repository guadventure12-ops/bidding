"""Versioned, configurable local review rules; stored findings are never erased."""
import hashlib
import json
import re
import contextvars
import functools
from contextlib import contextmanager
from . import db

VERSION = 'review-rules-3'
CATALOG = [
    {'id':'empty_body','name':'正文为空','version':'1.0',
     'description':'检查章节或逐条响应是否存在实质正文。标题、空白、纯内部提示、只有表头不算正文。',
     'triggers':['章节正文为空、只有标题或内部待办。','非报价类逐条响应为空或没有实质内容。'],
     'exceptions':['有实质内容的短段落不会仅因字数少触发。','表格包含有效数据行时，不按空表头处理。'],
     'correct_example':{'text':'# 实施方案\n\n','result':'应触发：只有标题，没有实施正文。'},
     'false_positive_example':{'text':'按采购程序完成本章编排。','result':'不应触发：这是有实质含义的短句，不能设置任意最小篇幅。'}},
    {'id':'internal_wrapper','name':'内部包装文字','version':'1.0',
     'description':'检查正文中编制用的待补标签、TODO、承诺模板包装及内部说明。关闭此审核项不关闭正文与内部待办的分离功能。',
     'triggers':['正文含【待补充：…】、待补事项汇总等内部编制文字。','正文仍有未识别的承诺模板包装。'],
     'exceptions':['业务功能里的“待确认状态”“待办任务管理”不是内部提示。','已移入内部待办的真实缺项按其实际类型处理，不会因为关闭包装检查而自动消失。'],
     'correct_example':{'text':'TODO：请补齐编制人员说明。','result':'应触发：这是编制待办，不是投标正文。'},
     'false_positive_example':{'text':'系统提供待确认状态查询与待办任务管理。','result':'不应按包装触发；其中产品能力是否有依据由其他规则判断。'}},
    {'id':'capability_mismatch','name':'能力描述与资料不一致','version':'1.0',
     'description':'检查正文能力陈述的资料支持及明确矛盾。数字逐项核对在“数字缺乏直接企业依据”独立规则中控制。历史AI意见保留，关闭后对应意见标为停用，不计入当前本地阻断。',
     'triggers':['“系统支持某功能”等陈述尚未得到所引企业资料支持。','已有模型复核给出矛盾或依据不足意见。','正文能力陈述与已认可资料的含义不同。'],
     'exceptions':['事实含义未改变且有逐字直接资料支持的描述。','采购要求只是需求，不作为企业能力证据；引用格式/资料有效性仍由固定校验检查。'],
     'correct_example':{'text':'正文承诺100并发，所引产品资料只支持20并发。','result':'应触发：数字或能力与直接资料不一致。'},
     'false_positive_example':{'text':'资料与正文均写“提供档案检索功能”。','result':'有有效直接引用时不应因为表述简短而触发。'}},
    {'id':'quotation_missing','name':'报价字段缺失','version':'1.0',
     'description':'检查已识别的报价金额、单价、税率等正文缺项，以及报价类要求的空响应/缺项状态。不修改报价、不推算缺失金额。',
     'triggers':['内部待办明确缺少报价金额、单价或税率等字段。','报价类要求没有形成可用响应或仍标为缺项。'],
     'exceptions':['已填写的实际金额不能当作空字段。','预算和最高限价不能自动填作投标报价；结构化报价确认、金额计算及分册导出的固定要求保持独立。'],
     'correct_example':{'text':'【待补充：含税报价总金额】','result':'应触发：明确缺少本项目实际报价。'},
     'false_positive_example':{'text':'税率为【10】%。','result':'不应按缺失触发：【10】已有数值；真实性另行核对。'}},
    {'id':'attachments_incomplete','name':'附件／签章未完成','version':'1.0',
     'description':'检查附件复印件、签字、盖章、装订等独立交付待办。关闭后保留待办记录，只停止其当前本地规则阻断。',
     'triggers':['已登记的附件、签章交付待办尚未完成。','需要签章装入的资格声明仍存在交付待办。'],
     'exceptions':['记录已完成且当前内容版本与确认记录一致。','“法定代表人姓名及签章缺失”等混合事实字段问题不属于纯附件事项，不能一并忽略。'],
     'correct_example':{'text':'营业执照复印件尚需盖章并装订。','result':'应触发交付待办，不据此认定正文能力错误。'},
     'false_positive_example':{'text':'法定代表人姓名尚未填写。','result':'不能只按签章处理：这是仍需处理的事实字段缺项。'}},
]

from .review_rule_catalog import CATALOG as BACKEND_CATALOG
for row in CATALOG:
    row.update(group='正文与批准',source='app/content_review.py:inspect_section / assessments',editable=True,check_codes=[])
CATALOG += BACKEND_CATALOG
# Every catalogued rule is user-configurable. A disabled review never creates
# nonexistent records or converts an unreadable payload into valid program data.
for row in CATALOG:
    row['editable'] = True
    row['version'] = '1.1'
    row['description'] = row['triggers'][0] + ' 关闭后跳过本项审核判断；仍需可读取的输入和可保存的数据结构，问题不会自动补齐。'
IDS = {r['id'] for r in CATALOG}
EDITABLE = {r['id'] for r in CATALOG if r['editable']}
CHECK_RULES = {code:r['id'] for r in CATALOG for code in r['check_codes']}
PRICE = re.compile(r'报价|单价|总价|税率|含税价|不含税价')
MISSING = re.compile(r'缺少|缺失|未填|未提供|未完成|未签|未盖|未附|尚未|待补|待签|待盖|需提交|需补充|为空')
ATTACHMENT = re.compile(r'复印件|扫描件|签章|盖章|签字|签署|装订|装入|附件|原件')
FACT_FIELD = re.compile(r'姓名|金额|报价|日期|版本|税率|单价|总价|地址|电话|有效期')
PURE_WRAPPERS = {'【待企业确认的承诺模板】','待企业确认的承诺模板','拟用文本：','拟用文本:'}


def flags():
    raw = db.get_settings().get('review_rules', {})
    raw = raw if isinstance(raw, dict) else {}
    return {key:raw[key] if type(raw.get(key)) is bool else True for key in EDITABLE}


def revision(enabled=None):
    enabled = flags() if enabled is None else enabled
    return hashlib.sha256(json.dumps([VERSION, enabled], sort_keys=True).encode()).hexdigest()


def enabled(rule, config):
    return rule not in EDITABLE or config.get(rule, True)


_CURRENT = contextvars.ContextVar('review_rule_operation', default=None)


@contextmanager
def scope(config=None):
    current = _CURRENT.get()
    snapshot = config if config is not None else current if current is not None else flags()
    token = _CURRENT.set(snapshot)
    try:
        yield snapshot
    finally:
        _CURRENT.reset(token)


def active(rule):
    """Pure helpers default to all-on; business entry points bind a snapshot."""
    config = _CURRENT.get()
    return True if config is None else enabled(rule, config)


def governed(fn):
    @functools.wraps(fn)
    def run(*args, **kwargs):
        with scope():
            return fn(*args, **kwargs)
    return run


def todo_rule(item):
    if item.get('rule_id') in IDS:
        return item['rule_id']
    kind = item.get('kind')
    message = item.get('message','').strip()
    if message in PURE_WRAPPERS or item.get('raw','').strip() in PURE_WRAPPERS:
        return 'internal_wrapper'
    if kind in ('delivery', 'body_delivery', 'qualification_delivery') and not item.get('blocks_content'):
        return 'attachments_incomplete'
    if kind in ('body_editorial', 'unresolved_wrapper', 'unresolved_legacy'):
        return 'internal_wrapper'
    if kind in ('support_review', 'content_conflict'):
        return 'capability_mismatch'
    if kind in ('content_gap', 'body_content_gap'):
        return 'quotation_missing' if PRICE.search(message) else 'other_content_gap'
    if kind == 'qualification_declaration':
        return 'qualification_declaration'
    return 'other_review_issue'


def model_finding_rule(finding):
    reason = finding.get('reason','')
    if finding.get('verdict') == 'contradiction':
        return 'capability_mismatch'
    if re.search(r'(?:正文|章节|响应).{0,8}(?:为空|空白|没有实质|仅有标题|只有标题)', reason):
        return 'empty_body'
    if re.search(r'系统|产品|功能|能力|支持|实现|识别|自动|模块', reason) and re.search(r'证据|资料|依据|证明|支撑', reason):
        return 'capability_mismatch'
    if MISSING.search(reason) and PRICE.search(reason):
        return 'quotation_missing'
    if MISSING.search(reason) and ATTACHMENT.search(reason) and not FACT_FIELD.search(reason):
        return 'attachments_incomplete'
    if re.search(r'内部包装|承诺模板|编制说明|TODO|待补事项汇总', reason):
        return 'internal_wrapper'
    if re.search(r'证据|资料|依据|支撑|证明|能力|性能|并发', reason):
        return 'capability_mismatch'
    return 'ai_other_finding'


def annotate(item, config):
    rule = todo_rule(item)
    return {**item, 'rule_id':rule, 'rule_enabled':enabled(rule, config)}


def describe():
    config = flags()
    groups = ['招标与解析','企业资料认可','资料与引用','响应与覆盖','正文与批准','AI复核','AI判断约束','生成约束','交付与导出','审核汇总','生成与解析校验','响应保存校验','正文保存校验','操作一致性','文件与运行校验']
    ordered = sorted(CATALOG,key=lambda r:groups.index(r['group']) if r['group'] in groups else len(groups))
    return {'version':VERSION, 'revision':revision(config), 'enabled':config,
            'rules':[{**r,'enabled':enabled(r['id'],config)} for r in ordered],
            'scope':'全部目录规则均可关闭，支持全选及按分类批量开关，保存后生效。关闭审核判断不等于问题已解决，也不会补造不存在的资料或有效模型结果。设置本身不修改正文、批准状态和历史记录。'}


def save(values, expected_revision):
    from . import workflow
    with workflow.editing(None, whole_workspace=True):
        current = flags()
        if expected_revision != revision(current):
            raise ValueError('审核设置已被其他页面修改，请重新加载后保存')
        if not values or not set(values) <= EDITABLE or any(type(v) is not bool for v in values.values()):
            raise ValueError('只能修改目录中可调整的布尔审核开关')
        updated = {**current, **values}
        if updated != current:
            db.set_setting('review_rules', updated)
        return {**describe(), 'changed':updated != current}
