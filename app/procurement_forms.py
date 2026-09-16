"""Loss-aware rendering of procurement qualification and price templates.

Pure functions: no files, databases, models, signatures, or corporate facts are
created. Source blocks are data. A rendered declaration remains proposed text
requiring enterprise recognition and actual signature/delivery.
"""
from __future__ import annotations

import copy
from collections import Counter
from decimal import Decimal, InvalidOperation
import re

VERSION = 'procurement-forms-1'
_APPENDIX = re.compile(r'^\s*附件\s*(\d+)\s*[：:]\s*(.+?)\s*$')
_NUMBERED = re.compile(r'^\s*(?:[▲★]\s*)?\d+[.、．)]')
_PERSONAL = re.compile(r'我方|我公司|我司|本公司|本单位|本人')
_EMPTY = re.compile(r'^[_＿\s]*$')
_DATE_LINE = re.compile(r'^(?:(?:承诺|签署|报价)?日期\s*[:：].*|[_＿\s]*年[_＿\s]*月[_＿\s]*日)$')
_IDENTITIES = {'供应商名称':'company_name','供应商':'company_name','投标人名称':'company_name','投标人':'company_name',
               '承诺单位':'company_name','采购人名称':'buyer','采购人':'buyer',
               '项目名称':'tender_name','采购项目名称':'tender_name','采购项目':'tender_name',
               '项目编号':'project_number','采购项目编号':'project_number'}
_INLINE = re.compile(r'[（(]\s*(供应商名称|投标人名称|采购人名称|项目名称|采购项目名称|项目编号|采购项目编号)\s*[）)]')


def _ref_key(ref):
    if not isinstance(ref,dict):raise ValueError('表单来源引用格式无效')
    key=(str(ref.get('document_id') or ''),str(ref.get('chunk_id') or ''),str(ref.get('locator') or ''))
    if not key[1] and not key[2]:raise ValueError('表单来源缺少块ID和原文位置，不能确定顺序')
    return key


def _text(value):
    return str(value or '').replace('\r\n','\n').replace('\r','\n')


def _nodes(spec):
    schema=spec.get('form_schema') or {}
    refs=schema.get('source_refs')
    if not isinstance(refs,list) or not refs:
        raise ValueError('采购表单缺少完整有序来源，不能用简化表静默替代')
    ordered={}
    for index,ref in enumerate(refs):
        key=_ref_key(ref)
        if key in ordered:raise ValueError('采购表单来源位置重复，无法无损确认节点顺序')
        if not isinstance(ref.get('quote'),str):raise ValueError('采购表单来源没有可读取的原文')
        ordered[key]=index
    paragraphs={}
    for item in schema.get('paragraphs',[]):
        if not isinstance(item,dict) or not isinstance(item.get('text'),str):raise ValueError('采购表单段落结构无效')
        key=_ref_key(item.get('source'))
        if key not in ordered or key in paragraphs:raise ValueError('采购表单段落缺少唯一来源顺序')
        if _text(refs[ordered[key]]['quote'])!=_text(item['text']):raise ValueError('采购表单段落与来源原文不一致，未静默选择其中一个版本')
        paragraphs[key]=item
    rows_by_ref={};tables={}
    for ti,table in enumerate(schema.get('tables',[])):
        rows=table.get('rows');sources=table.get('source_refs')
        if not isinstance(rows,list) or not isinstance(sources,list) or not rows or len(rows)!=len(sources):
            raise ValueError('采购表单表格行与来源数量不一致，无法保证字段完整')
        widths=[]
        for ri,(row,ref) in enumerate(zip(rows,sources)):
            cells=row.get('cells');spans=row.get('spans') or [1]*len(cells or [])
            if (not isinstance(cells,list) or not cells or any(not isinstance(cell,str) for cell in cells)
                    or len(spans)!=len(cells) or any(type(span) is not int or span<1 for span in spans)):
                raise ValueError('采购表单单元格或合并信息无效')
            if any('|' in cell for cell in cells):raise ValueError('原表单单元格含Markdown分隔符，不能在当前渲染器中无损呈现')
            widths.append(sum(spans));key=_ref_key(ref)
            if key not in ordered or key in rows_by_ref or key in paragraphs:
                raise ValueError('采购表单表格行缺少唯一来源顺序')
            if _text(ref['quote'])!=_text(refs[ordered[key]]['quote']):raise ValueError('采购表单表格来源版本冲突')
            rows_by_ref[key]=(ti,ri)
        if len(set(widths))!=1 or widths[0]>30:raise ValueError('采购表单网格宽度不一致或过宽，不能推测缺失单元格')
        positions=[ordered[_ref_key(ref)] for ref in sources]
        if positions!=list(range(positions[0],positions[0]+len(positions))):
            raise ValueError('采购表单表格行被其他节点打断或顺序不一致，不能静默重排')
        tables[ti]=table
    nodes=[]
    for index,ref in enumerate(refs):
        key=_ref_key(ref)
        if key in rows_by_ref:
            ti,ri=rows_by_ref[key]
            if ri==0:nodes.append({'kind':'table','table':copy.deepcopy(tables[ti]),'source_refs':copy.deepcopy(tables[ti]['source_refs']),'source_index':index})
            continue
        if re.search(r'(?:^|/)\s*表格\s*\d+',ref.get('locator','')) and key not in paragraphs:
            raise ValueError('原来源含表格行但缺少对应网格，未退化成普通段落')
        nodes.append({'kind':'paragraph','text':ref['quote'],'source_refs':[copy.deepcopy(ref)],'source_index':index})
    return nodes,len(refs)


def _name_candidates(project,spec):
    texts=[r.get('quote','') for r in (spec.get('form_schema') or {}).get('source_refs',[])]
    for item in (project.get('metadata') or {}).get('proposal_blueprint',{}).get('sections',[]):
        texts.extend(r.get('quote','') for r in (item.get('form_schema') or {}).get('source_refs',[]))
    labelled=[];embedded=[]
    for text in texts:
        match=re.fullmatch(r'\s*(?:项目名称|采购项目名称|采购项目)\s*[:：]\s*([^\n|]{2,160})\s*',text)
        if match and not _EMPTY.fullmatch(match[1]) and not _INLINE.fullmatch(match[1]):labelled.append(match[1].strip())
        for pattern in (r'已仔细研究了\s+([^（）\n]{2,160}?项目)\s+[（(]项目编号',r'我方在\s+([^\n。]{2,160}?项目)\s+中做'):
            embedded.extend(m.group(1).strip() for m in re.finditer(pattern,text))
    return set(labelled or embedded)


def _identity(project,spec):
    candidates=_name_candidates(project,spec)
    if len(candidates)>1:
        buyer=str(project.get('buyer') or '').strip()
        # The same tender can explicitly print both buyer+project and the short
        # project name. Use the already printed full form only when that exact
        # known-buyer prefix is the sole difference; never guess by length alone.
        stripped={name[len(buyer):].lstrip() if buyer and name.startswith(buyer) else name for name in candidates}
        if not buyer or len(stripped)!=1:raise ValueError('采购表单出现不同项目名称，不能把工作区名称当作实际招标名称填入')
        name=next(name for name in candidates if name.startswith(buyer))
    else:name=next(iter(candidates)) if candidates else project.get('actual_tender_name') or ''
    cover=project.get('_cover_identity') or {}
    if not name and cover.get('status')=='source_verified':name=cover.get('project_name') or ''
    return {'company_name':str(project.get('company_name') or ''),'buyer':str(project.get('buyer') or ''),
            'project_number':str(project.get('project_number') or ''),'tender_name':name}


def _fill_identity(text,known,changes,source):
    def inline(match):
        value=known.get(_IDENTITIES[match[1]])
        if not value:return match[0]
        changes.append({'field':match[1],'before':match[0],'after':value,'source':source})
        return value
    # Replace only literal empty placeholder tokens, not text already filled by
    # procurement (including the agency's account and third-party identity).
    result=_INLINE.sub(inline,text)
    match=re.fullmatch(r'(\s*)([^：:\n]+)([：:])([_＿\s]*)([（(](?:盖章|公章)[）)])?\s*',result)
    if match and match[2].strip() in _IDENTITIES:
        label=match[2].strip();value=known.get(_IDENTITIES[label])
        if value:
            result=match[1]+label+match[3]+value+(match[5] or '')
            changes.append({'field':label,'before':text,'after':result,'source':source})
    return result


def _blank_cell(value):
    return bool(re.fullmatch(r'[￥¥_＿\s]*',value))


def _confirmed(quote):
    return isinstance(quote,dict) and quote.get('confirmed') is True and not quote.get('issues')


def _amount(value):
    if isinstance(value,bool) or value is None:return None
    text=str(value).strip()
    try:number=Decimal(text)
    except InvalidOperation:return None
    return text if number.is_finite() and number>=0 else None


def _pricing_cells(table,quote,changes):
    if not _confirmed(quote):return
    rows=table['rows']
    if len(rows)<2:return
    # Column contracts are explicit. No currency conversion, order-based item
    # matching, or distribution of a total across unrelated original rows.
    headers=[re.sub(r'\s+','',v).replace('(','（').replace(')','）') for v in rows[0]['cells']]
    if any(span!=1 for span in (rows[0].get('spans') or [1]*len(headers))):return
    names={'报价项目','费用项目','服务项目','项目名称','品目','分项名称'}
    name_col=next((i for i,h in enumerate(headers) if h in names),None)
    aliases={'数量':'quantity','单价（元）':'unit_price','含税单价（元）':'unit_price','小计（元）':'subtotal',
             '含税小计（元）':'subtotal','分项金额（元）':'subtotal','含税报价（元）':'total_or_subtotal',
             '投标报价（元）':'total_or_subtotal','含税总价（元）':'total_or_subtotal','合计（元）':'total_or_subtotal'}
    items=quote.get('items') if isinstance(quote.get('items'),list) else []
    amount_cols=[i for i,h in enumerate(headers) if h in aliases and aliases[h]!='quantity']
    for ri,row in enumerate(rows[1:],1):
        cells=row['cells'];spans=row.get('spans') or [1]*len(cells)
        if len(cells)!=len(headers) or any(span!=1 for span in spans):continue
        name=cells[name_col].strip() if name_col is not None else ''
        matching=[item for item in items if isinstance(item,dict) and str(item.get('name') or '').strip()==name and name]
        selected=matching[0] if len(matching)==1 else None
        total_row=name in ('合计','总计','报价合计','投标总价','含税合计')
        single_total=(len(rows)==2 and len(amount_cols)==1 and name_col is not None)
        for ci,header in enumerate(headers):
            field=aliases.get(header)
            if not field or not _blank_cell(cells[ci]):continue
            candidate=None
            if total_row and field in ('subtotal','total_or_subtotal'):
                candidate=quote.get('total_including_tax')
            elif field=='total_or_subtotal':
                candidate=quote.get('total_including_tax') if total_row or single_total else selected.get('subtotal') if selected else None
            elif selected:candidate=selected.get(field)
            value=_amount(candidate)
            if value is not None:
                before=cells[ci];cells[ci]=value
                changes.append({'field':header,'row':ri+1,'column':ci+1,'item':name,'before':before,'after':value,'source':table['source_refs'][ri]})


def _table_content(table,known,changes,form_title,quote=None):
    table=copy.deepcopy(table)
    for ri,row in enumerate(table['rows']):
        cells=row['cells'];ref=table['source_refs'][ri]
        for ci,value in enumerate(cells):
            cells[ci]=_fill_identity(value,known,changes,ref)
        # Only explicit label/value pairs. Names of legal representatives,
        # delegates, banking and relationship fields are deliberately absent.
        for ci,label in enumerate(cells[:-1]):
            normalized=label.strip().rstrip('：:')
            field=_IDENTITIES.get(normalized)
            if normalized=='申报人名称' and '供应商控股及管理关系' in form_title:field='company_name'
            if field and known.get(field) and _EMPTY.fullmatch(cells[ci+1]):
                changes.append({'field':normalized,'before':cells[ci+1],'after':known[field],'source':ref,'row':ri+1,'column':ci+2})
                cells[ci+1]=known[field]
    if quote is not None:_pricing_cells(table,quote,changes)
    expanded=[]
    for row in table['rows']:
        values=[]
        for cell,span in zip(row['cells'],row.get('spans') or [1]*len(row['cells'])):
            # Markdown has no merged cells. Retain every field in its original
            # grid position; native cells/spans remain available in typed blocks.
            values.append(cell.replace('\r','').replace('\n',' '));values.extend(['']*(span-1))
        expanded.append(values)
    content=['| '+' | '.join(expanded[0])+' |','| '+' | '.join(['---']*len(expanded[0]))+' |']
    content.extend('| '+' | '.join(row)+' |' for row in expanded[1:])
    return '\n'.join(content),table


def _field_line(text):
    stripped=text.strip()
    return (bool(_DATE_LINE.fullmatch(stripped)) or
            bool(re.search(r'签字|签名|盖章',stripped) and len(stripped)<180 and ('：' in stripped or ':' in stripped)) or
            bool(re.fullmatch(r'[^：:\n]{1,45}[：:][_＿\s]*',stripped)))


def _guidance(text,active):
    stripped=text.strip()
    if _field_line(text) and stripped not in ('报价要求：','报价要求:','填表说明：','填表说明:'):return False,None
    if re.fullmatch(r'(?:报价要求|填表说明|填写说明|填报说明|编制说明)\s*[：:]?',stripped):return True,'explicit_guidance_heading'
    prefix=re.match(r'^(?:注|注释|填写说明|填表说明|填报说明)\s*[：:]',stripped)
    if prefix and not (_PERSONAL.search(stripped) and not re.search(r'填写|填报|填列|请|须在.*栏',stripped)):
        return True,'explicit_form_note'
    if re.match(r'^说明\s*[：:]',stripped) and re.search(r'填写|填报|填列|请在|请按',stripped) and not _PERSONAL.search(stripped):
        return True,'explicit_filling_instruction'
    if active and _NUMBERED.match(stripped) and not _PERSONAL.search(stripped):return True,'numbered_guidance_continuation'
    if (active and re.match(r'^(?:请|须|应|供应商(?:应|须)|投标人(?:应|须))',stripped)
            and re.search(r'填写|填报|填列|提供|提交|签字|盖章',stripped) and not _PERSONAL.search(stripped)):
        return True,'explicit_guidance_continuation'
    return False,None


def _unfilled(text):
    result=[]
    if _DATE_LINE.fullmatch(text.strip()):
        value=re.split(r'[：:]',text,maxsplit=1)[-1].strip()
        if _EMPTY.fullmatch(value) or re.fullmatch(r'[_＿\s]*年[_＿\s]*月[_＿\s]*日',value):result.append('日期（保留原空栏）')
    result.extend(match[1] for match in _INLINE.finditer(text))
    for match in re.finditer(r'[（(]\s*(姓名|法定代表人姓名|负责人姓名|被授权人姓名)\s*[）)]',text):result.append(match[1])
    if _field_line(text) and not result:
        before=re.split(r'[：:]',text,maxsplit=1)[0].strip()
        rest=re.split(r'[：:]',text,maxsplit=1)[1] if re.search(r'[：:]',text) else ''
        if not re.sub(r'[_＿\s]|[（(](?:签字|签名|盖章|签字或盖章)[）)]','',rest):result.append(before)
    return result


def _render(project,spec,kind,confirmed_quote=None):
    nodes,source_count=_nodes(spec);known=_identity(project,spec)
    parts=[];blocks=[];notes=[];changes=[];forms=[];coverage=[];current=None;guidance=False
    ids=list(spec.get('requirement_ids') or [])
    def new_form(title,number=None):
        form={'form_id':str(number or 'form')+'-'+str(len(forms)+1),'title':title,'source_count':0,'body_source_count':0,
              'note_source_count':0,'tables':0,'date_fields':0,'signature_fields':0,'unfilled_fields':[]}
        forms.append(form);return form
    for node in nodes:
        refs=node['source_refs'];raw=node.get('text','');first=refs[0]
        attachment=_APPENDIX.fullmatch(raw) if node['kind']=='paragraph' else None
        if attachment:current=new_form(raw.strip(),attachment[1]);guidance=False
        elif current is None:current=new_form(str(spec.get('title') or spec.get('group_title') or '采购表单'))
        current['source_count']+=len(refs)
        nonbody=all(re.match(r'^\s*(?:页眉|页脚)(?:\s|/|$)',ref.get('locator','')) for ref in refs)
        is_note,why=_guidance(raw,guidance) if node['kind']=='paragraph' and not attachment else (False,None)
        if nonbody or is_note:
            category='source_non_body' if nonbody else 'form_instruction'
            why='源定位明确属于页眉或页脚，不是表单正文' if nonbody else why
            message=f"《{current['title']}》"+('非正文来源：' if nonbody else '填表或采购执行说明：')+raw
            notes.append({'kind':'editorial','message':message,'raw':raw,'requirement_ids':ids,'blocks_content':False,'blocks_delivery':False,
                          'form_id':current['form_id'],'source_refs':copy.deepcopy(refs),'source':VERSION,'classification':category,'reason':why})
            current['note_source_count']+=len(refs);guidance=is_note
            destination='internal_note'
            rendered=''
        else:
            guidance=False;destination='body'
            if node['kind']=='table':
                rendered,table=_table_content(node['table'],known,changes,current['title'],confirmed_quote if kind=='pricing' else None)
                node={**node,'table':table};current['tables']+=1
                for ri,row in enumerate(table['rows']):
                    for ci,value in enumerate(row['cells']):
                        if _EMPTY.fullmatch(value):
                            headers=table['rows'][0]['cells']
                            if ri and len(row['cells'])==len(headers) and headers[-1].strip() in ('页码','备注','说明'):
                                header=headers[ci].strip()
                                if header in ('备注','说明'):continue  # Optional fields remain empty, not invented missing facts.
                                if header=='页码':
                                    current['unfilled_fields'].append({'field':'附件装入后的实际页码','row':ri+1,'column':ci+1,'source':table['source_refs'][ri],'delivery_only':True})
                                    continue
                                if kind=='pricing':
                                    label=header+(' / '+row['cells'][0].strip() if ci else '')
                                else:label=' / '.join(dict.fromkeys(cell.strip() for cell in row['cells'][:ci] if cell.strip()))
                            else:label=' / '.join(dict.fromkeys(cell.strip() for cell in row['cells'][:ci] if cell.strip()))
                            if not label:continue  # A vertical-merge continuation is not a new fillable fact.
                            current['unfilled_fields'].append({'field':label,'row':ri+1,'column':ci+1,'source':table['source_refs'][ri]})
            else:
                rendered=_fill_identity(raw,known,changes,first)
                if attachment:rendered='### '+rendered
                current['date_fields']+=bool(_DATE_LINE.fullmatch(raw.strip()))
                current['signature_fields']+=bool(re.search(r'签字|签名|盖章',raw) and _field_line(raw))
                current['unfilled_fields'].extend({'field':field,'source':first} for field in _unfilled(rendered))
            parts.append(rendered);current['body_source_count']+=len(refs)
        blocks.append({**copy.deepcopy(node),'form_id':current['form_id'],'destination':destination,'rendered':rendered})
        coverage.extend({'source':ref,'form_id':current['form_id'],'destination':destination,'node_kind':node['kind']} for ref in refs)
    if len(coverage)!=source_count:raise ValueError('采购表单来源覆盖不完整，未输出部分正文')
    central=('集中核对并认可本节各附件的拟签声明、响应承诺和授权范围；保留原采购条件，不认定已签、已盖章、资格事实已核验或附件已装入，不逐子条款增加证明材料'
             if kind=='qualification' else '按原报价项目及表头核对实际报价；金额只采用明确确认且可精确映射的数据，原报价约束仍须落实，不认定签字盖章或提交完成')
    notes.append({'kind':'qualification_declaration' if kind=='qualification' else 'content_gap','message':central,'raw':central,
                  'requirement_ids':ids,'blocks_content':True,'blocks_delivery':True,'form_ids':[f['form_id'] for f in forms],'source':VERSION})
    for form in forms:
        for delivery in (False,True):
            fields=[x for x in form['unfilled_fields'] if bool(x.get('delivery_only'))==delivery]
            if not fields:continue
            labels=list(dict.fromkeys(x['field'] for x in fields))
            notes.append({'kind':'delivery' if delivery else 'content_gap','message':f"《{form['title']}》保留待填写原字段："+'、'.join(labels),
                          'requirement_ids':ids,'blocks_content':not delivery,'blocks_delivery':True,'form_id':form['form_id'],
                          'fields':fields,'source':VERSION})
    for note in notes:
        note.setdefault('raw',note['message'])
    reason='本节采购表单及拟签文本已按源顺序呈现；具体字段、声明认可和交付事项见内部待办，尚未认定已完成'
    return ({'content':'\n\n'.join(parts),'responses':[{'requirement_id':rid,'response':'','evidence_ids':[],'gap':True,'gap_reason':reason} for rid in ids],
             '_internal_notes':notes,'_generation_mode':'procurement_'+kind+'_forms','_model_calls':0,'_selected_asset_ids':[],
             '_typed_gap_notes_complete':True,'_procurement_form_blocks':blocks,
             '_form_audit':{'version':VERSION,'source_count':source_count,'represented_source_count':len(coverage),'forms':forms,
                            'coverage':coverage,'identity_fills':changes,'enterprise_fact_evidence':False,
                            'declarations_approved':False,'signed':False,'sealed':False,
                            'native_table_spans_retained':True,'markdown_grid_note':'Markdown按原网格展开合并位置，表内换行仅改为空格；typed blocks保留原cells及spans'}}, {})


def render_qualification(project,spec):
    """Preserve all ordered qualification forms; never approve their statements."""
    return _render(project,spec,'qualification')


def render_pricing(project,spec,confirmed_quote=None):
    """Use only explicitly mapped confirmed values, never procurement budgets."""
    return _render(project,spec,'pricing',confirmed_quote)
