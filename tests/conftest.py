"""Mock the native pagination boundary for ordinary offline application tests.

Native pagination validation is covered separately by test_proposal_pagination
and recorded real Word QA. This stub is test-only and never loaded by the app.
"""
from pathlib import Path
import re
import zipfile
import pytest


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    """Never inherit real workspace data, API keys or external network access."""
    import socket
    from app import db
    monkeypatch.setattr(db, 'DATA', tmp_path / 'runtime')
    monkeypatch.setenv('MX_DATA_DIR', str(tmp_path / 'runtime'))
    monkeypatch.setenv('MX_TESTING', '1')
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.delenv('MX_LANGFUSE_TEST_EXPORT', raising=False)
    original_connect = socket.socket.connect
    original_resolve = socket.getaddrinfo

    def check_host(host):
        if host not in ('127.0.0.1', '::1', 'localhost'):
            raise RuntimeError('External networking is disabled in offline tests')

    def connect(sock, address):
        check_host(address[0])
        return original_connect(sock, address)

    def resolve(host, *args, **kwargs):
        check_host(host)
        return original_resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, 'connect', connect)
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    db.init()


@pytest.fixture(autouse=True)
def mock_proposal_pagination(monkeypatch,request):
    if request.node.get_closest_marker('native_word'):return
    from app import proposal_export
    def fake(source,output,*,pdf_path=None):
        from lxml import etree as E
        source=Path(source);output=Path(output)
        root=Path(__file__).resolve().parents[1]
        for production in (root/'data',):
            assert not source.resolve().is_relative_to(production.resolve())
            assert not output.resolve().is_relative_to(production.resolve())
        assert not output.exists()
        with zipfile.ZipFile(source) as z:entries={i.filename:(i,z.read(i.filename)) for i in z.infolist()}
        xml=E.fromstring(entries['word/document.xml'][1]);W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        pages={n.get(W+'name')[4:]:2 for n in xml.iter(W+'bookmarkStart') if re.fullmatch('mxs_[a-f0-9]{32}',n.get(W+'name',''))}
        for field in xml.iter(W+'fldSimple'):
            if 'PAGEREF mxs_' in field.get(W+'instr',''):
                for text in field.iter(W+'t'):text.text='2'
        with zipfile.ZipFile(output,'w') as z:
            for name,(info,body) in entries.items():z.writestr(info,E.tostring(xml,xml_declaration=True,encoding='UTF-8',standalone=True) if name=='word/document.xml' else body)
        if pdf_path:
            import pymupdf
            pdf=pymupdf.open()
            for i in range(3):pdf.new_page().insert_text((72,72),'MOCK PAGINATION')
            pdf.save(pdf_path);pdf.close()
        return {'path':str(output),'pdf_path':str(pdf_path) if pdf_path else None,'section_pages':pages,'page_count':3,'page_numbers_refreshed':True,'engine':'mock'}
    monkeypatch.setattr(proposal_export,'_paginate',fake)
