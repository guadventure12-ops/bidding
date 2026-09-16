"""Regressions found while exercising normal upload/reparse and knowledge flows."""
import asyncio
from datetime import date, timedelta
import threading

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app import db, tender_context, workflow
from app.main import app, save_upload
from test_backend import database, document, job, make_project, seed_generation_chapters


def test_reparse_retains_scoring_groups_and_table_source_context(database):
    p = make_project()
    source = database / '评分表.docx'
    doc = Document()
    table = doc.add_table(rows=0, cols=2)
    for text in ('技术部分（55分）', '系统应支持档案检索', '商务部分（10分）', '提供企业证明文件'):
        cells = table.add_row().cells
        if '部分' in text:
            cells[0].merge(cells[1])
        cells[0].text = text
    doc.save(source)
    imported = workflow.ingest(source, p)
    before = tender_context.build_context(p)['scoring_sections']
    assert [(x['name'], x['points']) for x in before] == [('技术部分', '55'), ('商务部分', '10')]
    jid, record = job(p, 'reparse')
    record['payload'] = {'document_id': imported['id']}
    workflow.run_reparse(jid, record)
    after = tender_context.build_context(p)['scoring_sections']
    assert [(x['name'], x['points']) for x in after] == [(x['name'], x['points']) for x in before]
    assert all(x['member_chunk_ids'] for x in after)
    chunks = db.all('SELECT * FROM chunks WHERE document_id=? ORDER BY ordinal', (imported['id'],))
    assert workflow._short_table_context(chunks[1]), 'reanalysis needs neighboring table header context'


def test_upload_reserves_project_before_reading_file(database, monkeypatch):
    p = make_project()
    monkeypatch.setattr(workflow.POOL, 'submit', lambda *args: None)

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        class Upload:
            filename = 'tender.txt'
            read_once = False

            async def read(self, count):
                if self.read_once:
                    return b''
                self.read_once = True
                started.set()
                await release.wait()
                return '系统必须支持档案检索。'.encode()

            async def close(self):
                pass

        task = asyncio.create_task(save_upload(Upload(), p))
        await asyncio.wait_for(started.wait(), 3)
        try:
            with pytest.raises(ValueError, match='保存|导入|修改'):
                workflow.create_job(p, 'analyze')
        finally:
            release.set()
            result = await task
        assert result['document']['parse_status'] == 'ready'
        # Once the upload is committed, a new job may be admitted normally.
        assert workflow.create_job(p, 'analyze')['status'] == 'queued'

    asyncio.run(scenario())


@pytest.mark.parametrize('first,second', [('knowledge', 'project'), ('project', 'knowledge')])
def test_knowledge_reparse_and_generation_cannot_overlap(database, monkeypatch, first, second):
    # An admitted writing task must already own a planned directory. Keep the
    # readiness guard active so this fixture reaches the intended mutual lock.
    p, _ = seed_generation_chapters(database, count=1, planned=True)
    doc = document(database, '企业产品资料。')
    monkeypatch.setattr(workflow.POOL, 'submit', lambda *args: None)
    actions = {
        'knowledge': lambda: workflow.create_job(None, 'reparse', {'document_id': doc['id']}),
        'project': lambda: workflow.create_job(p, 'generate'),
    }
    actions[first]()
    with pytest.raises(ValueError, match='企业|证据|资料'):
        actions[second]()


def test_expired_approved_documents_are_not_counted_as_usable(database):
    doc = document(database, '档案功能资料。')
    db.update('documents', doc['id'], {'status': 'approved', 'scope': 'archive', 'valid_until': (date.today() - timedelta(days=1)).isoformat()})
    with TestClient(app) as client:
        assert client.get('/api/dashboard').json()['stats']['approved'] == 0
        stats = client.get('/api/knowledge').json()['stats']
        assert stats['approved'] == 0 and stats['expired'] == 1


@pytest.mark.parametrize('value', ['20301231', '2030-W01-1'])
def test_valid_until_requires_unambiguous_calendar_date(database, value):
    doc = document(database, '有效期测试资料。')
    with TestClient(app) as client:
        assert client.patch('/api/knowledge/' + doc['id'], json={'valid_until': value}).status_code == 400
        assert client.get('/api/documents/' + doc['id']).json()['document']['valid_until'] == ''


def test_invalid_origin_is_rejected_without_server_error(database):
    with TestClient(app) as client:
        assert client.get('/api/health', headers={'origin': 'http://testserver:not-a-port'}).status_code == 403


def test_disconnected_upload_waits_for_parser_before_releasing_project(database, monkeypatch):
    p = make_project()
    monkeypatch.setattr(workflow.POOL, 'submit', lambda *args: None)
    started, release, completed = threading.Event(), threading.Event(), threading.Event()
    from app import documents
    original = documents.parse_document

    def slow_parse(*args, **kwargs):
        started.set()
        assert release.wait(5)
        try:
            return original(*args, **kwargs)
        finally:
            completed.set()

    monkeypatch.setattr(documents, 'parse_document', slow_parse)

    class Upload:
        filename = 'upload.txt'
        consumed = False

        async def read(self, count):
            if self.consumed:
                return b''
            self.consumed = True
            return '档案系统必须提供检索。'.encode()

        async def close(self):
            pass

    async def scenario():
        task = asyncio.create_task(save_upload(Upload(), p))
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(.01)
        assert started.is_set()
        task.cancel()
        await asyncio.sleep(.05)
        try:
            with pytest.raises(ValueError, match='保存|导入|修改'):
                workflow.create_job(p, 'analyze')
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.to_thread(completed.wait, 3)
        assert db.one('SELECT parse_status FROM documents WHERE project_id=?', (p,))['parse_status'] == 'ready'
        assert workflow.create_job(p, 'analyze')['status'] == 'queued'

    asyncio.run(scenario())
