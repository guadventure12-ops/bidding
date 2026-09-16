"""Persisted chapter identity shared by editing, generation and all exports."""
from __future__ import annotations

from collections import defaultdict
import copy
import hashlib
import json
import re
import sqlite3
from . import db, export_outline

VERSION = 'chapter-outline-1'
FIELDS = ('title','outline_group_id','outline_group_title','legacy_title')
MIGRATION_KIND = 'chapter_outline_migration'


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def group_id(project_id, title):
    return 'group-'+digest([project_id,title])[:20]


def snapshot_matches(current, expected):
    if current is None:return False
    if 'outline_group_id' in expected:return current==expected
    projected={key:current.get(key) for key in expected}
    if current.get('outline_group_id') and expected.get('title')==current.get('legacy_title'):
        projected['title']=expected['title']
    return projected==expected


def preserve_directory_on_restore(current, values):
    """Old snapshots own content, not a directory created after that snapshot."""
    if current and current.get('outline_group_id') and 'outline_group_id' not in values:
        return {**values,**{key:current.get(key,'') for key in FIELDS}}
    return values


def initial_fields(project_id, sections, requirements=()):
    """One-time compatibility adapter; persisted titles never follow model text."""
    from .documents import clean_export_locators
    prepared=[];originals={row['id']:row for row in sections}
    known_groups={};used_titles=defaultdict(set)
    for row in sections:
        if row.get('outline_group_id'):
            gid=row['outline_group_id'];parent=row['outline_group_title']
            if parent in known_groups and known_groups[parent]!=gid:
                raise ValueError('同名一级分类存在多个目录ID，请先核对目录关联')
            known_groups[parent]=gid
            used_titles[gid].add(row['title'])
        ids=[r['id'] for r in requirements]
        prepared.append({**row,'title':clean_export_locators(row.get('title',''),ids)['content'],
                         'content':clean_export_locators(row.get('content',''),ids)['content']})
    result={}
    # A partially undone migration can leave one old numbered title next to
    # persisted siblings. Their parent still defines the family, including its
    # identity and already reserved child titles; do not create a numbered H1.
    for group in export_outline.outline_sections(prepared,family_names=known_groups):
        title=group['title'];gid=known_groups.get(title,group_id(project_id,title));used=used_titles[gid]
        for part in group['parts']:
            row=part['section']
            if row.get('outline_group_id'):
                result[row['id']]={key:originals[row['id']].get(key,'') for key in FIELDS}
                continue
            topic=part.get('title') or export_outline._part_title(export_outline._themes(row,title),used)
            if topic in used:topic=export_outline._part_title(export_outline._themes(row,title),used)
            used.add(topic)
            result[row['id']]={'title':topic,'outline_group_id':gid,'outline_group_title':title,
                               'legacy_title':originals[row['id']]['title']}
    return result


def view(project_id, sections=None):
    rows=sections if sections is not None else db.all('SELECT * FROM sections WHERE project_id=? ORDER BY ordinal,id',(project_id,))
    from . import compilation_outline
    project=db.one('SELECT * FROM projects WHERE id=?',(project_id,)) or {'id':project_id,'metadata':{}}
    rows=compilation_outline.projection(project,rows)['active']
    saved_specs={s['section_id']:s for s in project.get('metadata',{}).get('proposal_blueprint',{}).get('sections',[]) if s.get('section_id')}
    saved_specs.update(project.get('metadata',{}).get(compilation_outline.KEY,{}).get('specifications',{}))
    groups=[];mapping={}
    for row in rows:
        gid=row.get('outline_group_id') or group_id(project_id,row['title'])
        title=row.get('outline_group_title') or row['title']
        if gid not in mapping:
            mapping[gid]={'id':gid,'title':title,'sections':[]};groups.append(mapping[gid])
        spec=row.get('_proposal_spec') or saved_specs.get(row['id'],{})
        mapping[gid]['sections'].append({**{k:row.get(k) for k in ('id','title','ordinal','status')},
                                         'children':effective_suboutline(row,spec),
                                         'directory_format':'procurement_form' if fixed_format(spec) else spec.get('directory_format','')})
    return {'version':VERSION,'groups':groups,'section_count':len(rows),'group_count':len(groups),
            'persisted':all(row.get('outline_group_id') for row in rows)}


def _heading_title(value):
    # Same display-number removal as the Word heading formatter. Do not strip
    # numbers from body prose, product versions, or module names.
    value=str(value).strip().replace('**','')
    value=re.sub(r'\s+#+\s*$','',value)
    return re.sub(r'^(?:第[一二三四五六七八九十百\d]+[章节篇]\s+|[一二三四五六七八九十百]+[、．]\s*|\d+(?:\.\d+)*(?:[.．)）]\s+|、\s*|\s+))(?=\S)', '',value).strip()


def structure_body(section, content=None):
    """Read-only export/outline adapter for exact leading self-label wrappers."""
    text=str(section.get('content') or '') if content is None else str(content or '')
    aliases={str(section.get(key) or '').strip() for key in ('title','legacy_title','outline_group_title','group_title')} - {''}
    original=str(section.get('legacy_title') or section.get('title') or '').strip()
    title=str(section.get('title') or '').strip()
    lines=text.splitlines(keepends=True)
    while lines:
        first=next((i for i,line in enumerate(lines) if line.strip()),None)
        if first is None:break
        label=export_outline._plain_heading(lines[first])
        promoted=False
        if label and original and label.startswith(original):
            rest=re.sub(r'^\s*(?:——|—|--|：|:|[－-])\s*','',label[len(original):])
            promoted=bool(rest and rest==title)
        if label and (label in aliases or promoted):del lines[first]
        else:break
    return ''.join(lines)


def body_headings(section, content=None):
    """Read visible Markdown headings using Word's relative nesting stack.

    A leaf owns H2; its first business heading is H3 regardless of hash count.
    Fenced code is not structure. Only an exact leading self-label is skipped.
    """
    text=structure_body(section,content)
    stack=[];result=[];fence=None
    for raw in text.splitlines():
        line=raw.strip()
        if not line:continue
        marker=re.match(r'^(`{3,}|~{3,})(.*)$',line)
        if marker:
            if fence is None:fence=(marker[1][0],len(marker[1]))
            elif marker[1][0]==fence[0] and len(marker[1])>=fence[1] and not marker[2].strip():fence=None
            continue
        if fence is not None:continue
        match=re.match(r'^(#{1,6})\s+(.+)$',line)
        if not match:continue
        depth=len(match[1])
        while stack and stack[-1]>=depth:stack.pop()
        stack.append(depth)
        result.append({'level':2+len(stack),'title':_heading_title(match[2])})
    return result


def effective_suboutline(section, spec=None):
    """Project the saved body or empty-leaf plan; never rewrite business data."""
    spec=spec or {}
    if fixed_format(spec):return []
    content=str(section.get('content') or '')
    if not content.strip():return copy.deepcopy(spec.get('suboutline') or [])
    headings=body_headings(section)
    original={}
    for h3 in spec.get('suboutline') or []:
        original[(3,_heading_title(h3.get('title','')),'')]=h3.get('id')
        for h4 in h3.get('children') or []:
            original[(4,_heading_title(h4.get('title','')),_heading_title(h3.get('title','')))]=h4.get('id')
    result=[];parent=None;counts=defaultdict(int)
    for item in headings:
        if item['level'] not in (3,4):continue
        title=item['title'];parent_title=parent['title'] if parent and item['level']==4 else ''
        key=(item['level'],title,parent_title);counts[key]+=1
        identity=original.get(key) if counts[key]==1 else None
        identity=identity or 'topic-'+digest([section.get('id'),*key,counts[key]])[:20]
        node={'id':identity,'title':title}
        if item['level']==3:
            node['children']=[];result.append(node);parent=node
        elif parent is not None:parent['children'].append(node)
    return result


def fixed_format(spec):
    if not isinstance(spec,dict):return False
    if spec.get('directory_format')=='procurement_form' or spec.get('content_kind')=='attachment':return True
    if spec.get('content_kind')=='form':
        from . import index_materials
        return not index_materials.free_form(spec)
    return False


def validate_body_structure(spec, content):
    """Enforce the same ordered H3/H4 plan used by editor and subsequent AI."""
    from . import provider
    if not isinstance(spec,dict) or fixed_format(spec):return
    planned=spec.get('suboutline') or []
    if spec.get('secondary_defined') and any(h['level']>4 for h in body_headings(spec,content)):
        raise provider.ProviderError('本次目录最多到四级，不能在已确认二级主题下再产生五级目录')
    if not planned:return  # Existing unplanned content has no invented contract.
    expected=[]
    for h3 in planned:
        expected.append({'level':3,'title':_heading_title(h3['title'])})
        expected.extend({'level':4,'title':_heading_title(h4['title'])} for h4 in h3.get('children',[]))
    actual=body_headings(spec,content)
    if any(h['level']>4 for h in actual):raise provider.ProviderError('正文产生超出四级的目录，请按已确认的三级四级主题编写')
    if actual!=expected:
        raise provider.ProviderError('正文标题或顺序与已确认的三级四级目录不一致，不能增加、遗漏或重排目录主题')


def validate_title(section, title):
    title=title.strip()
    if not title:raise ValueError('二级主题不能为空')
    if '\n' in title or '\r' in title:raise ValueError('二级主题应为一行文字')
    if re.search(r'\[E:|采购条款定位|要求\s*ID\s*[:：]|对应要求\s+[0-9a-f]{8}',title,re.I):
        raise ValueError('请填写主题名称，不要把内部定位编号写入目录')
    if re.search(r'[（(][1-9]\d{0,2}[）)]$',title):
        raise ValueError('请使用具体主题名称，不使用旧的数字括号分段标题')
    if section.get('outline_group_id') and db.one('SELECT id FROM sections WHERE project_id=? AND outline_group_id=? AND title=? AND id<>?',
            (section['project_id'],section['outline_group_id'],title,section['id'])):
        raise ValueError('本分类已有同名主题，请使用不同的二级标题')
    return title


def migrate_project(project_id):
    """Explicit, backed-up directory migration; never changes content/status/history."""
    from . import workflow
    if db.one("SELECT id FROM jobs WHERE status IN ('queued','running')"):
        raise ValueError('有运行任务，暂不迁移目录')
    with workflow.editing(project_id,whole_workspace=True):
        rows=db.all('SELECT * FROM sections WHERE project_id=? ORDER BY ordinal,id',(project_id,))
        changes=initial_fields(project_id,rows,db.all('SELECT id FROM requirements WHERE project_id=?',(project_id,)))
        selected=[row for row in rows if any(row.get(k,'')!=changes[row['id']][k] for k in FIELDS)]
        if not selected:return {'changed':0,'snapshot_id':None,'outline':view(project_id,rows)}
        folder=db.DATA/'outline-backups';folder.mkdir(parents=True,exist_ok=True)
        backup=folder/(db.uid()+'.sqlite3')
        with sqlite3.connect((db.DATA/'bidding.sqlite3').as_uri()+'?mode=ro',uri=True) as source,sqlite3.connect(backup) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('目录备份校验失败')
        snapshot_id=db.uid();after=[{**row,**changes[row['id']]} for row in selected]
        payload={'kind':MIGRATION_KIND,'version':VERSION,'backup':str(backup),'before':selected,'after':after,'restored_ids':[]}
        with db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            current=[db.decode(r) for r in conn.execute('SELECT * FROM sections WHERE project_id=? ORDER BY ordinal,id',(project_id,))]
            if current!=rows or conn.execute("SELECT id FROM jobs WHERE status IN ('queued','running')").fetchone():
                raise ValueError('目录或任务状态已变化，迁移取消')
            for row in after:
                conn.execute('UPDATE sections SET title=?,outline_group_id=?,outline_group_title=?,legacy_title=? WHERE id=?',
                             (*[row[k] for k in FIELDS],row['id']))
            conn.execute('INSERT INTO project_snapshots(id,project_id,label,payload,created_at) VALUES(?,?,?,?,?)',
                         (snapshot_id,project_id,'一级分类—二级主题目录同步',json.dumps(payload,ensure_ascii=False),db.now()))
        return {'changed':len(after),'snapshot_id':snapshot_id,'backup':str(backup),'outline':view(project_id)}


def restore_migration(snapshot_id):
    """Restore only untouched directory fields, not the old whole database."""
    from . import workflow
    snapshot=db.one('SELECT * FROM project_snapshots WHERE id=?',(snapshot_id,))
    if not snapshot or snapshot['payload'].get('kind')!=MIGRATION_KIND:raise ValueError('不是目录迁移记录')
    if db.one("SELECT id FROM jobs WHERE status IN ('queued','running')"):
        raise ValueError('有运行任务，暂不撤销目录同步')
    result=[]
    with workflow.editing(snapshot['project_id'],whole_workspace=True),db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute("SELECT id FROM jobs WHERE status IN ('queued','running')").fetchone():
            raise ValueError('有运行任务，暂不撤销目录同步')
        payload=snapshot['payload'];restored=set(payload.get('restored_ids',[]))
        before={row['id']:row for row in payload['before']}
        for expected in payload['after']:
            id=expected['id'];current=db.decode(conn.execute('SELECT * FROM sections WHERE id=?',(id,)).fetchone())
            if id in restored:result.append({'id':id,'status':'already_restored'});continue
            if current!=expected:result.append({'id':id,'status':'conflict'});continue
            conn.execute('UPDATE sections SET title=?,outline_group_id=?,outline_group_title=?,legacy_title=? WHERE id=?',
                         (*[before[id].get(k,'') for k in FIELDS],id))
            restored.add(id);result.append({'id':id,'status':'restored'})
        payload['restored_ids']=sorted(restored)
        conn.execute('UPDATE project_snapshots SET payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),snapshot_id))
    return result


def generation_plan(project_id, requirements, category_labels):
    """Logical units are independent of request/token batch size."""
    from . import proposal_runtime, compilation_outline
    project=db.one('SELECT * FROM projects WHERE id=?',(project_id,))
    selection=project.get('metadata',{}).get(compilation_outline.KEY) if project else None
    if selection and selection.get('confirmed'):
        compilation_outline.ensure_generation_ready(project_id)
        rows=db.all('SELECT * FROM sections WHERE project_id=? ORDER BY ordinal,id',(project_id,))
        proposal=proposal_runtime.decorate_units(project,rows,requirements)
        if proposal is not None:return proposal
        by_id={r['id']:r for r in requirements}
        selected=compilation_outline.projection(project,rows)['active']
        plan=[]
        for row in selected:
            if any(rid not in by_id for rid in row['requirement_ids']):raise ValueError('已选章节的要求范围失效，请先核对来源')
            plan.append({**proposal_runtime.decorate_section(project,row),'requirements':[by_id[rid] for rid in row['requirement_ids']]})
        return plan
    proposal=proposal_runtime.prepare_plan(project_id,requirements)
    if proposal is not None:return proposal
    rows=db.all('SELECT * FROM sections WHERE project_id=? ORDER BY ordinal,id',(project_id,))
    if any(not row.get('outline_group_id') for row in rows):
        raise ValueError('项目旧目录尚未同步，请先完成目录同步；未调用模型')
    fields=initial_fields(project_id,rows,requirements)
    by_id={r['id']:r for r in requirements};covered=set();plan=[]
    for row in rows:
        reqs=[by_id[id] for id in row['requirement_ids'] if id in by_id]
        covered.update(r['id'] for r in reqs)
        plan.append({**row,**fields[row['id']],'requirements':reqs})
    missing=defaultdict(list)
    for req in requirements:
        if req['id'] not in covered:missing[req['category']].append(req)
    ordinal=max([row['ordinal'] for row in rows]+[-1])+1
    for category in [*category_labels,*sorted(set(missing)-set(category_labels))]:
        reqs=missing.get(category,[])
        if not reqs:continue
        parent=category_labels.get(category,category);gid=group_id(project_id,parent)
        has_parent=any(unit['outline_group_id']==gid for unit in plan)
        identity=[project_id,'logical-category',category]
        if has_parent:identity.extend(['additional-scope',sorted(r['id'] for r in reqs)])
        logical_id=digest(identity)[:32]
        existing=next((unit for unit in plan if unit['id']==logical_id),None)
        if existing:
            existing['requirements'].extend(reqs);continue
        used={unit['title'] for unit in plan if unit['outline_group_id']==gid}
        themes=[export_outline._topic(str(r.get('title') or ''),parent) for r in reqs]
        topic=export_outline._part_title(list(dict.fromkeys(x for x in themes if x)),used)
        plan.append({'id':logical_id,'project_id':project_id,'ordinal':ordinal,'title':topic,
                     'outline_group_id':gid,'outline_group_title':parent,'legacy_title':'','requirements':reqs})
        ordinal+=1
    # Establish logical leaves before dispatching any model request. Empty
    # planned drafts are visible and retryable; no batch creates a new row.
    from . import workflow
    original_ids={row['id'] for row in rows}
    with workflow.JOB_LOCK,db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for index,unit in enumerate(plan):
            if unit['id'] in original_ids:continue
            now=db.now()
            conn.execute('INSERT OR IGNORE INTO sections(id,project_id,ordinal,title,outline_group_id,outline_group_title,legacy_title,requirement_ids,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (unit['id'],project_id,unit['ordinal'],unit['title'],unit['outline_group_id'],unit['outline_group_title'],'',json.dumps([r['id'] for r in unit['requirements']]),now,now))
            stored=db.decode(conn.execute('SELECT * FROM sections WHERE id=?',(unit['id'],)).fetchone())
            if stored['project_id']!=project_id:raise ValueError('逻辑章节ID不属于本项目')
            plan[index]={**stored,'requirements':unit['requirements']}
    return plan
