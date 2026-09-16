"""Render procurement-defined volumes and keep the complete ledger separate."""
from __future__ import annotations
import copy
import csv
import json
import re
import tempfile
import zipfile
from pathlib import Path
from . import proposal_runtime as runtime, compilation_outline


def _paginate(source,output,*,pdf_path=None):
    from .proposal_pagination import paginate_docx
    return paginate_docx(source,output,pdf_path=pdf_path)


def _cell(value):
    return str(value or '').replace('|','／').replace('\n','；')


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join('---' for _ in headers)+' |',
                      *['| '+' | '.join(_cell(x) for x in row)+' |' for row in rows]])


def index_content(project,sections=None):
    """One index definition for generation/editor/export; page fields resolve in Word."""
    from . import review_index
    return review_index.markdown(review_index.build(project,sections))


def projected_sections(project, requirements, sections, volume):
    # Keep the complete durable set for response/integrity checks; participation
    # is a projection and must never delete the retained blueprint entries.
    full_sections=list(sections)
    plan=runtime.blueprint(project)
    specs={s['section_id']:s for s in plan['sections']}
    rows,issues=runtime.project_responses(project,full_sections,requirements)
    sections=compilation_outline.projection(project,full_sections)['active']
    retained_ids={s['id'] for s in full_sections}-{s['id'] for s in sections}
    result=[]
    for original in sections:
        spec=specs.get(original['id'])
        if not spec or spec['volume']!=volume:continue
        section=copy.deepcopy(original);title=spec['group_title']
        if spec['content_kind']=='form' and re.search('评审.*索引',title):
            from . import review_index
            saved=(project.get('metadata') or {}).get('proposal_section_results',{}).get(original['id'],{}).get('review_index',{})
            if saved.get('content_sha256')==review_index.content_digest(original.get('content','')) and saved.get('model'):
                section['_review_index_model']=copy.deepcopy(saved['model'])
            elif not original.get('content') and not original.get('user_edited'):
                section['_review_index_model']=review_index.build(project,sections)
                section['content']=review_index.markdown(section['_review_index_model'])
            elif original.get('content'):
                section['_review_index_model']=review_index.edited_model(original['content'],saved.get('model'))
            if section.get('_review_index_model'):
                from . import index_materials
                try:assets=index_materials.available_assets(project)
                except ValueError:assets=set()
                review_index.refresh_model_pages(project,section['_review_index_model'],full_sections,assets)
                current={s['id']:s for s in sections}
                invalid=set()
                page_text=section['content']+'\n'+'\n'.join(str(r['values'][-1]) for t in section['_review_index_model']['tables'] for r in t['rows'])
                for target in re.findall(r'\[\[PAGE:([a-f0-9]{32})\]\]',page_text):
                    if target in retained_ids or target in current and target in specs and not index_materials.status(current[target],specs[target],project,assets)['ready']:
                        invalid.add('[[PAGE:'+target+']]')
                for token in invalid:section['content']=section['content'].replace(token,'')
                for t in section['_review_index_model']['tables']:
                    for row in t['rows']:
                        for token in invalid:row['values'][-1]=row['values'][-1].replace(token,'').strip('\n')
            # A saved/manual body is authoritative; export must not silently
            # replace it with another header layout or discard user instructions.
        elif spec['content_kind']=='form' and re.search('(?:商务|技术).*偏离',title):
            # This procurement form asks for actual differences, not every
            # extracted requirement. Preserve the editable form verbatim.
            # In particular, neither source approval nor an AI response implies
            # the bidder has declared "no deviation".
            if not original.get('user_edited'):
                issues.append({'section_id':original['id'],'kind':'deviation_decision_missing',
                    'message':f'《{title}》实际偏离结论尚未填写确认。采购文件对空表有接受条款的约定，草稿空表不能作为本项目已确认结论。',
                    'source_refs':spec.get('source_refs',[])})
        result.append(section)
    return result,issues


def _csv_cell(value):
    text=str(value if value is not None else '')
    return "'"+text if text.lstrip().startswith(('=','+','-','@')) or text.startswith(('\t','\r')) else text


def bind_page_references(path, sections):
    """Use real Word bookmarks/PAGEREF fields, never guessed page numbers."""
    from lxml import etree as E
    W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    ns={'w':W[1:-1]};path=Path(path)
    with zipfile.ZipFile(path) as archive:
        entries={i.filename:(i,archive.read(i.filename)) for i in archive.infolist()}
    root=E.fromstring(entries['word/document.xml'][1]);body=root.find(W+'body')
    targets={};counter=50000
    group=''
    for paragraph in body.findall(W+'p'):
        style=paragraph.find('./'+W+'pPr/'+W+'pStyle')
        name=style.get(W+'val') if style is not None else ''
        text=''.join(paragraph.xpath('.//w:t/text()',namespaces=ns))
        if name=='Heading1':group=text
        if name!='Heading2':continue
        section=next((s for s in sections if s['title']==text and s.get('outline_group_title')==group and s['id'] not in targets),None)
        if not section:continue
        bookmark='mxs_'+section['id'][:32];counter+=1
        start=E.Element(W+'bookmarkStart',{W+'id':str(counter),W+'name':bookmark});end=E.Element(W+'bookmarkEnd',{W+'id':str(counter)})
        paragraph.insert(1 if paragraph.find(W+'pPr') is not None else 0,start);paragraph.append(end)
        targets[section['id']]=bookmark
    replaced=0
    for paragraph in root.iter(W+'p'):
        joined=''.join('\n' if node.tag==W+'br' else node.text or '' for node in paragraph.iter() if node.tag in (W+'t',W+'br'))
        matches=list(re.finditer(r'\[\[PAGE:([a-f0-9]{32})\]\]',joined))
        if not matches:continue
        if any(match[1] not in targets for match in matches):raise ValueError('评审索引引用的章节不存在，未生成虚假页码')
        for child in list(paragraph):
            if child.tag!=W+'pPr':paragraph.remove(child)
        def literal(value):
            if not value:return
            run=E.SubElement(paragraph,W+'r')
            for index,line in enumerate(value.split('\n')):
                if index:E.SubElement(run,W+'br')
                if line:E.SubElement(run,W+'t',{'{http://www.w3.org/XML/1998/namespace}space':'preserve'}).text=line
        end=0
        for match in matches:
            literal(joined[end:match.start()])
            field=E.SubElement(paragraph,W+'fldSimple',{W+'instr':' PAGEREF '+targets[match[1]]+' \\h ',W+'dirty':'true'})
            E.SubElement(E.SubElement(field,W+'r'),W+'t').text='____'
            replaced+=1;end=match.end()
        literal(joined[end:])
    xml=E.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
    temporary=path.with_name(path.name+'.fields.tmp')
    with zipfile.ZipFile(temporary,'w') as archive:
        for name,(info,data) in entries.items():archive.writestr(info,xml if name=='word/document.xml' else data)
    temporary.replace(path)
    return {'bookmarks':len(targets),'page_reference_fields':replaced,'page_numbers_refreshed':False}


def write_export(project, requirements, sections, output, company, format='docx', final=False):
    from .documents import compose_docx
    from . import index_materials,index_pages
    output=Path(output);plan=runtime.blueprint(project)
    revision=index_materials.input_revision(project,sections)
    names={'qualification':'01_资格文件','technical':'02_商务技术文件','pricing':'03_报价文件'}
    report={'profile':runtime.PROFILE,'volumes':{},'warnings':[],'internal_ledger_requirements':len(requirements)}
    def one(volume,path,pdf_path=None):
        chosen,issues=projected_sections(project,requirements,sections,volume)
        p={**project,'document_title':{'technical':'商务、技术文件','qualification':'资格文件','pricing':'报价文件'}[volume],
           '_proposal_volume':volume,
           '_omit_response_table':True,'_omit_attachments':True,'_omit_evidence_index':True,'_hide_inline_evidence':True}
        with tempfile.TemporaryDirectory(dir=path.parent,prefix='index-layout-') as tmp:
            source=Path(tmp)/'source.docx'
            result=compose_docx(p,[],chosen,str(source),company=company,final=final)
            from . import review_index
            review_index.restore_word_tables(source,chosen)
            fields=bind_page_references(source,chosen)
            pagination=_paginate(source,path,pdf_path=pdf_path)
            fields.update({k:pagination[k] for k in ('section_pages','page_count','page_numbers_refreshed')})
        report['volumes'][volume]={'file':path.name,'section_ids':[s['id'] for s in chosen],
                                  'response_version_issues':issues,**fields,'outline':result['outline'],'used_assets':result.get('used_assets',[]),
                                  'source_notes':result.get('source_notes',[])}
        report['warnings'].extend(result['warnings'])
        return chosen
    if format in ('docx','pdf'):
        docx=output if format=='docx' else output.with_suffix('.docx')
        one('technical',docx,output if format=='pdf' else None)
    elif format=='md':
        chosen,issues=projected_sections(project,requirements,sections,'technical')
        from .export_outline import outline_sections,part_body
        parts=['# '+project['name'],'商务、技术文件']
        for group in outline_sections(chosen):
            parts.append('## '+group['title'])
            for part in group['parts']:parts.extend(['### '+part['title'],part_body(part)])
        text='\n\n'.join(parts)
        text=re.sub(r'\[\[PAGE:[a-f0-9]{32}\]\]','____',text)
        output.write_text(text,encoding='utf-8')
    elif format=='zip':
        with tempfile.TemporaryDirectory(dir=output.parent,prefix='proposal-') as tmp:
            folder=Path(tmp)
            for volume,name in names.items():one(volume,folder/(name+'.docx'))
            ledger={r['requirement_id']:r for r in plan['ledger']}
            response_rows,issues=runtime.project_responses(project,sections,requirements)
            with (folder/'内部响应与交付台账.csv').open('w',encoding='utf-8-sig',newline='') as f:
                writer=csv.writer(f);writer.writerow(['要求ID','归属章节','处理位置','采购要求','响应','原文位置','证据ID'])
                for row in response_rows:
                    item=ledger.get(row['id'],{})
                    writer.writerow([_csv_cell(v) for v in [row['id'],item.get('owner_section_key'),item.get('disposition'),row['text'],row.get('response'),row.get('locator'),' '.join(row.get('evidence_ids',[]))]])
            (folder/'内部来源与版本记录.json').write_text(json.dumps({'blueprint':plan,'response_issues':issues,
                'evidence':company.get('evidence',[]),'assets':company.get('assets',[]),'report':report},ensure_ascii=False,indent=2),encoding='utf-8')
            with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
                for path in folder.iterdir():archive.write(path,path.name)
    else:raise ValueError('不支持的导出格式')
    report['warnings']=list(dict.fromkeys(report['warnings']))
    report['warnings'].append('技术文件按采购目录生成；内部完整台账在整套ZIP中单独提供。Word索引页数已按本次正文与附件排版；后续改动需要重新排版。' if format!='md' else 'Markdown没有固定分页，页数请以本次Word排版结果为准。')
    if format!='md':index_pages.save_receipt(output,project,sections,report,revision)
    return report
