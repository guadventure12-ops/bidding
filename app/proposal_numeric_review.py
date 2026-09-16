"""Conservative numeric source checks for the new proposal profile only.

This is not a general semantic or legal verifier. An empty issue list means the
numeric-source rule found no issue, never that a plan is approved or performed.
Evidence must already have passed source scope/version validation upstream.
"""
from __future__ import annotations

import re

ROLES = frozenset({'same_tender_proposed_plan', 'conditional_resource_recommendation'})
_CITATION = re.compile(r'\[E:([^\]\r\n]+)\]')
_BOUNDARY = re.compile(r'[。；;！？!?\r\n]+')
_TRAILING_CITES = re.compile(r'(?:[ \t]*\[E:[^\]\r\n]+\])+')
_PLANNED = re.compile(r'计划|建议|推荐|参考配置|初步方案|拟(?:采用|配置|投入|安排|实施|提供|完成|部署|开展|建设|设置|执行|按|选用|由|组织|作为|定于|于|进行)')
_ACTUAL = re.compile(r'已经|现有|现已|实测|实际(?:支持|达到|投入|配置|运行)|已(?:达到|实现|具备|投入|配置|部署|配备|完成|落实|拥有)|'
                     r'(?:产品|系统|本公司|我方|本企业)(?:目前)?(?:支持|可支持|具有|具备|拥有)|保证达到')
_PROCUREMENT = re.compile(r'采购(?:文件)?(?:要求|规定|约定|约束)|招标(?:文件)?(?:要求|规定|约定)|原声明模板|原承诺模板|格式原文')
_NEGATIVE = re.compile(r'不支持|不具备|不能|尚未|未实现|未达到|未投入|未提供')
_DATE = re.compile(r'(?<!\d)(20\d{2})\s*(?:年|[-/.])\s*(0?[1-9]|1[0-2])\s*(?:月\s*(?:(0?[1-9]|[12]\d|3[01])\s*日)?|[-/.]\s*(0?[1-9]|[12]\d|3[01]))(?!\d)')
_CORE = re.compile(r'(?<![\d.])\d+(?:\.\d+)?\s*核')


def _plain(value):
    text=_CITATION.sub('',str(value or ''))
    return re.sub(r'[\s#*`“”"「」『』（）()。；;，,:：]', '', text)


def _texts(value):
    if isinstance(value,str):return [value]
    if isinstance(value,dict):value=[value]
    result=[]
    for item in value or []:
        if isinstance(item,str):result.append(item)
        elif isinstance(item,dict):
            text=item.get('quote') or item.get('text')
            if isinstance(text,str):result.append(text)
    return result


def _masked(text):
    # Preserve offsets while excluding identifiers/URLs from numeric prose.
    chars=list(text)
    masks=[m.span() for m in _CITATION.finditer(text)]
    masks += [m.span(1) for m in re.finditer(r'!\[[^\]\n]*\]\(([^)\n]*)\)',text)]
    masks += [m.span() for m in re.finditer(r'https?://[^\s<>]+',text)]
    # Markdown outline numbers are labels, not quantities such as "5.5 日".
    # Mask only an explicit ordinal prefix, never the heading's actual values.
    for match in re.finditer(r'(?m)^[ \t]{0,3}#{1,6}[ \t]+(?P<ordinal>\d+(?:\.\d+)+|\d+[.)、．])[ \t]+(?=\S)',text):
        # A decimal followed by a unit can be a real heading claim, for example
        # "50.0 万元报价" or "2.5 小时恢复目标". Only the known title words
        # 项目/日志 disambiguate their misleading single-character unit prefix.
        quantity_unit=re.match(r'(?:%|％|万元|亿元|元|小时|分钟|毫秒|秒|个月|工作日|天|年|月|'
                               r'日(?!志)|人|名|家|台|个|项(?!目)|TB|GB|MB|ms|核)',text[match.end():],re.I)
        if not _DATE.fullmatch(match['ordinal']) and not quantity_unit:
            masks.append(match.span('ordinal'))
    # "1V1人工客服" is a service-mode identifier, not a claim of one employee.
    masks += [m.span() for m in re.finditer(r'(?<![A-Za-z0-9_])\d+[vV]\d+(?![A-Za-z0-9_])',text)]
    for start,end in masks:chars[start:end]=' '*(end-start)
    return ''.join(chars)


def _number_key(value):
    from .workflow import number_key
    date=_DATE.fullmatch(value)
    if date:
        day=date[3] or date[4]
        return f'{int(date[1]):04}-{int(date[2]):02}' + (f'-{int(day):02}' if day else '')
    return number_key(value)


def _numbers(text):
    from .workflow import _claim_numbers
    masked=_masked(text)
    extended=[(m.start(),m.end(),text[m.start():m.end()]) for pattern in (_DATE,_CORE) for m in pattern.finditer(masked)]
    spans=list(extended)
    for value in _claim_numbers(masked):
        for match in re.finditer(re.escape(value),masked):
            if match.start() and (masked[match.start()-1].isdigit() or masked[match.start()-1]=='.'):continue
            if any(match.start()<end and match.end()>start for start,end,_ in extended):continue
            spans.append((match.start(),match.end(),text[match.start():match.end()]))
    unique={(start,end):(start,end,value,_number_key(value)) for start,end,value in spans}
    return sorted(unique.values())


def _statements(text):
    start=0
    while start<len(text):
        boundary=_BOUNDARY.search(text,start)
        end=boundary.end() if boundary else len(text)
        trailing=_TRAILING_CITES.match(text,end)
        if trailing:end=trailing.end()
        if text[start:end].strip():yield start,text[start:end]
        start=end


def _clause(statement, position):
    # A proposal in the previous clause does not excuse a subsequent assertion
    # of an achieved capacity, even if both use the same number and citation.
    start=max(statement.rfind('，',0,position),statement.rfind(',',0,position))+1
    ends=[p for p in (statement.find('，',position),statement.find(',',position)) if p>=0]
    end=min(ends) if ends else len(statement)
    return statement[start:end].strip()


def _support(row):
    value=row.get('support_text')
    if isinstance(value,str):return value
    value=str(row.get('text') or '')
    return value.split('\n原文：\n',1)[-1] if row.get('evidence_kind')=='curated_extract' else value


def _role(row):
    value=row.get('reference_role') or (row.get('source_constraints') or {}).get('role')
    if value:return value
    return 'unknown_reference' if row.get('evidence_kind')=='proposal_reference' else 'enterprise_fact'


def _conditions(source):
    conditions=[]
    # Preserve explicit parenthetical scaling conditions as well as the range.
    for match in re.finditer(r'[（(]([^）)]*)[）)]',source):
        if re.search(r'按|根据|视|仅|限|条件|调整',match[1]):conditions.append(match[1])
    source=re.sub(r'[（(][^）)]*[）)]','',source)
    for part in re.split(r'[，,。；;\r\n]',source):
        part=part.strip().strip('|').strip()
        mode=_PLANNED.search(part)
        prefix=part[:mode.start()] if mode else part
        leading=re.match(r'^(?:仅在|在|当|若|如果|适用于|仅限于|针对)(.+)',prefix)
        if leading:
            value=re.sub(r'(?:条件下|情况下|时|下)$','',leading[1]).strip()
            if value:conditions.append(value)
        elif re.search(r'以下|以内|以上',prefix):
            conditions.append(prefix)
        if re.search(r'^(?:按|根据|视).*(?:调整|扩展|选择|确定)$',part):conditions.append(part)
        # Numeric lower/upper bounds must not become an unconditional value.
        for start,end,value,_ in _numbers(part):
            bound=re.search(r'(不低于|不超过|至少|最多|小于|大于|少于|多于)\s*$',part[:start])
            if bound:conditions.append(bound[1]+value)
    return list(dict.fromkeys(_plain(c) for c in conditions if _plain(c)))


def _preserves_conditions(source, statement):
    normalized=_plain(statement)
    if any(condition not in normalized for condition in _conditions(source)):return False
    # A value in a negative source statement cannot attest a positive capability.
    return all(word in statement for word in _NEGATIVE.findall(source))


def _source_fragments(row,key):
    return [statement for _,statement in _statements(_support(row)) if any(value_key==key for _,_,_,value_key in _numbers(statement))]


def _original_quote(statement,quotes):
    core=_CITATION.sub('',statement).strip()
    core=re.sub(r'^\s*(?:[#>*-]\s*)*','',core)
    core=re.sub(r'^\s*(?:采购(?:文件)?(?:要求|规定|约定|约束)|招标(?:文件)?(?:要求|规定|约定)|原声明模板|原承诺模板|格式原文)\s*[：:为]*\s*','',core)
    return any(_plain(core) and _plain(core) in _plain(quote) for quote in quotes)


def unsupported_numbers(text, evidence, source_quotes=(), deliverable_context=()):
    """Return per-occurrence issues without approving prose or changing state.

    ``source_quotes`` and ``deliverable_context`` accept exact original strings,
    or dictionaries with text/quote. Deliverable fields are not company facts.
    Context lacking a known structure is never automatically assigned a role.
    """
    text=str(text or '')
    evidence=[row for row in evidence or [] if isinstance(row,dict)]
    quotes=_texts(source_quotes);fields=_texts(deliverable_context)
    result=[]
    for offset,statement in _statements(text):
        cited=set(_CITATION.findall(statement))
        # A clearly labelled exact quotation remains the purchaser's constraint,
        # even if its quoted words include "系统支持". An appended bidder claim
        # is no longer that exact quotation and does not receive this exception.
        labelled=bool(re.match(r'^\s*(?:[#>*-]\s*)*(?:采购(?:文件)?(?:要求|规定|约定|约束)|招标(?:文件)?(?:要求|规定|约定)|原声明模板|原承诺模板|格式原文)\s*[：:]',statement))
        quoted_procurement=labelled and _original_quote(statement,quotes)
        for start,end,value,key in _numbers(statement):
            if quoted_procurement:continue
            clause=_clause(statement,start)
            actual=bool(_ACTUAL.search(clause))
            planned=bool(_PLANNED.search(clause)) or (not actual and not _ACTUAL.search(statement) and bool(_PLANNED.search(statement)))
            matched=[];reasons=[];supported=False
            for row in evidence:
                fragments=_source_fragments(row,key)
                if not fragments:continue
                role=_role(row);is_cited=str(row.get('id') or '') in cited
                if role=='enterprise_fact':
                    for fragment in fragments:
                        # Exact approved source reuse can be traced by the
                        # already supplied evidence without adding repeat tags.
                        exact=_plain(clause) and _plain(clause) in _plain(fragment)
                        if not is_cited and not exact:continue
                        if actual and _PLANNED.search(fragment):continue
                        if _preserves_conditions(fragment,statement):supported=True;break
                    if supported:break
                    continue
                matched.append(str(row.get('id') or ''))
                if role=='procurement_requirement':
                    if is_cited and labelled and _original_quote(statement,fragments):
                        supported=True;break
                    reasons.append(('numeric_procurement_not_enterprise_fact','采购要求中的数字只能用于明确原采购约束引用，不能证明企业能力，也不属于拟方案或资源推荐豁免'));continue
                if role not in ROLES:
                    reasons.append(('numeric_unknown_source_role','来源角色不明确，不能作为数字事实依据'));continue
                if actual:
                    reasons.append(('numeric_requires_enterprise_fact','已实现能力、现有资源或实际投入须有产品/企业事实依据，拟方案或推荐配置不能证明'));continue
                if not is_cited:
                    reasons.append(('numeric_role_not_cited','本句未明确引用对应拟方案或推荐配置原文'));continue
                if not planned:
                    reasons.append(('numeric_proposal_language_missing','拟方案或推荐配置数字须明确保持拟、计划、建议语气，不能作为无条件承诺'));continue
                if not any(_preserves_conditions(fragment,statement) for fragment in fragments):
                    reasons.append(('numeric_source_condition_changed','原推荐范围、前提或调整条件未完整保留'));continue
                supported=True;break
            if supported:continue
            if not actual and _PROCUREMENT.search(statement) and _original_quote(statement,quotes):continue
            # Exact reviewed date/name/quantity fields can substantiate the
            # field itself, never a different sentence asserting capability.
            if not actual and any(_plain(statement)==_plain(field) for field in fields):continue
            code,reason=reasons[0] if reasons else ('numeric_source_missing','未找到与本句数字及语气相符的明确来源，需核对')
            result.append({'value':value,'number_key':key,'statement':clause,'start':offset+start,'end':offset+end,
                           'code':code,'reason':reason,'evidence_ids':list(dict.fromkeys(matched)) if matched else sorted(cited)})
    return result
