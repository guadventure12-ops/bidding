"""Isolated directory recovery edges; no model or production data involved."""
import copy
import socket
import pytest
from app import db, chapter_outline as co, review_rules


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path)
    monkeypatch.setenv('MX_TESTING','1')
    monkeypatch.setenv('LANGFUSE_ENABLED','false')
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:(_ for _ in ()).throw(AssertionError('network prohibited')))
    db.init()
    for id in ('p','other'):
        db.insert('projects',{'id':id,'name':'隔离项目','created_at':db.now(),'updated_at':db.now()})
    for id,number,theme in [('a',1,'主体资格'),('b',2,'信用核查')]:
        db.insert('sections',{'id':id,'project_id':'p','ordinal':number-1,'title':f'资格与商务响应（{number}）',
            'content':f'## {theme}\n原有正文123。','status':'approved','created_at':db.now(),'updated_at':db.now()})


def test_partial_undo_then_migration_reuses_existing_parent_identity(isolated):
    first=co.migrate_project('p')
    db.update('sections','b',{'content':'后续用户编辑正文456。'})
    kept=db.one('SELECT * FROM sections WHERE id=?',('b',))
    assert {r['id']:r['status'] for r in co.restore_migration(first['snapshot_id'])}=={'a':'restored','b':'conflict'}
    old=db.one('SELECT * FROM sections WHERE id=?',('a',))
    result=co.migrate_project('p')
    new=db.one('SELECT * FROM sections WHERE id=?',('a',))
    assert result['changed']==1 and result['outline']['group_count']==1
    assert new['outline_group_id']==kept['outline_group_id']
    assert new['outline_group_title']=='资格与商务响应' and new['title']=='主体资格'
    assert all(old[k]==new[k] for k in old if k not in co.FIELDS)
    assert db.one('SELECT * FROM sections WHERE id=?',('b',))==kept
    assert co.migrate_project('p')['changed']==0


def test_partial_directory_uses_custom_parent_id_and_reserves_sibling_titles(isolated):
    db.update('sections','a',{'content':'## 信用核查\n原有正文123。'})
    db.update('sections','b',{'title':'信用核查','outline_group_id':'existing-custom-id',
        'outline_group_title':'资格与商务响应','legacy_title':'资格与商务响应（2）'})
    before=db.one('SELECT * FROM sections WHERE id=?',('b',))
    result=co.migrate_project('p')
    row=db.one('SELECT * FROM sections WHERE id=?',('a',))
    assert result['outline']['group_count']==1
    assert row['outline_group_id']=='existing-custom-id'
    assert row['outline_group_title']=='资格与商务响应'
    assert row['title']!='信用核查' and '（1）' not in row['title']
    assert row['content']=='## 信用核查\n原有正文123。'
    assert db.one('SELECT * FROM sections WHERE id=?',('b',))==before


@pytest.mark.parametrize('status',['queued','running'])
@pytest.mark.parametrize('mask_precheck',[False,True])
def test_directory_undo_blocks_active_jobs_even_with_all_rules_off(isolated,monkeypatch,status,mask_precheck):
    result=co.migrate_project('p')
    db.set_setting('review_rules',{rule:False for rule in review_rules.IDS})
    db.insert('jobs',{'id':'active','project_id':'other','mode':'generate','status':status,'created_at':db.now()})
    before={t:db.all('SELECT * FROM '+t+' ORDER BY id') for t in ('sections','project_snapshots','jobs')}
    if mask_precheck:
        # Simulate a job admitted after the optimistic read. The transaction
        # must still detect it; settings cannot disable this scope protection.
        original=db.one
        monkeypatch.setattr(db,'one',lambda sql,args=(): None if sql=="SELECT id FROM jobs WHERE status IN ('queued','running')" else original(sql,args))
    with pytest.raises(ValueError,match='有运行任务'):co.restore_migration(result['snapshot_id'])
    assert {t:db.all('SELECT * FROM '+t+' ORDER BY id') for t in before}==before
