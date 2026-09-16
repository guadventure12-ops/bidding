import json
import hashlib
from pathlib import Path
import pytest
from PIL import Image
from test_review_rules import local
from app import db, proposal_assets as pa, proposal_runtime as pr
from app.document_assets import extract_document_assets


def setup_assets(tmp_path,monkeypatch):
    source=tmp_path/'source';source.mkdir();Image.new('RGB',(500,180),'white').save(source/'flow.png')
    md=source/'manual.md';md.write_text('# 智能检索\n![关键词查询](flow.png)',encoding='utf-8')
    assets=extract_document_assets(md,document_id='d',output_dir=db.DATA/'assets')['assets']
    asset=assets[0];asset['review_status']='usable_for_draft';asset['reviewed_title']='关键词查询界面'
    asset['reviewed_tags']=['智能检索','关键词'];asset['source']['candidate_neighbor_chunk_id']='c'
    db.insert('documents',{'id':'d','name':'已认可产品说明','path':str(md),'sha256':asset['source_sha256'],
      'source_type':'knowledge','status':'approved','scope':'archive','parse_status':'ready','created_at':db.now(),'updated_at':db.now()})
    raw=json.dumps({'assets':[asset]},ensure_ascii=False).encode();path=db.DATA/'assets/manifest.json';path.write_bytes(raw)
    p={'id':'p','domain':'archive','metadata':{'generation_profile':pr.PROFILE,
        'proposal_source_policy':{'fact_document_ids':['d']},
        'proposal_assets':{'manifest_path':str(path),'sha256':hashlib.sha256(raw).hexdigest()},
        'proposal_blueprint':{'sections':[{'section_id':'s','title':'智能检索','group_title':'功能设计','content_kind':'narrative'},
                                        {'section_id':'other','title':'项目进度','group_title':'实施','content_kind':'narrative'}]}}}
    monkeypatch.setattr(pr,'candidate_rows',lambda p,ids=None:[{'id':'c'}])
    return p,asset,path


def test_only_reviewed_source_version_is_loaded_and_image_has_one_owner(local,tmp_path,monkeypatch):
    p,asset,path=setup_assets(tmp_path,monkeypatch)
    assert len(pa.load_manifest(p))==1
    allocated=pa.allocate(p)
    assert list(allocated)==['s'] and allocated['s'][0]['asset_id']==asset['asset_id']
    db.update('documents','d',{'status':'pending'})
    assert pa.load_manifest(p)==[]


def test_manifest_change_cannot_change_the_selected_image_without_revision(local,tmp_path,monkeypatch):
    p,asset,path=setup_assets(tmp_path,monkeypatch);path.write_text('{"assets":[]}')
    with pytest.raises(ValueError,match='版本已变化'):pa.load_manifest(p)


def test_image_bytes_change_fails_before_embedding(local,tmp_path,monkeypatch):
    p,asset,path=setup_assets(tmp_path,monkeypatch);Path(asset['path']).write_bytes(b'changed')
    with pytest.raises(ValueError):pa.load_manifest(p)


def test_disallowed_originating_paragraph_excludes_the_image(local,tmp_path,monkeypatch):
    p,asset,path=setup_assets(tmp_path,monkeypatch)
    monkeypatch.setattr(pr,'candidate_rows',lambda p,ids=None:[])
    assert pa.load_manifest(p)==[]


def test_source_scope_and_hash_are_rechecked(local,tmp_path,monkeypatch):
    p,asset,path=setup_assets(tmp_path,monkeypatch);db.update('documents','d',{'scope':'expense'})
    assert pa.load_manifest(p)==[]
    db.update('documents','d',{'scope':'archive','sha256':'different'})
    assert pa.load_manifest(p)==[]


def test_tied_image_owner_follows_outline_not_random_project_ids(local,tmp_path,monkeypatch):
    p,asset,path=setup_assets(tmp_path,monkeypatch)
    first=p['metadata']['proposal_blueprint']['sections'][0]
    second={**first,'section_id':'zzzz'}
    p['metadata']['proposal_blueprint']['sections']=[first,second]
    assert list(pa.allocate(p))==['s']
