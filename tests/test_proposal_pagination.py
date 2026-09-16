"""Offline native-pagination contracts; Word subprocess and model calls are absent."""
import base64
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest
from pypdf import PdfWriter

from app import documents, proposal_pagination as pagination


SID = 'a' * 32
SECOND = 'b' * 32
NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def write_docx(path, *, bookmarks=(SID,), fields=((SID, '____'),), complex_fields=False):
    items = []
    for index, sid in enumerate(bookmarks):
        items.append(f'<w:p><w:bookmarkStart w:id="{index}" w:name="mxs_{sid}"/>'
                     f'<w:r><w:t>章节{index}</w:t></w:r><w:bookmarkEnd w:id="{index}"/></w:p>')
    for sid, text in fields:
        if complex_fields:
            items.append('<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                         f'<w:r><w:instrText> PAGEREF mxs_{sid} \\h </w:instrText></w:r>'
                         '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
                         f'<w:r><w:t>{text}</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>')
        else:
            items.append(f'<w:p><w:fldSimple w:instr=" PAGEREF mxs_{sid} \\h ">'
                         f'<w:r><w:t>{text}</w:t></w:r></w:fldSimple></w:p>')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('word/document.xml', f'<w:document xmlns:w="{NS}"><w:body>{"".join(items)}</w:body></w:document>')


def write_pdf(path, page_count=2):
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=595, height=842)
    writer.write(path)


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    monkeypatch.setattr(documents.review_rules, 'active', lambda _: True)


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    source, output = tmp_path / 'source.docx', tmp_path / 'new.docx'
    write_docx(source)
    state = {'section_pages': {SID: 2}, 'page_count': 2, 'engine': 'word',
             'bookmarks': (SID,), 'fields': ((SID, '2'),), 'complex_fields': True, 'pdf_count': 2}
    calls = []
    def render(src, pdf, updated):
        calls.append((src, pdf, updated))
        write_docx(updated, bookmarks=state['bookmarks'], fields=state['fields'], complex_fields=state['complex_fields'])
        write_pdf(pdf, state['pdf_count'])
        return {'path': pdf, 'updated_docx_path': updated, 'warnings': [],
                **{key: copy.deepcopy(state[key]) for key in ('section_pages', 'page_count', 'engine') if key in state}}
    monkeypatch.setattr(documents, 'render_pdf', render)
    return source, output, state, calls


def test_verified_pages_and_updated_fields_published_without_changing_source(scenario):
    source, output, state, calls = scenario
    before = source.read_bytes()
    result = pagination.paginate_docx(source, output)
    assert result['page_numbers_refreshed'] is True
    assert result['section_pages'] == {SID: 2} and result['page_count'] == 2
    assert result['page_reference_fields'] == 1 and len(calls) == 1
    assert source.read_bytes() == before
    assert pagination._document_fields(output) == ({SID}, [(SID, '2')])
    assert Path(result['pdf_path']).is_file()


@pytest.mark.parametrize('change', [
    {'section_pages': {}}, {'section_pages': None}, {'section_pages': {SID: '2'}},
    {'section_pages': {SID: True}}, {'section_pages': {SID: 0}}, {'section_pages': {SID: 3}},
    {'section_pages': {SID: 2, SECOND: 1}}, {'page_count': None}, {'page_count': True},
    {'engine': None}, {'bookmarks': ()}, {'fields': ()},
    {'fields': ((SID, '____'),)}, {'fields': ((SID, '错误! 未定义书签。'),)},
    {'fields': ((SID, '1'),)}, {'fields': ((SECOND, '2'),)}, {'pdf_count': 1},
])
def test_failure_never_publishes_a_falsely_paginated_document(scenario, change):
    source, output, state, _ = scenario
    state.update(change)
    with pytest.raises(ValueError, match='分页失败'):
        pagination.paginate_docx(source, output)
    assert not output.exists() and not output.with_suffix('.pdf').exists()


@pytest.mark.parametrize('existing', ['docx', 'pdf'])
def test_existing_output_is_preserved_without_render(scenario, existing):
    source, output, _, calls = scenario
    path = output if existing == 'docx' else output.with_suffix('.pdf')
    path.write_bytes(b'old delivered file')
    with pytest.raises(ValueError, match='不能覆盖'):
        pagination.paginate_docx(source, output)
    assert path.read_bytes() == b'old delivered file' and not calls


def test_same_source_and_output_rejected_before_render(scenario):
    source, _, _, calls = scenario
    with pytest.raises(ValueError, match='不同路径'):
        pagination.paginate_docx(source, source)
    assert not calls


def test_missing_bookmark_rejected_before_render(scenario):
    source, output, _, calls = scenario
    write_docx(source, bookmarks=())
    with pytest.raises(ValueError, match='书签不存在'):
        pagination.paginate_docx(source, output)
    assert not calls


def test_duplicate_bookmark_rejected_before_render(scenario):
    source, output, _, calls = scenario
    write_docx(source, bookmarks=(SID, SID))
    with pytest.raises(ValueError, match='重复的章节书签'):
        pagination.paginate_docx(source, output)
    assert not calls


def test_document_without_sections_still_requires_actual_page_count(scenario):
    source, output, state, _ = scenario
    write_docx(source, bookmarks=(), fields=())
    state.update(bookmarks=(), fields=(), section_pages={})
    result = pagination.paginate_docx(source, output)
    assert result['section_pages'] == {} and result['page_count'] == 2


def test_volume_with_bookmark_and_no_index_fields_still_returns_actual_page(scenario):
    source, output, state, _ = scenario
    write_docx(source, fields=())
    state['fields'] = ()
    result = pagination.paginate_docx(source, output)
    assert result['section_pages'] == {SID: 2} and result['page_reference_fields'] == 0


def test_repeated_references_to_one_chapter_are_all_verified(scenario):
    source, output, state, _ = scenario
    write_docx(source, fields=((SID, '____'), (SID, '____')))
    state['fields'] = ((SID, '2'), (SID, '2'))
    result = pagination.paginate_docx(source, output)
    assert result['page_reference_fields'] == 2 and result['section_pages'] == {SID: 2}


def test_word_unavailable_is_an_error_not_placeholder_success(scenario, monkeypatch):
    source, output, _, _ = scenario
    monkeypatch.setattr(documents, 'render_pdf', lambda *args: {'path': None, 'warnings': ['Word 未安装']})
    with pytest.raises(ValueError, match='Word 未安装'):
        pagination.paginate_docx(source, output)
    assert not output.exists()


@pytest.mark.parametrize('record', [None, [], {}, {'engine': 'word', 'section_pages': {SID: True}, 'page_count': 2},
                                  {'engine': 'word', 'section_pages': {SID: 2}, 'page_count': 1}])
def test_render_rejects_invalid_pagination_record(tmp_path, monkeypatch, record):
    source, pdf = tmp_path / 'input.docx', tmp_path / 'output.pdf'
    source.write_bytes(b'original')
    powershell = tmp_path / 'Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
    powershell.parent.mkdir(parents=True)
    powershell.touch()
    monkeypatch.setenv('SystemRoot', str(tmp_path / 'Windows'))
    monkeypatch.setattr(documents.os, 'name', 'nt')
    monkeypatch.setattr(documents, 'Path', type(tmp_path))
    def run(command, **kwargs):
        Path(kwargs['env']['BIDDING_RENDER_OUTPUT']).write_bytes(b'%PDF-' + b'x' * 200)
        Path(kwargs['env']['BIDDING_RENDER_PAGINATION']).write_text(json.dumps(record), encoding='utf-8')
        return SimpleNamespace(returncode=0, stdout=b'OK', stderr=b'')
    monkeypatch.setattr(documents.subprocess, 'run', run)
    result = documents.render_pdf(str(source), str(pdf))
    assert result['path'] is None and not pdf.exists()
    assert result['error_type'] == 'invalid_pagination'


def test_native_script_records_actual_heading_start_pages_after_repagination(tmp_path, monkeypatch):
    source, pdf = tmp_path / 'input.docx', tmp_path / 'output.pdf'
    source.write_bytes(b'original')
    powershell = tmp_path / 'Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
    powershell.parent.mkdir(parents=True)
    powershell.touch()
    monkeypatch.setenv('SystemRoot', str(tmp_path / 'Windows'))
    monkeypatch.setattr(documents.os, 'name', 'nt')
    monkeypatch.setattr(documents, 'Path', type(tmp_path))
    def run(command, **kwargs):
        script = base64.b64decode(command[-1]).decode('utf-16le')
        assert '$bookmarkRange.Collapse(1)' in script
        assert '$bookmarkRange.Information(3)' in script
        assert script.index('$sectionBookmarkNames.Add') < script.index('$document.Fields.Update()')
        assert script.index('$document.Repaginate()') < script.index('$bookmarkRange.Information(3)')
        assert '$pass -lt 4' in script
        assert 'if (-not $paginationStable) { throw' in script
        assert 'Set-Content -LiteralPath $env:BIDDING_RENDER_PAGINATION' in script
        Path(kwargs['env']['BIDDING_RENDER_OUTPUT']).write_bytes(b'%PDF-' + b'x' * 200)
        Path(kwargs['env']['BIDDING_RENDER_PAGINATION']).write_text(json.dumps({
            'engine': 'word', 'section_pages': {SID: 2}, 'page_count': 2}), encoding='utf-8-sig')
        return SimpleNamespace(returncode=0, stdout=b'OK', stderr=b'')
    monkeypatch.setattr(documents.subprocess, 'run', run)
    result = documents.render_pdf(str(source), str(pdf))
    assert result['section_pages'] == {SID: 2} and result['page_count'] == 2
