"""Local, frozen delivery selections; no model, approval or business-data writes.

Historical facts and same-tender personnel candidates are deliberately separate
from the general product-evidence channel. A reviewed value is never a legal,
qualification, scoring, appointment, attachment-completion or signature verdict.
"""
from __future__ import annotations
import copy
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from . import db

VERSION = 'proposal-deliverables-1'
USES = {'historical_fact', 'certificate_fact', 'same_project_candidate'}
STATES = {'verified', 'source_claim', 'candidate', 'missing', 'conflict', 'structural'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def template_digest(schema):
    # A fresh import of the same tender gets new chunk IDs. Bind the actual
    # form layout/text plus the separately checked tender hash, not those IDs.
    return digest({'tables':[[{'cells':r.get('cells',[]),'spans':r.get('spans',[])} for r in t.get('rows',[])]
                              for t in schema.get('tables',[])],
                   'paragraphs':[p.get('text') if isinstance(p,dict) else p for p in schema.get('paragraphs',[])]})


def _sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else str(value).encode()).hexdigest()


def _date(value):
    match = re.search(r'(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})', str(value or ''))
    return date(*map(int, match.groups())) if match else None


def _note(message, ids=(), *, item_id='', field='', source_refs=(), delivery=False):
    return {'kind':'delivery' if delivery else 'content_gap', 'message':message, 'raw':message,
            'requirement_ids':list(ids), 'blocks_content':not delivery, 'blocks_delivery':True,
            'source':VERSION, 'item_id':item_id, 'field':field, 'source_refs':copy.deepcopy(list(source_refs))}


def _safe_root_path(value):
    root=(db.DATA/'assets').resolve()
    if not value or str(value).startswith(('//','\\\\')):
        raise ValueError('交付清单必须位于本地DATA/assets目录')
    path=Path(value)
    if not path.is_absolute():path=root/path
    path=path.resolve()
    if not path.is_relative_to(root):raise ValueError('交付清单或图片路径越界')
    return path


def load_manifest(project):
    selection=(project.get('metadata') or {}).get('proposal_deliverables')
    if not selection:return None
    if not isinstance(selection,dict):raise ValueError('项目交付资料选择格式无效')
    path=_safe_root_path(selection.get('manifest_path'))
    if not path.is_file() or path.stat().st_size>12*1024*1024:raise ValueError('交付清单缺失或过大')
    raw=path.read_bytes()
    if _sha(raw)!=selection.get('sha256'):raise ValueError('交付清单版本已变化，须重新核对本次选择')
    manifest=json.loads(raw)
    if manifest.get('version')!=VERSION or not isinstance(manifest.get('sections'),list):
        raise ValueError('交付清单版本或章节格式无效')
    if len(manifest['sections'])>100:raise ValueError('交付清单章节数超过上限')
    expected=manifest.get('tender_sha256s')
    actual={d['sha256'] for d in db.all("SELECT sha256 FROM documents WHERE project_id=? AND source_type='tender'",(project['id'],))}
    if not isinstance(expected,list) or not actual or set(expected)!=actual:
        raise ValueError('交付资料仅适用已核对的同一招标版本，本项目招标原件不匹配')
    if manifest.get('domain')!=project.get('domain') or manifest.get('bidder_name')!=project.get('company_name'):
        raise ValueError('交付资料的产品范围或投标主体不匹配')
    if manifest.get('reference_date'):
        if _date(manifest['reference_date'])!=_date(project.get('deadline')) or not _date(project.get('deadline')):
            raise ValueError('交付资料历史核对日期与本项目采购截止日期不一致')
    return manifest


class _Validator:
    def __init__(self,project,manifest):
        self.project,self.manifest=project,manifest
        self.documents={};self.chunks={};self.errors={}
        self.asset_selections={a['asset']['asset_id']:a for s in manifest.get('sections',[]) for a in s.get('assets',[]) if isinstance(a.get('asset'),dict) and a['asset'].get('asset_id')}
        self.checked_images={}

    def source(self,ref):
        if not isinstance(ref,dict):raise ValueError('缺少具体来源entry')
        did,cid=ref.get('document_id'),ref.get('chunk_id')
        if not did or not cid:raise ValueError('缺少来源文件或entry ID')
        if did not in self.documents:
            d=db.one('SELECT * FROM documents WHERE id=?',(did,))
            if not d:raise ValueError('来源文件已删除')
            if d['source_type']!='knowledge' or d.get('project_id') or d['status']!='approved' or d['parse_status']!='ready':
                raise ValueError('来源不是当前已批准且解析完成的企业资料')
            if d['scope'] not in ('general',self.project.get('domain')):raise ValueError('来源产品范围不适用')
            if d.get('valid_until'):
                expires=date.fromisoformat(d['valid_until'])
                if expires<date.today():raise ValueError('来源文件明确有效期已过，不自动续期')
            meta=d.get('metadata') or {}
            if any(meta.get(k) for k in ('ai_generated','conflicts_with_project','failure_case','failure_review','postmortem')) or meta.get('source_kind') in ('generated','tender','failure_case','failure_review','postmortem'):
                raise ValueError('来源已标记为生成稿、冲突或失败资料')
            applicable=meta.get('project_ids',meta.get('applicable_projects'))
            if applicable and (not isinstance(applicable,list) or self.project['id'] not in applicable):
                raise ValueError('来源现有项目适用范围不包含本项目')
            if str(d['path']).startswith(('//','\\\\')):raise ValueError('不从网络路径读取交付原件')
            source_path=Path(d['path']).resolve()
            if not source_path.is_file() or source_path.stat().st_size>512*1024*1024 or _sha(source_path.read_bytes())!=d['sha256']:
                raise ValueError('来源原件缺失或实际文件hash已改变')
            self.documents[did]=d
        d=self.documents[did]
        if d['sha256']!=ref.get('source_sha256'):raise ValueError('来源原件版本与冻结记录不一致')
        if cid not in self.chunks:self.chunks[cid]=db.one('SELECT * FROM chunks WHERE id=?',(cid,))
        chunk=self.chunks[cid]
        if not chunk or chunk['document_id']!=did or _sha(chunk['text'])!=ref.get('chunk_sha256'):
            raise ValueError('来源entry已删除、重解析或正文变化')
        approval=(d.get('metadata') or {}).get('knowledge_approval') or {}
        if approval.get('source_sha256')!=d['sha256']:raise ValueError('来源认可记录未绑定当前原件版本')
        entry=next((e for e in approval.get('entries',[]) if e.get('id')==cid and e.get('sha256')==ref['chunk_sha256']),None)
        if not entry or not isinstance(entry.get('text'),str):raise ValueError('来源entry不在当前认可范围')
        from .evidence_retrieval import _source_subset
        if not _source_subset(entry['text'],chunk['text']):raise ValueError('认可文本不能回到实际读取原文')
        if ref.get('approved_text_sha256') and _sha(entry['text'])!=ref['approved_text_sha256']:
            raise ValueError('entry认可内容范围已变化')
        return {k:ref[k] for k in ('document_id','source_sha256','chunk_id','chunk_sha256','approved_text_sha256','locator') if k in ref}

    def item(self,item):
        if item.get('use') not in USES:raise ValueError('交付条目用途未明确，不作为本项目事实')
        refs=item.get('source_refs')
        if not isinstance(refs,list) or not refs:raise ValueError('交付条目缺少已认可原文entry关联')
        checked=[self.source(ref) for ref in refs]
        conditions=item.get('conditions') or {}
        if conditions.get('subject') and conditions['subject']!=self.project.get('company_name'):
            raise ValueError('证照主体与本次投标主体不一致，未填为本企业当前证照')
        until=conditions.get('valid_until')
        if until:
            basis=_date(self.manifest.get('reference_date')) if conditions.get('as_of')=='tender_date' else date.today()
            if basis is None:raise ValueError('缺少可核对的采购基准日期')
            if date.fromisoformat(until)<basis:raise ValueError('证照图示期限未覆盖本次核对日期，需有效版本')
        if item.get('use')=='same_project_candidate' and self.manifest.get('personnel_use')!='same_tender_candidates_only':
            raise ValueError('人员仅可用于已核对同一项目的候选计划，不可作为当前任命')
        if item.get('use')=='same_project_candidate':
            for ref in checked:
                meta=self.documents[ref['document_id']].get('metadata') or {}
                if meta.get('other_customer_only') or meta.get('source_kind')=='customer_specific':
                    raise ValueError('来源明确为其他客户专属，不能迁移为本项目人员候选')
        return checked

    def asset(self,selection):
        if selection.get('include_in_draft') is not True:return None
        if selection.get('review_status')!='reviewed_for_draft':raise ValueError('图片尚未明确核对为本次草稿候选')
        self.item(selection)
        asset=copy.deepcopy(selection.get('asset') or {})
        if not any(ref['document_id']==asset.get('document_id') and ref['source_sha256']==asset.get('source_sha256') for ref in selection['source_refs']):
            raise ValueError('图片原件版本与其entry来源关联不一致')
        if (asset.get('source') or {}).get('display_transform'):raise ValueError('图片原件显示变换尚未复现')
        if asset.get('asset_id') in self.checked_images:return copy.deepcopy(self.checked_images[asset['asset_id']])
        asset['path']=str(_safe_root_path(asset.get('path')))
        from .document_assets import verified_asset_path
        verified_asset_path(asset,asset_root=db.DATA/'assets')
        asset['title']=selection.get('caption') or '历史资料关键页'
        asset['reviewed_title']=asset['title']
        self.checked_images[asset['asset_id']]=copy.deepcopy(asset)
        return asset

    def image_refs(self,item,cell):
        if cell.get('basis')!='reviewed_image_field':return []
        ids=cell.get('image_asset_ids') or item.get('image_asset_ids')
        if not isinstance(ids,list) or not ids:raise ValueError('图核对字段缺少具体图片ID及hash来源')
        result=[]
        for aid in ids:
            selection=self.asset_selections.get(aid)
            if not selection:raise ValueError('图核对字段引用的图片不在冻结清单中')
            image=self.asset({**selection,'include_in_draft':True})
            result.append({'asset_id':image['asset_id'],'sha256':image['sha256'],
                           'source_sha256':image['source_sha256']})
        return result


def _expanded_rows(table):
    rows=[]
    for row in table.get('rows',[]):
        cells=row.get('cells',[]);spans=row.get('spans') or [1]*len(cells)
        output=[]
        for index,value in enumerate(cells):
            output.append(value)
            output.extend(['']*(max(1,int(spans[index] if index<len(spans) else 1))-1))
        rows.append(output)
    return rows


def _flat_headers(rows,count):
    """Flatten leaf labels after span expansion, never by padding raw cells."""
    width=max(map(len,rows[:count]))
    header=[]
    for column in range(width):
        values=[str(row[column]).strip() for row in rows[:count] if column<len(row) and str(row[column]).strip()]
        label=values[-1] if values else ''
        if label=='证书名称' and any('执业' in value or '职业' in value for value in values[:-1]):label='执业证书名称'
        header.append(label)
    return [header]


def template_rows(table):
    rows=_expanded_rows(table)
    if len(rows)>1 and {'证书名称','级别','证号'}<=set(rows[1]):return _flat_headers(rows,2)+rows[2:]
    return rows


def _resume_tables(table, filled_rows):
    """Reflow a recognised label/value resume without dropping source cells.

    Fixed-cell coordinates refer to the expanded procurement grid. Recover each
    actual cell through its span before separating identity and experience. If
    the layout is ambiguous, retain the caller's complete original grid.
    """
    original=table.get('rows',[])
    if len(original)!=len(filled_rows):return None
    rows=[];span_rows=[]
    for source,filled in zip(original,filled_rows):
        cells=source.get('cells',[])
        spans=source.get('spans') or [1]*len(cells)
        if (len(spans)!=len(cells) or not cells
                or any(type(span) is not int or span<1 for span in spans)
                or sum(spans)!=len(filled)):
            return None
        logical=[];offset=0
        for span in spans:
            # A value written inside a merged-cell continuation must not vanish.
            if any(str(value if value is not None else '').strip() for value in filled[offset+1:offset+span]):return None
            logical.append(filled[offset]);offset+=span
        rows.append(logical);span_rows.append(spans)
    boundaries=[i for i,row in enumerate(rows)
                if len(row)==1 and re.fullmatch(r'(?:主要)?工作经历',str(row[0]).strip())]
    if len(boundaries)!=1:return None
    boundary=boundaries[0]
    if boundary<1 or boundary+2>=len(rows):return None
    fields=[]
    for source,current in zip(original[:boundary],rows[:boundary]):
        cells=source['cells']
        if len(cells)%2:return None
        for index in range(0,len(cells),2):
            label=cells[index]
            if not str(label).strip() or current[index]!=label:return None
            fields.append([label,current[index+1]])
    if not any(str(field[0]).strip()=='姓名' for field in fields):return None
    header=rows[boundary+1];header_spans=span_rows[boundary+1]
    if len(header)<2 or not all(str(value).strip() for value in header):return None
    if (header!=original[boundary+1]['cells']
            or any(spans!=header_spans for spans in span_rows[boundary+2:])):
        return None
    return {'fields':[['采购字段','候选资料']]+fields,
            'experience_heading':rows[boundary][0],
            'experience':rows[boundary+1:]}


def procurement_declaration(schema,project=None):
    """Keep the original proposed declaration, without inventing or approving it."""
    result=[];active=False
    for item in schema.get('paragraphs',[]):
        text=str(item.get('text') if isinstance(item,dict) else item or '').strip()
        if re.fullmatch(r'资格审查资料承诺书',text):
            if not active:result.append('资格审查资料承诺书（拟签文本）')
            active=True
            continue
        if active and re.match(r'^附件\s*\d+\s*[：:]',text):break
        if active and text:
            from .proposal_generation import _identity_field
            text=_identity_field(text,project)
            p=project or {}
            known={'项目名称':p.get('name'),'采购人名称':p.get('buyer'),'供应商名称':p.get('company_name')}
            text=re.sub(r'[（(]\s*(项目名称|采购人名称|供应商名称)\s*[）)]',
                        lambda match:str(known[match[1]]) if known.get(match[1]) else match[0],text)
            result.append(text)
    return result


def apply_section(project,unit,spec):
    if spec.get('content_kind') not in ('form','attachment') or spec.get('volume')=='pricing':return None
    manifest=load_manifest(project)
    if manifest is None:return None
    candidates=[s for s in manifest['sections'] if s.get('group_title')==spec.get('group_title')]
    matches=[s for s in candidates
             if not s.get('section_key') or s['section_key']==spec.get('section_key')]
    if candidates and not matches:raise ValueError('交付清单绑定了其他项目的章节键；未静默退回空表，请按同一招标表单重新核对选择')
    if not matches:return None
    if len(matches)!=1:raise ValueError('交付清单同一章节存在重复选择')
    selected=matches[0];schema=spec.get('form_schema') or {}
    if selected.get('template_sha256')!=template_digest(schema):raise ValueError('采购表单结构已变化，不能套用旧字段选择')
    from .proposal_generation import _markdown_table,_template_fields,_response_rows
    ids=[r['id'] for r in unit.get('requirements',[])];validator=_Validator(project,manifest)
    notes=[];parts=[];provenance=[];selected_assets=[];review_fields=[];filled=0;numeric_source_rows=[]
    def note(message,**kwargs):
        item=_note(message,ids,**kwargs)
        if item not in notes:notes.append(item)
    def checked(item):
        try:return validator.item(item)
        except (ValueError,KeyError,TypeError) as exc:
            note(f"{item.get('id','交付条目')}：{exc}",item_id=item.get('id',''),source_refs=item.get('source_refs',[]))
            return None
    def cells(item,values,labels=()):
        nonlocal filled
        refs=checked(item)
        output=[]
        for index,cell in enumerate(values):
            if not isinstance(cell,dict):cell={'value':cell,'state':'missing' if not cell else 'source_claim'}
            state=cell.get('state');value=cell.get('value','')
            if state not in STATES:raise ValueError('交付字段状态无效')
            image_refs=[];image_problem=None
            if state not in ('missing','conflict') and refs is not None:
                try:image_refs=validator.image_refs(item,cell)
                except (ValueError,KeyError,TypeError) as exc:image_problem=str(exc)
            if state in ('missing','conflict') or refs is None or image_problem:
                output.append('')
                if cell.get('reason'):note(cell['reason'],item_id=item.get('id',''),field=str(index),source_refs=item.get('source_refs',[]))
                if image_problem:note(image_problem,item_id=item.get('id',''),field=str(index),source_refs=item.get('source_refs',[]))
            else:
                output.append(value)
                if str(value).strip() and state!='structural':filled+=1
                if str(value).strip() and state!='structural':
                    label=str(labels[index] if index<len(labels) else index)
                    if not re.search(r'证号|证件号|身份证|号码|电话|手机|账号|邮箱',label):
                        safe_value=re.sub(r'(?<!\d)\d{11,19}[Xx]?(?!\d)','[编号已隐去]',str(value))
                        review_fields.append({'item_id':item.get('id'),'field':label,'use':item['use'],
                           'state':state,'value':safe_value,'basis':cell.get('basis') or 'approved_entry_claim',
                           'source_refs':refs,'image_refs':image_refs})
        if refs is not None:provenance.append({'item_id':item.get('id'),'use':item['use'],'source_refs':refs})
        for pending in item.get('pending',[]):
            note(str(pending),item_id=item.get('id',''),source_refs=item.get('source_refs',[]))
        return output
    rendered=set()
    for table_selection in selected.get('tables',[]):
        index=table_selection.get('schema_table_index')
        if index is None:
            if schema.get('tables'):raise ValueError('存在采购原表时不能用自定义表替换')
            table_rows=[table_selection.get('headers') or []]
            header_count=1
        else:
            if not isinstance(index,int) or not 0<=index<len(schema.get('tables',[])):raise ValueError('采购表索引无效')
            table_rows=_expanded_rows(schema['tables'][index]);rendered.add(index)
            header_count=int(table_selection.get('header_rows',1))
            if not 1<=header_count<=len(table_rows):raise ValueError('采购表头范围无效')
        if table_selection.get('layout')=='fixed_cells':
            for item in table_selection.get('rows',[]):
                labels=[]
                for row,col in item.get('positions',[]):
                    if not isinstance(row,int) or not isinstance(col,int) or not 0<=row<len(table_rows) or not 0<=col<len(table_rows[row]):
                        raise ValueError('固定采购表单元格位置越界')
                    prior=next((str(table_rows[row][i]) for i in range(col-1,-1,-1) if str(table_rows[row][i]).strip()),'')
                    labels.append(prior or f'采购表第{row+1}行第{col+1}列')
                values=cells(item,[c for c in item.get('cells',[])],labels)
                for change,value in zip(item.get('positions',[]),values):
                    row,col=change
                    if not 0<=row<len(table_rows) or not 0<=col<len(table_rows[row]):raise ValueError('固定采购表单元格位置越界')
                    if str(table_rows[row][col]).strip():raise ValueError('固定采购表只能补空白字段，不能覆盖标签或原要求')
                    table_rows[row][col]=value
        else:
            table_rows=_flat_headers(table_rows,header_count) if header_count>1 else table_rows[:header_count]
            width=max(map(len,table_rows))
            for item in table_selection.get('rows',[]):
                values=cells(item,item.get('cells',[]),table_rows[0])
                if len(values)!=width:raise ValueError('交付字段数量与采购原表列数不一致')
                table_rows.append(values)
        numeric_source_rows.extend(line for line in _markdown_table(table_rows).splitlines()
                                   if line.lstrip().startswith('|') and not re.search(r'(?<!\d)\d{11,19}[Xx]?(?!\d)',line))
        candidate=any(item.get('use')=='same_project_candidate' for item in table_selection.get('rows',[]))
        if candidate:
            parts.append('### 拟投入人员候选方案' if table_selection.get('layout')!='fixed_cells' else '### 拟投入候选人员简历')
        resume=(_resume_tables(schema['tables'][index],table_rows)
                if candidate and table_selection.get('layout')=='fixed_cells' and index is not None else None)
        if resume:
            parts.extend([_markdown_table(resume['fields']),
                          '#### '+str(resume['experience_heading']),
                          _markdown_table(resume['experience'])])
        else:
            parts.append(_markdown_table(table_rows))
    for index,table in enumerate(schema.get('tables',[])):
        if index not in rendered:parts.append(_markdown_table(template_rows(table)))
    for selection in selected.get('assets',[]):
        try:asset=validator.asset(selection)
        except (ValueError,KeyError,TypeError) as exc:
            note(f"候选图片{selection.get('id','')}未选入：{exc}",item_id=selection.get('id',''),delivery=True)
            continue
        if asset is None:continue
        selected_assets.append(asset)
        caption=str(selection.get('caption') or '历史资料关键页').replace(']','）').replace('[','（').replace('\n',' ')
        parts.append(f"![{caption}](asset:{asset['asset_id']})")
    declaration=procurement_declaration(schema,project)
    parts.extend(declaration);parts.extend(field for field in _template_fields(schema,project) if field not in declaration)
    for message in selected.get('pending',[]):note(str(message))
    note('本节候选证明的实际装入以本次导出记录为准；采购要求的签字、盖章及原件备查仍需独立完成',delivery=True)
    if any(p['use']=='same_project_candidate' for p in provenance):
        note('拟配置人员仅为同项目候选；须集中确认本次任命、授权、档期、驻场阶段及人天，未认定已任命或已驻场')
    if procurement_declaration(schema):note('集中确认《资格审查资料承诺书》适用条款与声明内容；按采购格式完成签字盖章，不逐条虚增独立证明')
    reason='本节具体差异及交付事项已集中记录，尚未认定完成'
    return ({'content':'\n\n'.join(filter(None,parts)), 'responses':_response_rows(unit,'',True,reason),
             '_internal_notes':notes,'_generation_mode':'deterministic_reviewed_deliverables',
             '_selected_asset_ids':list(dict.fromkeys(a['asset_id'] for a in selected_assets)),
             '_deliverable_assets':selected_assets,'_deliverable_provenance':provenance,
             '_deliverable_review_fields':review_fields,'_typed_gap_notes_complete':True,
             '_numeric_source_rows':numeric_source_rows,
             '_filled_cell_count':filled,'_model_calls':0}, {})


def review_context(project,section):
    """Fresh typed local-field evidence; no pictures, personal IDs or global facts."""
    if isinstance(section,dict):spec=section;sid=spec.get('section_id')
    else:
        sid=section
        spec=next((s for s in (project.get('metadata') or {}).get('proposal_blueprint',{}).get('sections',[]) if s.get('section_id')==sid),None)
    if not spec:return {'version':VERSION,'section_id':sid,'fields':[]}
    enriched=apply_section(project,{'id':sid,'requirements':[]},spec)
    fields=enriched[0].get('_deliverable_review_fields',[]) if enriched else []
    # Exact frozen table rows support their own numeric fields only. They are
    # never a numeric whitelist for unrelated capability sentences.
    rows=[line for line in (enriched[0]['content'].splitlines() if enriched else [])
          if line.lstrip().startswith('|') and not re.search(r'(?<!\d)\d{11,19}[Xx]?(?!\d)',line)]
    if enriched:rows=list(dict.fromkeys([*rows,*enriched[0].get('_numeric_source_rows',[])]))
    return {'version':VERSION,'section_id':sid,'scope':'this_section_reviewed_delivery_fields_only',
            'review_basis':'本轮冻结manifest的本地字段/原图核对；不是法律效力、真实性或评分结论',
            'fields':fields,'numeric_source_rows':rows}


def assets_for_export(project):
    manifest=load_manifest(project)
    if not manifest:return []
    validator=_Validator(project,manifest);result={}
    for section in manifest['sections']:
        for selection in section.get('assets',[]):
            try:asset=validator.asset(selection)
            except (ValueError,KeyError,TypeError):continue
            if asset:result[asset['asset_id']]=asset
    return list(result.values())
