"""Publish native Word pagination only after checking bookmarks and field results.

This module neither estimates page numbers nor changes the source document.
It has no model, application database, or project-approval dependencies.
"""
from __future__ import annotations

from collections import Counter
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from . import documents

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_BOOKMARK = re.compile(r'mxs_([0-9a-f]{32})', re.I)
_PAGEREF = re.compile(r'^\s*PAGEREF\s+"?(mxs_[0-9a-f]{32})"?(?=\s|$)', re.I)


def _document_fields(path: Path) -> tuple[set[str], list[tuple[str, str]]]:
    """Read original business references across both simple/complex field forms."""
    try:
        with zipfile.ZipFile(path) as archive:
            root = documents._safe_xml(archive.read('word/document.xml'))
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise ValueError('分页失败：DOCX 正文结构无法读取。') from exc
    if root.tag != W + 'document' or root.find(W + 'body') is None:
        raise ValueError('分页失败：DOCX 缺少有效正文结构。')
    names = []
    for node in root.iter(W + 'bookmarkStart'):
        name = node.get(W + 'name', '')
        match = _BOOKMARK.fullmatch(name)
        if match:
            names.append(match[1].lower())
    if len(names) != len(set(names)):
        raise ValueError('分页失败：存在重复的章节书签，无法确定实际页数。')

    fields = []
    def accept(code, text):
        match = _PAGEREF.match(code)
        if match:
            fields.append((match[1][4:].lower(), text.strip()))

    for node in root.iter(W + 'fldSimple'):
        accept(node.get(W + 'instr', ''), ''.join(item.text or '' for item in node.iter(W + 't')))
    stack = []
    for node in root.iter():
        if node.tag == W + 'fldChar':
            kind = node.get(W + 'fldCharType')
            if kind == 'begin':
                stack.append({'code': [], 'text': [], 'result': False})
            elif kind == 'separate' and stack:
                stack[-1]['result'] = True
            elif kind == 'end' and stack:
                item = stack.pop()
                accept(''.join(item['code']), ''.join(item['text']))
        elif node.tag == W + 'instrText' and stack and not stack[-1]['result']:
            stack[-1]['code'].append(node.text or '')
        elif node.tag == W + 't':
            for item in stack:
                if item['result']:
                    item['text'].append(node.text or '')
    if any(_PAGEREF.match(''.join(item['code'])) for item in stack):
        raise ValueError('分页失败：章节页码域结构不完整。')
    return set(names), fields


def _validate(source: Path, updated: Path, pdf: Path, result: dict) -> dict:
    expected, source_fields = _document_fields(source)
    actual, updated_fields = _document_fields(updated)
    if expected != actual:
        raise ValueError('分页失败：更新后的章节书签与原文件不一致。')
    pages = result.get('section_pages')
    count = result.get('page_count')
    if (result.get('engine') != 'word' or type(count) is not int or count < 1
            or not isinstance(pages, dict) or set(pages) != expected
            or any(type(value) is not int or value < 1 or value > count for value in pages.values())):
        raise ValueError('分页失败：缺少完整的 Word 实际章节页数记录。')
    if Counter(name for name, _ in source_fields) != Counter(name for name, _ in updated_fields):
        raise ValueError('分页失败：更新前后的评审索引页码域数量或目标不一致。')
    for name, text in updated_fields:
        if name not in pages or not re.fullmatch(r'[1-9][0-9]*', text) or int(text) != pages[name]:
            raise ValueError('分页失败：评审索引仍有未更新或不匹配的页数，未发布文件。')
    try:
        from pypdf import PdfReader
        with pdf.open('rb') as handle:
            pdf_count = len(PdfReader(handle).pages)
    except Exception as exc:
        raise ValueError('分页失败：Word 生成的 PDF 页数无法验证。') from exc
    if pdf_count != count:
        raise ValueError('分页失败：Word 章节页数记录与实际 PDF 页数不一致。')
    return {'section_pages': dict(pages), 'page_count': count, 'page_reference_fields': len(updated_fields)}


def _publish_new(source: Path, target: Path) -> None:
    """Exclusive creation protects any previously delivered output, even on races."""
    created = False
    try:
        with target.open('xb') as out:
            created = True
            with source.open('rb') as inp:
                shutil.copyfileobj(inp, out)
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise


def paginate_docx(source_path, output_path, *, pdf_path=None) -> dict:
    """Create a new DOCX/PDF and return {section_id: physical_page} from Word.

    The optional PDF destination defaults to the new DOCX's sibling .pdf.
    Any existing destination is rejected. No partial/stale DOCX is returned
    when Word, pagination records, field verification, or publication fails.
    """
    source = Path(source_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    pdf = Path(pdf_path).expanduser().resolve() if pdf_path else output.with_suffix('.pdf')
    if not source.is_file() or source.suffix.lower() != '.docx':
        raise ValueError('分页失败：请提供已经生成的 DOCX 文件。')
    if output.suffix.lower() != '.docx' or pdf.suffix.lower() != '.pdf':
        raise ValueError('分页失败：输出文件必须分别为新的 DOCX 和 PDF。')
    if source == output or source == pdf or output == pdf:
        raise ValueError('分页失败：输入、Word 输出和 PDF 输出必须为不同路径。')
    if output.exists() or pdf.exists():
        raise ValueError('分页失败：输出文件已存在，不能覆盖原件或旧导出。')
    # Validate structure before opening Word. Missing target bookmarks cannot
    # be repaired by a guessed page number or by a successful renderer status.
    bookmarks, fields = _document_fields(source)
    if any(name not in bookmarks for name, _ in fields):
        raise ValueError('分页失败：评审索引指向的章节书签不存在。')
    output.parent.mkdir(parents=True, exist_ok=True)
    pdf.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.pagination-', dir=output.parent) as directory:
        work = Path(directory)
        updated, rendered = work / 'updated.docx', work / 'document.pdf'
        result = documents.render_pdf(str(source), str(rendered), str(updated))
        if (not result.get('path') or not result.get('updated_docx_path')
                or not updated.is_file() or not rendered.is_file()):
            reason = '；'.join(str(item) for item in result.get('warnings', []))
            raise ValueError('分页失败：' + (reason or 'Word 未生成可验证的分页文件。'))
        verified = _validate(source, updated, rendered, result)
        _publish_new(rendered, pdf)
        try:
            _publish_new(updated, output)
        except BaseException:
            pdf.unlink(missing_ok=True)
            raise
    return {'path': str(output), 'pdf_path': str(pdf), 'engine': 'word',
            'page_numbers_refreshed': True, 'warnings': list(result.get('warnings', [])), **verified}
