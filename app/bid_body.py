"""Separate drafting notes from deliverable prose without inventing missing facts.

Pure, deterministic and idempotent. Removed spans remain available as internal
items. Ordinary product descriptions containing words such as 待确认 are kept.
"""
import re
from . import review_rules

VERSION = 'bid-body-1'
MARK = re.compile(r'【(?:待补充|待确认|待核实|待填写|待提供|待实施确认|待项目确认|待企业确认|待企业核实|待人工确认|待采购人澄清|待企业及实施确认|待企业确认的承诺模板|TODO|内部待办|编制说明)(?:[：:\s][^】]*)?】|\[(?:TODO|TBD|待补充|待确认)(?:[：:\s][^\]\n]*)?\]|（[^（）\n]*(?:本项目|本次)[^（）\n]*待(?:确认|核实)[^（）\n]*）', re.I)
TITLE = re.compile(r'^(?:(?:本章|本章节|本节|内部|企业)?(?:待补充|待补齐|待补|待办|待确认|缺失|缺少)(?:事项|证明材料|材料|资料|信息|字段|内容|问题)?(?:汇总表|汇总|总表|一览表|清单|说明|列表)?|(?:内部)?编制说明|内部待办(?:清单)?|TODO(?:\s*LIST)?|TBD|待企业确认的承诺模板|(?:证明)?材料待补充清单)$', re.I)
LABEL = re.compile(r'^(?:[-*+]\s*)?(?:TODO|TBD|待补充(?:事项|材料)?|待确认|待核实|待填写|待提供|内部待办|编制说明|缺失材料|缺少材料|备注[（(]内部[）)])\s*[：:]\s*', re.I)
EDITORIAL = re.compile(r'^(?:以下为|以下仅为|以上|本章|本节|本响应|此文本|上述承诺|本次证据|所有待补充|所有.*待补).*?(?:待人工|待企业|待核实|待确认|仅供内部|内部确认|签字盖章后|不可.*(?:提交|响应)|不代表|不表示|未提供|不臆造)')
DELIVERY = re.compile(r'签章|签字|签署|盖章|复印件|扫描件|附件|装订|装入|截图|原件')
FACT = re.compile(r'金额|报价|税率|单价|总价|姓名|地址|电话|邮箱|日期|版本|注册资本|信用代码|经营范围|有效期|时限|数量|比例|税率|适用条件|是否|能力|性能|支持|实现|企业事实|事实核实')


def heading(line):
    value = line.strip()
    m = re.match(r'^(#{1,6})\s+(.+?)\s*#*$', value)
    if m:
        return len(m[1]), m[2].strip().strip('*').strip()
    m = re.match(r'^([一二三四五六七八九十百]+)[、．.]\s*(.+)$', value)
    if m:
        return 3, m[2].strip().strip('*').strip()
    if value.startswith('**') and value.endswith('**'):
        return 4, value.strip('*').strip()
    return None


def title_name(value):
    return re.sub(r'^(?:[一二三四五六七八九十百]+[、．.]|\d+(?:[.．]\d+)*(?:[.、．)])?\s+)\s*', '', value).strip().rstrip('：:')


def internal_title(value):
    value = title_name(value.rstrip('：:').strip().strip('*').strip())
    return bool(TITLE.fullmatch(value) or re.fullmatch(
        r'(?:(?:本章|本章节|本节|内部|企业)(?:涉及的)?)?(?:待澄清|待补充|待确认|待核实)(?:与(?:待补充|待确认|待核实|待澄清))*(?:事项|材料|问题)?(?:汇总|清单)?(?:如下)?', value)
        or re.fullmatch(r'(?:本章|本章节|本节)?编制说明(?:与.+)?', value))


def separate(text):
    text = text or ''
    lines = text.splitlines(keepends=True)
    edits, items = [], []
    offset, index = 0, 0
    code_fence = None

    def record(start, end, raw, kind, message=None, mixed=False):
        message = message or raw.strip()
        delivery = kind != 'editorial' and bool(DELIVERY.search(message)) and not FACT.search(message) and not mixed
        items.append({'raw': raw, 'message': message, 'kind': 'editorial' if kind=='editorial' else 'delivery' if delivery else 'content_gap',
                      'blocks_content':kind != 'editorial' and not delivery, 'blocks_delivery':kind != 'editorial',
                      'line':text.count('\n',0,start)+1, 'mixed':mixed})
        edits.append({'start':start, 'end':end, 'text':raw, 'source':VERSION})

    while index < len(lines):
        line = lines[index]
        if code_fence:
            if line.strip().startswith(code_fence):
                code_fence = None
            offset += len(line); index += 1; continue
        fence = re.match(r'^\s*(```+|~~~+)(?:json|sql|xml|yaml|yml|python|javascript|js|java|csharp|mermaid|bash|powershell)\s*$', line, re.I)
        if fence:
            code_fence = fence[1]
            offset += len(line); index += 1; continue
        consumed = 1
        cross_line = MARK.search(text, offset)
        if cross_line and cross_line.start() < offset + len(line) and cross_line.end() > offset + len(line):
            while index + consumed < len(lines) and offset + len(line) < cross_line.end():
                line += lines[index + consumed]
                consumed += 1
        plain = line.strip().lstrip('> ').strip().strip('*').strip()
        h = heading(line)
        if h and internal_title(h[1]):
            end = index+1
            while end < len(lines):
                next_heading = heading(lines[end])
                if next_heading and next_heading[0] <= h[0]:
                    break
                end += 1
            raw = ''.join(lines[index:end])
            record(offset, offset+len(raw), raw, 'note')
            offset += len(raw); index=end; continue
        # Standalone labels also delimit an internal summary until the next
        # heading. Without a heading/label, ordinary numbered body stays intact.
        if internal_title(plain) and plain.rstrip('：:'):
            end=index+1
            while end<len(lines) and not heading(lines[end]):
                end+=1
            raw=''.join(lines[index:end])
            record(offset,offset+len(raw),raw,'note')
            offset+=len(raw);index=end;continue
        matches=list(MARK.finditer(line))
        if matches:
            remainder=MARK.sub('',line).strip().strip('{}').strip()
            remainder=re.sub(r'^(?:[-*+]\s+|\d+[.)、]\s*|[一二三四五六七八九十]+、\s*)', '', remainder).strip()
            # A note beside a substantive assertion qualifies that assertion.
            # Do not simply erase it and accidentally make an unverified claim.
            mixed=bool(re.search(r'[\w\u4e00-\u9fff]',remainder))
            if line.lstrip().startswith('|'):
                # Preserve table shape and values in other cells; a noted cell
                # is left visibly blank, with its entire old value kept inside.
                pos=offset
                for cell in re.split(r'(?<!\\)\|',line):
                    if MARK.search(cell):
                        record(pos,pos+len(cell),cell,'note',mixed=bool(MARK.sub('',cell).strip()))
                    pos+=len(cell)+1
            else:
                record(offset,offset+len(line),line,'note',mixed=mixed)
        elif LABEL.match(plain):
            record(offset,offset+len(line),line,'note')
        elif plain in ('拟用文本：','拟用文本:','【待企业确认的承诺模板】',
                       '以下为待核实的拟用文本，不表示企业事实已核验、证明材料已提交或声明已签署盖章。',
                       '以下仅为依据采购文件拟写的待确认文本，需由企业核实后决定是否出具；不表示所述企业事实已经核验，也不表示已签署或盖章。'):
            record(offset,offset+len(line),line,'editorial')
        elif EDITORIAL.search(plain) or '不表示企业事实已核验' in plain or re.search(r'^本章对应.*编制说明', plain):
            record(offset,offset+len(line),line,'note')
        offset += len(line);index+=consumed
    clean=text
    for edit in reversed(edits):
        clean=clean[:edit['start']]+clean[edit['end']:]
    return {'content':clean,'items':items,'removed':edits,'uncertain':[
        {'line':i['line'],'text':i['raw'],'reason':'本行同时含正文与内部说明，整行转入待办，需核对缺失正文'} for i in items if i['mixed']]}


def normalize_result(result):
    """Preserve model gaps and every separated span in internal metadata."""
    if not review_rules.active("body_separation"):
        return dict(result)
    normalized=dict(result)
    body=separate(result.get('content',''))
    normalized['content']=body['content']
    notes=list(result.get('_internal_notes',[]))+[{**i,'requirement_ids':[]} for i in body['items']]
    responses=[]
    for row in result.get('responses',[]):
        split=separate(row.get('response',''))
        response={**row,'response':split['content']}
        if split['items']:
            notes.extend({**i,'requirement_ids':[row['requirement_id']]} for i in split['items'])
            if any(i['blocks_delivery'] for i in split['items']):
                response['gap']=True
                response['gap_reason']='；'.join(filter(None,[str(row.get('gap_reason') or ''),*[i['message'] for i in split['items'] if i['blocks_delivery']]]))
        responses.append(response)
    normalized['responses']=responses
    normalized['_internal_notes']=notes
    return normalized
