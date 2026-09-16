"""Pure, traceable H1/H2 recommendations from procurement contents and scoring.

This reads already parsed blocks only. It never interprets a procurement demand
as an enterprise fact, edits existing chapters, or calls a model. The second and
third *logical* scoring columns are used, including horizontal cell spans.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

VERSION = 'outline-sources-1'
_CN = '一二三四五六七八九十百零〇'
_CONTENTS = re.compile(r'(?:商务[、，,及和/\s]*)?(?:技术(?:响应)?|投标|响应|应答)(?:文件|书|标)?(?:编制)?(?:目录|组成)(?:[（(].{0,35}[）)])?[:：]?')
_SCORING = re.compile(r'(?:评分|评审)(?:细则|标准|办法|内容|因素|项目)')
_RATING = re.compile(r'(?:得|扣|满分|最高(?:得分)?|分值(?:范围)?|计分|评分|分数|得分|分值为|权重)')
_FORMULA = re.compile(r'基准(?:价|值)|偏离值|算术平均|四舍五入|扣完为止|(?:得分|分值)\s*(?:为|=|＝|[：:])\s*\d|[=＝].*[+*×÷]')
_REFUSAL = re.compile(r'^(?:未|不)(?:提供|响应|提交)|^不满足|^否则|^(?:优|良|中|差|优秀|良好|一般|较差|合格|不合格)(?:[：:,，]|的|得|者)|^\d+(?:\.\d+)?\s*(?:[-–~～至]\s*\d+(?:\.\d+)?)?\s*分[：:,，]?$')


def _norm(value):
    return re.sub(r'\s+', '', str(value or '')).replace('（', '(').replace('）', ')').casefold()


def _id(*parts):
    return 'source-' + hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]


def _table(block):
    meta = block.get('metadata') or {}
    if isinstance(meta, str):
        try: meta = json.loads(meta)
        except (ValueError, TypeError): meta = {}
    return block.get('table') or (meta.get('table') if isinstance(meta, dict) else {}) or {}


def _locator(block):
    return re.sub(r'（字符 \d+[–-]\d+）$', '', str(block.get('locator') or ''))


def _identity(block):
    table = _table(block)
    # Chunk IDs may change on reparse or repeat when a long row is fragmented.
    return [block.get('document_id'), table.get('index'), table.get('row'), _locator(block)]


def _ref(block, quote=None, column=None, excerpt=None):
    result = {'chunk_id': block.get('chunk_id') or block.get('id'),
              'document_id': block.get('document_id'), 'locator': _locator(block),
              'quote': str(block.get('_outline_source_quote', block.get('text') or '')) if quote is None else str(quote)}
    table = _table(block)
    if table:
        result.update(table_index=table.get('index'), row=table.get('row'))
    if column is not None: result['column'] = column
    if excerpt is not None: result['excerpt'] = excerpt
    elif block.get('_outline_source_excerpt'): result['excerpt'] = block['_outline_source_excerpt']
    return result


def _notice(notices, code, message, block=None):
    item = {'code': code, 'message': message}
    if block is not None: item['source_refs'] = [_ref(block)]
    if item not in notices: notices.append(item)


def _plain_title(text):
    text = re.sub(r'\s*(?:[.．·…]{2,}|\t+)\s*\d+\s*$', '', text.strip())
    return re.split(r'[（(](?:格式|根据|详见|见附件|主要用于)', text, maxsplit=1)[0].strip(' \t：:；;')


def _numbered(text):
    decimal = re.match(r'^\s*(\d+(?:[.．]\d+)+)(?:[.．、]?\s*)', text)
    if decimal:
        return min(4, len(re.split(r'[.．]', decimal[1]))), _plain_title(text[decimal.end():])
    patterns = [
        (1, rf'^\s*第[{_CN}\d]+章\s*'),
        (2, rf'^\s*第[{_CN}\d]+节\s*'),
        (1, rf'^\s*[{_CN}]+[、.．]\s*'),
        (2, rf'^\s*[（(][{_CN}\d]+[）)]\s*'),
        (1, r'^\s*\d+[、.．]\s*'),
        (2, r'^\s*##\s+'),
        (1, r'^\s*#\s+'),
    ]
    for level, pattern in patterns:
        match = re.match(pattern, text)
        if match: return level, _plain_title(text[match.end():])
    return None, _plain_title(text)


def _contents_heading(text):
    _, title = _numbered(text)
    if _CONTENTS.fullmatch(title): return True
    return bool(re.fullmatch(r'(?:投标|响应|技术|商务、技术)文件(?:须|应|必须)?(?:严格)?(?:按照|按|包括)(?:以下|如下)(?:完整)?目录(?:编制)?[：:]?', title))


def _scoring_heading(text):
    _, title = _numbered(text)
    return bool(re.fullmatch(r'(?:评分|评审)(?:细则|标准|办法|内容|因素|项目)(?:[（(][^\n]{0,35}[）)])?[:：]?', title))


def _explicit_entries(blocks):
    """Split an unambiguous numbered list, keeping the original source record."""
    for block in blocks:
        text = str(block.get('text') or '')
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if (not _table(block) and len(lines) > 1
                and all(_numbered(line)[0] or _contents_heading(line) or _scoring_heading(line) for line in lines)):
            for line in lines:
                yield {**block, 'text': line, '_outline_source_quote': text,
                       '_outline_source_excerpt': line}
        else:
            yield block


def _explicit(blocks, notices):
    groups, active, doc, parent, force_complete, force_partial = [], False, None, None, False, False
    child_parent, grandchild_parent = None, None
    for block in _explicit_entries(blocks):
        text = str(block.get('text') or '').strip()
        if doc != block.get('document_id'):
            active, parent = False, None
            child_parent, grandchild_parent = None, None
            doc = block.get('document_id')
        if _contents_heading(text):
            active, parent = True, None
            child_parent, grandchild_parent = None, None
            force_complete = force_complete or bool(re.search('完整|不得增删|不得调整', text))
            force_partial = force_partial or bool(re.search('部分|参考|示例|建议', text))
            continue
        if not active or not text: continue
        if re.match(r'^附件\s*\d+\s*[：:]', text) or _table(block) or _scoring_heading(text):
            active = False
            continue
        if re.search(r'(?:目录|一级|二级).*(?:不得增删|必须完整采用|全部如下)', text):
            force_complete = True
            continue
        if re.search(r'(?:以下|目录|规定).*(?:部分|仅供参考|示例)', text):
            force_partial = True
            continue
        level, title = _numbered(text)
        if '\n' in text or '\r' in text:
            _notice(notices, 'ambiguous_multiline_directory', '目录段落同时包含编号项和无法确定归属的换行内容，保留原文供核对，未合并或拆猜标题。', block)
            active = False
            continue
        # In imported Word, list numbering may be omitted. Short plain entries
        # inside a designated contents section are H1, as in the actual tender.
        if not title or len(title) > 110 or re.search(r'[。；;]', title) or re.match(r'^(?:注[：:]|说明[：:]|供应商应|投标人应|响应人应)', title):
            active = False
            continue
        if level and level > 2:
            owner = grandchild_parent if level == 4 else child_parent
            if owner is not None:
                node = {'id': _id('explicit-subtopic', *_identity(block), title), 'title': title,
                        'origin': 'tender', 'source_refs': [_ref(block)], 'source_text': text}
                owner.setdefault('children', []).append(node)
                if level == 3: grandchild_parent = node
            else:
                _notice(notices, 'orphan_explicit_subtopic', '规定目录下级项缺少可定位的二级父项，保留来源供核对，未将其提升成二级目录。', block)
        elif level == 2 and parent is not None:
            child = {'id': _id('explicit-child', *_identity(block), title), 'title': title,
                     'origin': 'tender', 'source_refs': [_ref(block)], 'source_text': text,
                     'requirement_ids': [], 'explicit': True}
            same = next((c for c in parent['children'] if _norm(c['title']) == _norm(title)), None)
            if same:
                same['source_refs'].append(_ref(block))
                child_parent = same
                _notice(notices, 'duplicate_explicit_child', '规定目录重复出现同名二级项，合并显示并保留全部来源。', block)
            else:
                parent['children'].append(child)
                child_parent = child
            grandchild_parent = None
        else:
            child_parent, grandchild_parent = None, None
            if level == 2:
                _notice(notices, 'orphan_explicit_level', '目录从括号编号开始，按当前可见顶层保留；未猜测不存在的父级。', block)
            parent = next((g for g in groups if _norm(g['title']) == _norm(title)), None)
            if parent is None:
                parent = {'id': _id('explicit-group', *_identity(block), title), 'title': title,
                          'origin': 'tender', 'source_refs': [_ref(block)], 'children': [],
                          'source_text': text, 'explicit': True}
                groups.append(parent)
            else: parent['source_refs'].append(_ref(block))
    complete = bool(groups) and not force_partial and (force_complete or all(g['children'] for g in groups))
    return groups, complete, force_complete


def _logical(table):
    cells = table.get('cells')
    if not isinstance(cells, list) or not cells: return None
    spans = table.get('spans') or [1] * len(cells)
    if len(spans) != len(cells) or any(type(s) is not int or not 1 <= s <= 100 for s in spans): return None
    result, owners = [], []
    for index, (value, span) in enumerate(zip(cells, spans)):
        result.extend([str(value or '').strip()] + [''] * (span - 1))
        owners.extend([index] * span)
    return result, owners


def _scoring_tables(blocks, notices):
    tables = {}
    for block in blocks:
        table = _table(block)
        if not table: continue
        key = (block.get('document_id'), table.get('index'))
        state = tables.setdefault(key, {'rows': [], 'seen': {}, 'invalid': False})
        row_key = table.get('row', _locator(block))
        if row_key in state['seen']:
            prior = state['seen'][row_key]
            if any(_table(prior).get(k) != table.get(k) for k in ('cells', 'spans')):
                state['invalid'] = True
                _notice(notices, 'conflicting_table_row', '同一评分表行含不同解析内容，跳过该表，不拼接猜测。', block)
            continue
        state['seen'][row_key] = block
        state['rows'].append(block)
    for state in tables.values():
        rows = state['rows']
        if state['invalid']: continue
        header = None
        for i, block in enumerate(rows[:6]):
            raw = _table(block)
            cells = raw.get('cells') or []
            if any(re.search('页码|页数|对应页', str(c)) for c in cells): continue
            logical = _logical(raw)
            if not logical: continue
            values, owners = logical
            if len(values) < 3: continue
            # Header must establish a scoring table, never an index page or
            # qualifications checklist with a coincidentally similar title.
            score_labels = any(_SCORING.fullmatch(str(c).strip()) for c in cells)
            points = any(re.search('分值|分数|得分|满分|权重', str(c)) for c in cells)
            index = blocks.index(block)
            preceding = [b for b in blocks[:index] if b.get('document_id') == block.get('document_id') and not _table(b)]
            heading = any(_SCORING.search(str(b.get('text', ''))) and len(str(b.get('text', ''))) < 35 for b in preceding[-3:])
            if score_labels and (points or heading):
                # Column 2 must be an item/criterion, or share a merged review
                # standards header with column 3. Respect the user column rule.
                second = str(cells[owners[1]])
                third = str(cells[owners[2]])
                if not re.search('评审|评分|项目|因素|内容|名称|类别|标准', second) or re.search('分值|分数|得分|满分|权重', third):
                    _notice(notices, 'unsupported_scoring_columns', '评分表第二、三逻辑列与目录来源不一致，保留来源供核对，未改用其他列猜测。', block)
                    break
                header = (i, len(values))
                break
        if header is not None: yield rows, header


def _split_items(text):
    """Split source list boundaries, not parentheses containing requirements."""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'(?<!\d)(?=[（(](?:\d+|[一二三四五六七八九十]+)[）)])', '\n', text)
    pieces, buf, depth = [], [], 0
    for ch in text:
        if ch in '（(【[': depth += 1
        if ch in '）)】]': depth = max(0, depth - 1)
        if ch == '\n' or (ch in '；;' and depth == 0):
            if ''.join(buf).strip(): pieces.append(''.join(buf).strip())
            buf = []
        else: buf.append(ch)
    if ''.join(buf).strip(): pieces.append(''.join(buf).strip())
    return pieces


def _candidate(piece):
    text = re.sub(r'^\s*(?:[（(](?:\d+|[一二三四五六七八九十]+)[）)]|\d+[.、．]|[-•●])\s*', '', piece)
    text = re.sub(r'【(?:主观分|客观分)】', '', text).strip(' \t；;。')
    text = text.strip('【】').strip()
    if not text or text in ('证明材料：', '证明材料:', '注：', '注:'): return None
    text = re.sub(r'^(?:证明材料|评审内容|评分内容)[：:]\s*', '', text)
    if not text or _REFUSAL.search(text) or _FORMULA.search(text): return None
    # Opening evaluation instructions are scaffolding; their following items
    # carry the requested response. Numbered design/demo introductory lines
    # become a section label only if there are no specific following details.
    if re.search(r'(?:评审|评价|评分)内容包括[：:]?$', text): return None
    if re.fullmatch(r'(?:专业能力及结构|评审内容|评分内容)[：:]', text): return None
    if re.search(r'(?:以下|如下).*(?:证书|材料|内容|要求)[：:]$', text): return None
    if re.match(r'^(?:a|b|c)[.、．]', text, re.I) and _RATING.search(text): return None
    if re.match(r'^(?:注|其中|说明)[：:]', text): return None
    if re.search(r'作为无效(?:投标|磋商|响应)|应当予以否决|作废标处理', text): return None
    if re.search(r'(?:不得分|不予计分|不重复计分|得0分|得零分)', text) and not re.search(r'须|应提供|需|提供.*证明|响应.*要求', text): return None
    # Delete scoring language from titles only; full source text remains on
    # every candidate/ref. Business dates, numbers and named technologies stay.
    title = re.split(r'[,，。；;]\s*(?:得|最高(?:得)?|每提供|分值|可得|计|给予|扣)\s*\d*', text, maxsplit=1)[0]
    title = title.split('。', 1)[0]
    # A material request and its scoring penalty can share one sentence. Trim
    # only the trailing failure clause from the display title, never the source
    # or an ordinary business condition containing "不" / "得分".
    title = re.sub(r'[,，]\s*(?:未提供|不提供|无法提供|未提交|不提交|否则)'
                   r'[^。；;，,]{0,100}?(?:不得分|得0分|得零分|不予计分|不予评分)\s*$', '', title)
    title = re.split(r'(?:进行综合评分|进行综合评审|进行评分|进行评审)', title, maxsplit=1)[0]
    title = re.sub(r'[,，]?\s*(?:分值范围为|分值为|得分为)\s*.*$', '', title)
    title = re.sub(r'\s*(?:的)?(?:得|可得)\s*\d+(?:\.\d+)?\s*分.*$', '', title)
    title = re.sub(r'[：:]\s*(?:三级|四级|五级|优秀|良好|一般|较差|优|良|中|差)$', '', title)
    title = re.sub(r'^(?:是否|对|根据供应商提供的|根据供应商提交的|根据供应商|评审)', '', title).strip(' \t：:，,；;。')
    if title in ('', '未提供', '不提供', '每提供', '专业能力及结构'): return None
    if _RATING.search(title) and not re.search(r'响应|方案|系统|设计|人员|证书|证明|培训|支持|功能|服务|材料|配置|计划|需求|架构|接口|数据库|维护', title): return None
    # Exact excerpts stay available even when a long sentence needs a compact
    # display label. No source requirement is silently discarded or rewritten.
    label = title if len(title) <= 100 else title[:99] + '…'
    return label, text


def _req_ids(requirements, block, excerpt):
    result = []
    needle = _norm(excerpt)
    for item in requirements:
        same = item.get('chunk_id') in {block.get('id'), block.get('chunk_id')} - {None}
        same = same or (item.get('document_id') == block.get('document_id') and _locator(item) and _locator(item) == _locator(block))
        if not same: continue
        quote = _norm(item.get('quote') or item.get('text'))
        if len(quote) >= 6 and len(needle) >= 6 and (needle in quote or quote in needle) and item.get('id') not in result:
            result.append(item['id'])
    return result


def _children(text, block, requirements):
    result, seen = [], {}
    for piece in _split_items(text):
        candidate = _candidate(piece)
        if not candidate: continue
        title, excerpt = candidate
        key = _norm(title)
        refs = [_ref(block, text, 3, excerpt)]
        if key in seen:
            child = seen[key]
            child['source_refs'].extend(r for r in refs if r not in child['source_refs'])
            child['requirement_ids'] = list(dict.fromkeys([*child['requirement_ids'], *_req_ids(requirements, block, excerpt)]))
            continue
        child = {'id': _id('score-child', *_identity(block), key), 'title': title,
                 'origin': 'scoring', 'source_refs': refs, 'source_text': text,
                 'source_excerpt': excerpt, 'requirement_ids': _req_ids(requirements, block, excerpt),
                 'explicit': False}
        seen[key] = child
        result.append(child)
    return result


def _scored(blocks, requirements, notices):
    groups = []
    for rows, (header_index, width) in _scoring_tables(blocks, notices):
        last, section = None, ''
        for block in rows[header_index + 1:]:
            raw = _table(block)
            expanded = _logical(raw)
            if expanded is None:
                _notice(notices, 'invalid_scoring_row', '评分表行未保留完整单元格结构，跳过该行。', block)
                last = None
                continue
            cells, owners = expanded
            if len(cells) != width:
                _notice(notices, 'inconsistent_scoring_width', '评分表行列数不一致，未将偏移后的列当目录。', block)
                last = None
                continue
            if len(set(owners)) == 1:
                section = str(raw['cells'][0])
                last = None
                continue  # Business/technical/price merged separator, not H1.
            if owners[1] == owners[2]:
                _notice(notices, 'merged_scoring_body_columns', '评分正文行第二、三列横向合并，无法区分目录名称与响应内容，未拆猜标题。', block)
                last = None
                continue
            title, content = cells[1].strip(), cells[2].strip()
            if not title:
                # Existing DOCX metadata cannot distinguish a real blank from
                # a vMerge continuation; preserve as unresolved, do not inherit.
                if content:
                    _notice(notices, 'unresolved_merged_scoring_title', '评分行第二列为空；当前解析未保留纵向合并标记，不能确认归属，需核对原表。', block)
                last = None
                continue
            if _SCORING.fullmatch(title) and re.search('分值|评分|评审', content): continue
            if not content or re.fullmatch(r'[\d\s.、\-–~～%分]+', title): continue
            if len(title) > 110:
                _notice(notices, 'invalid_scoring_title', '评分表第二列不是简短标题，保留来源供核对。', block)
                continue
            existing = next((g for g in groups if _norm(g['title']) == _norm(title)), None)
            if existing is None:
                existing = {'id': _id('score-group', *_identity(block), _norm(title)), 'title': title,
                            'origin': 'scoring', 'source_refs': [], 'children': [],
                            'source_text': [], 'explicit': False,
                            'category': 'pricing' if re.search('价格|报价|商务报价', section) else 'technical' if '技术' in section else 'business' if '商务' in section else 'unspecified'}
                groups.append(existing)
            ref = _ref(block, title, 2)
            if ref not in existing['source_refs']: existing['source_refs'].append(ref)
            existing['source_text'].append(content)
            children = _children(content, block, requirements)
            for child in children:
                prior = next((c for c in existing['children'] if _norm(c['title']) == _norm(child['title'])), None)
                if prior:
                    prior['source_refs'].extend(r for r in child['source_refs'] if r not in prior['source_refs'])
                    prior['requirement_ids'] = list(dict.fromkeys([*prior['requirement_ids'], *child['requirement_ids']]))
                else: existing['children'].append(child)
            if not children:
                _notice(notices, 'no_actionable_scoring_children', '该评分项未提取到具体响应主题（可能仅有计分公式或档位）；保留原文，不编造二级目录。', block)
            last = existing
    return groups


def recommend(blocks, requirements=()):
    """Return source-backed candidates. No inputs are changed.

    ``explicit`` means visible H1/H2 are specified (or the source explicitly
    declares completeness); it is not a certification that parsing was perfect.
    Partial sources retain their specified H2, and only gaps use scoring topics.
    Generic modules deliberately do not enter this source helper.
    """
    blocks, requirements = list(blocks), list(requirements)
    notices = []
    explicit, complete, complete_wording = _explicit(blocks, notices)
    scoring = _scored(blocks, requirements, notices)
    groups = copy.deepcopy(explicit)
    if explicit:
        mode = 'explicit' if complete else 'partial'
        reason = '来源明确声明目录完整，按规定保留。' if complete_wording else '可见一级项均有规定二级项，按原目录保留。' if complete else '已有规定一级目录，但未规定全部二级目录；缺失部分按评分细则补齐。'
        if not complete:
            for score in scoring:
                matched = next((g for g in groups if _norm(g['title']) == _norm(score['title'])), None)
                if matched:
                    matched['scoring_source_refs'] = copy.deepcopy(score['source_refs'])
                    matched['scoring_source_text'] = copy.deepcopy(score['source_text'])
                    if not matched['children']:
                        matched['children'] = copy.deepcopy(score['children'])
                        matched['supplemented_from_scoring'] = bool(score['children'])
                    else:
                        _notice(notices, 'explicit_children_retained', f'“{matched["title"]}”已有规定二级项，保留原项；评分内容保留为来源，不覆盖规定目录。')
                else:
                    groups.append(copy.deepcopy(score))
                    _notice(notices, 'unmatched_scoring_parent', f'评分项“{score["title"]}”与规定一级项无精确同名关联，单列评分来源供核对。')
        else:
            _notice(notices, 'visible_structure_only', '已按可见规定目录保留；完整判定仅基于已解析来源层级或明确用语，不代表原文件解析无遗漏。')
    else:
        groups = scoring
        mode = 'scoring' if scoring else 'empty'
        reason = '未识别明确目录，按评分表第二逻辑列组织一级目录、第三逻辑列提取响应主题。' if scoring else '未识别明确目录或可用评分细则；通用目录仍由用户手动选择。'
    for index, group in enumerate(groups):
        group['order'] = index
        group['enabled'] = True
        for i, child in enumerate(group['children']):
            child['order'] = i
            child['enabled'] = True
            if child['origin'] == 'tender':
                refs = child['source_refs']
                for ref in refs:
                    block = next((b for b in blocks if (b.get('id') or b.get('chunk_id')) == ref.get('chunk_id')), None)
                    if block: child['requirement_ids'] = list(dict.fromkeys([*child['requirement_ids'], *_req_ids(requirements, block, child['source_text'])]))
    return {'version': VERSION, 'mode': mode, 'groups': groups, 'notices': notices,
            'explicit_count': len(explicit), 'scoring_count': len(scoring), 'completeness_reason': reason}
