"""Read-only enterprise attachments; never execute formulas, HTML, or links."""
from __future__ import annotations

import csv
import io
import posixpath
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from . import review_rules

S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _decode(source, result):
    from .documents import _warn
    data = source.read_bytes()
    if review_rules.active('file_parse_safety') and len(data) > 32 * 1024 * 1024:
        raise ValueError("文本/表格附件超过32 MB，请拆分后导入。")
    choices = ["utf-8-sig", "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "gb18030"]
    for encoding in choices:
        try:
            text = data.decode(encoding)
            result["encoding"] = encoding
            return text.replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeError:
            pass
    _warn(result, "文本编码无法完整识别，部分字符已替换，请核对原件。")
    return data.decode("utf-8", errors="replace")


def _relationships(zf, part):
    from .documents import _safe_xml
    directory, filename = posixpath.split(part)
    rel = posixpath.join(directory, "_rels", filename + ".rels")
    if rel not in zf.namelist():
        return {}
    links = {}
    for node in _safe_xml(zf.read(rel)):
        if node.get("TargetMode") == "External":
            continue
        target = node.get("Target", "")
        target = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join(directory, target))
        if target.startswith("../") or target not in zf.namelist():
            continue
        links[node.get("Id")] = (target, node.get("Type", ""))
    return links


def _xlsx(zf, result):
    from .documents import _add_block, _warn, _safe_xml
    shared = []
    if "xl/sharedStrings.xml" in zf.namelist():
        for item in _safe_xml(zf.read("xl/sharedStrings.xml")).findall(S + "si"):
            shared.append("".join(t.text or "" for t in item.iter(S + "t")))
    book = _safe_xml(zf.read("xl/workbook.xml"))
    links = _relationships(zf, "xl/workbook.xml")
    sheets = book.findall("./" + S + "sheets/" + S + "sheet")
    if review_rules.active('file_parse_safety') and len(sheets) > 1000:
        raise ValueError("工作表数量超过1000，请拆分后导入。")
    result["sheet_count"] = len(sheets)
    formulas = numeric = merged_count = cells_seen = 0
    for sheet in sheets:
        name = sheet.get("name", "未命名工作表")
        link = links.get(sheet.get(R + "id"))
        if not link:
            _warn(result, f"工作表{name}未找到内容，可能为不支持的图表表。")
            continue
        xml = _safe_xml(zf.read(link[0]))
        merged = [m.get("ref", "") for m in xml.findall("./" + S + "mergeCells/" + S + "mergeCell")]
        merged_count += len(merged)
        if sheet.get("state") in {"hidden", "veryHidden"}:
            _warn(result, f"工作表{name}处于隐藏状态，其内容已单独标识并提取，请确认适用性。")
        for row in xml.findall("./" + S + "sheetData/" + S + "row"):
            values = []
            for cell in row.findall(S + "c"):
                cells_seen += 1
                if review_rules.active('file_parse_safety') and cells_seen > 300000:
                    raise ValueError("工作簿超过30万个单元格，请拆分后导入。")
                ref, typ = cell.get("r", "?"), cell.get("t", "n")
                text = cell.findtext(S + "v", default="")
                if typ == "s":
                    if not text.isdigit() or int(text) >= len(shared):
                        raise ValueError("工作簿共享文本索引损坏，请检查原件。")
                    text = shared[int(text)]
                elif typ == "inlineStr":
                    text = "".join(t.text or "" for t in cell.iter(S + "t"))
                elif typ == "b":
                    text = "TRUE" if text == "1" else "FALSE"
                elif typ == "n" and text:
                    numeric += 1
                formula = cell.find(S + "f")
                if formula is not None:
                    formulas += 1
                    text = (text + "【公式缓存，未重算】") if text else "【公式缺少缓存值，需在Excel重算保存】"
                if text.strip():
                    merge = next((m for m in merged if m.split(":")[0] == ref), "")
                    values.append(f"{ref}{'（合并'+merge+'）' if merge else ''}：{text.strip()}")
            if values:
                hidden = "（隐藏）" if row.get("hidden") == "1" or sheet.get("state") in {"hidden", "veryHidden"} else ""
                _add_block(result, f"工作表：{name}{hidden}\n" + " | ".join(values), f"工作表 {name}{hidden} / 第 {row.get('r','?')} 行", "table_row", table={"cells": values})
            if result.get("truncated"):
                return
    if formulas:
        _warn(result, f"有{formulas}个公式单元格，仅提取文件保存时的缓存值，不执行公式；请在Excel重算并核对。")
    if numeric:
        _warn(result, "数值保留工作簿存储值，日期序列、百分比及自定义显示格式未转换，请对照Excel原件核对。")
    if merged_count:
        _warn(result, f"有{merged_count}处合并单元格，内容保留在左上角并标注范围，未自动向下填充。")
    images = sum(n.startswith("xl/media/") and not n.endswith("/") for n in zf.namelist())
    result["image_count"] = images
    if images:
        _warn(result, f"工作簿包含{images}张嵌入图片，未识别图中文字。")
    if any(n.startswith(("xl/charts/", "xl/externalLinks/")) for n in zf.namelist()):
        _warn(result, "存在图表或外部数据链接，未执行链接、未提取图表，需检查原件。")


def _pptx(zf, result):
    from .documents import _add_block, _warn, _safe_xml
    presentation = _safe_xml(zf.read("ppt/presentation.xml"))
    links = _relationships(zf, "ppt/presentation.xml")
    slides = presentation.findall("./" + P + "sldIdLst/" + P + "sldId")
    if review_rules.active('file_parse_safety') and len(slides) > 3000:
        raise ValueError("幻灯片超过3000页，请拆分后导入。")
    result["page_count"] = len(slides)
    result["page_count_source"] = "slide_order"
    for page, slide in enumerate(slides, 1):
        link = links.get(slide.get(R + "id"))
        if not link:
            raise ValueError(f"第{page}张幻灯片内容关系丢失。")
        xml = _safe_xml(zf.read(link[0]))
        texts = ["".join(t.text or "" for t in para.iter(A + "t")) for para in xml.iter(A + "p")]
        _add_block(result, "\n".join(filter(None, texts)), f"幻灯片第 {page} 页 / 正文", "slide", page=page)
        for target, typ in _relationships(zf, link[0]).values():
            if typ.endswith("/notesSlide"):
                notes = _safe_xml(zf.read(target))
                texts = []
                for shape in notes.iter(P + "sp"):
                    placeholder = shape.find("./" + P + "nvSpPr/" + P + "nvPr/" + P + "ph")
                    if placeholder is not None and placeholder.get("type") in {"sldNum", "dt", "hdr", "ftr"}:
                        continue
                    texts.extend("".join(t.text or "" for t in para.iter(A + "t")) for para in shape.iter(A + "p"))
                _add_block(result, "\n".join(filter(None, texts)), f"幻灯片第 {page} 页 / 演讲备注", "notes", page=page)
        if result.get("truncated"):
            return
    result["image_count"] = sum(n.startswith("ppt/media/") and not n.endswith("/") for n in zf.namelist())
    _warn(result, "PPTX仅提取幻灯片及备注的可编辑文字，文本顺序不等于视觉阅读顺序；图片、图表和图示布局需对照原件。")


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "template"}:
            self.skip += 1
        elif not self.skip and tag in {"p", "div", "tr", "br", "li", "h1", "h2", "h3", "section"}:
            self.parts.append("\n")
        elif not self.skip and tag in {"td", "th"}:
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "template"} and self.skip:
            self.skip -= 1
        elif not self.skip and tag in {"p", "div", "tr", "li", "h1", "h2", "h3", "section"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def parse_extra(source: Path, result: dict, ocr: bool = False) -> None:
    from .documents import _add_block, _warn, _check_zip
    ext = source.suffix.lower()
    if ext in {".xlsx", ".pptx"}:
        try:
            with zipfile.ZipFile(source) as zf:
                _check_zip(zf)
                (_xlsx if ext == ".xlsx" else _pptx)(zf, result)
        except zipfile.BadZipFile:
            raise ValueError("附件不是有效的Office开放格式，可能已加密；请提供可读取的副本。")
    elif ext == ".csv":
        text = _decode(source, result)
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        for index, row in enumerate(csv.reader(io.StringIO(text), dialect), 1):
            if review_rules.active('file_parse_safety') and index > 300000:
                raise ValueError("CSV超过30万行，请拆分后导入。")
            _add_block(result, " | ".join(f"列{i}：{value}" for i, value in enumerate(row, 1)), f"CSV第 {index} 行", "table_row", table={"cells": row})
            if result.get("truncated"):
                break
    elif ext in {".html", ".htm"}:
        parser = _HTMLText()
        parser.feed(_decode(source, result))
        for index, line in enumerate("".join(parser.parts).splitlines(), 1):
            _add_block(result, line.strip(), f"HTML提取文本第 {index} 行", "text")
        _warn(result, "HTML仅提取静态正文，不运行脚本或加载外部资源；动态页面与图片信息需另行提供。")
    else:
        raise ValueError("不支持的附件格式。")
