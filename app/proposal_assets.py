"""Use an explicit, versioned draft illustration selection; never infer approval."""
import hashlib
import json
from pathlib import Path
from . import db, evidence_retrieval, compilation_outline


def load_manifest(project):
    selection=(project.get('metadata') or {}).get('proposal_assets') or {}
    if not selection:return []
    root=(db.DATA/'assets').resolve()
    path=Path(selection.get('manifest_path','')).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size>8*1024*1024:
        raise ValueError('项目图片清单不在本地资产目录或文件不可读取')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=selection.get('sha256'):
        raise ValueError('项目图片清单版本已变化，请重新核对选用范围')
    manifest=json.loads(raw);assets=manifest.get('assets')
    if not isinstance(assets,list) or len(assets)>1500:raise ValueError('项目图片清单格式无效')
    policy=(project.get('metadata') or {}).get('proposal_source_policy') or {}
    facts=set(policy.get('fact_document_ids') or [])-set(policy.get('reference_document_ids') or [])
    ids=list({a.get('document_id') for a in assets if isinstance(a,dict) and a.get('document_id') in facts})
    if not ids:return []
    documents={d['id']:d for d in db.all('SELECT * FROM documents WHERE id IN ('+','.join('?' for _ in ids)+')',ids)}
    from . import document_assets, proposal_runtime
    associations=[a.get('source',{}).get('candidate_neighbor_chunk_id') for a in assets if isinstance(a,dict)]
    allowed_associations={r['id'] for r in proposal_runtime.candidate_rows(project,list(dict.fromkeys(a for a in associations if a)))}
    valid=[];seen=set()
    for asset in assets:
        if not isinstance(asset,dict) or asset.get('review_status')!='usable_for_draft':continue
        d=documents.get(asset.get('document_id'))
        if not d or d['source_type']!='knowledge' or d['status']!='approved' or d['parse_status']!='ready':continue
        if d['scope'] not in ('general',project['domain']):continue
        from datetime import date
        if d['valid_until'] and d['valid_until']<date.today().isoformat():continue
        if asset.get('source_sha256')!=d['sha256'] or asset.get('source',{}).get('display_transform'):continue
        # At least one allowed source passage associates the image's originating
        # context. This is provenance, not a claim that every depicted value is true.
        associated=asset.get('source',{}).get('candidate_neighbor_chunk_id')
        if associated not in allowed_associations:continue
        document_assets.verified_asset_path(asset,asset_root=root)
        if asset['asset_id'] in seen:raise ValueError('图片清单包含重复资产ID')
        seen.add(asset['asset_id']);valid.append({**asset,'document_name':d['name'],
          'title':asset.get('reviewed_title') or asset.get('title') or '产品界面示例',
          'locator':asset.get('source',{}).get('candidate_neighbor_locator') or asset.get('source',{}).get('heading','')})
    return valid


def allocate(project):
    assets=load_manifest(project)
    plan=(project.get('metadata') or {}).get('proposal_blueprint') or {}
    sections=compilation_outline.projection(project,[{**s,'id':s['section_id']} for s in plan.get('sections',[])])['active']
    sections=[s for s in sections if s.get('content_kind')=='narrative']
    def query(spec):
        return ' '.join([spec['title'],spec['group_title'],*spec.get('suggested_subtopics',[]),
          *[r.get('module','')+' '+r.get('function','') for r in spec.get('feature_rows',[])]])
    queries={s['section_id']:(query(s),set(evidence_retrieval.tokens(query(s)))) for s in sections}
    # Equal relevance follows the shared outline order, not a project-specific ID.
    order={s['section_id']:i for i,s in enumerate(sections)}
    pixels={};selected={}
    for asset in assets:
        tags=asset.get('reviewed_tags') or []
        words=set(evidence_retrieval.tokens(' '.join(tags)+' '+asset['title']))
        scores=[]
        for sid,(text,tokens) in queries.items():
            score=len(words & tokens)+sum(8 for tag in tags if tag and tag in text)
            scores.append((score,sid))
        if not scores:continue
        score,owner=max(scores,key=lambda item:(item[0],-order[item[1]]))
        if score<3:continue
        previous=pixels.get(asset['sha256'])
        if previous is None or (score,asset['asset_id'])>(previous[0],previous[2]['asset_id']):pixels[asset['sha256']]=(score,owner,asset)
    for score,owner,asset in pixels.values():selected.setdefault(owner,[]).append((score,asset))
    return {sid:[a for _,a in sorted(rows,key=lambda x:(-x[0],x[1]['asset_id']))[:10]] for sid,rows in selected.items()}


def for_section(project, unit):
    if not compilation_outline.is_enabled(project,unit):return []
    allocation=project.get('_proposal_asset_allocation')
    if allocation is None:allocation=allocate(project)
    return allocation.get(unit['id'],[])
