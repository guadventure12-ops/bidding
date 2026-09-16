"""Iterate-2 export-only contract using synthetic content and OOXML.

No database, model, renderer, or reference customer document is used here.
The separate visual gate checks page placement; these tests check semantic
formatting that screenshots alone cannot establish.
"""

import copy
import re

import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

from app.documents import compose_docx, cover_identity


def _value(paragraph, property_name):
    direct = getattr(paragraph.paragraph_format, property_name)
    if direct is not None:
        return direct
    style = paragraph.style
    while style is not None:
        value = getattr(style.paragraph_format, property_name)
        if value is not None:
            return value
        style = style.base_style
    return None


def _font_value(run, paragraph, property_name):
    direct = getattr(run.font, property_name)
    if direct is not None:
        return direct
    style = paragraph.style
    while style is not None:
        value = getattr(style.font, property_name)
        if value is not None:
            return value
        style = style.base_style
    return None


def _east_asia(run, paragraph):
    elements = [run._r]
    style = paragraph.style
    while style is not None:
        elements.append(style.element)
        style = style.base_style
    for element in elements:
        fonts = element.find("./" + qn("w:rPr") + "/" + qn("w:rFonts"))
        if fonts is not None and fonts.get(qn("w:eastAsia")):
            return fonts.get(qn("w:eastAsia"))
    return None


def _numbering(document, paragraph):
    candidates = [paragraph._p]
    style = paragraph.style
    while style is not None:
        candidates.append(style.element)
        style = style.base_style
    properties = next((node.find("./" + qn("w:pPr") + "/" + qn("w:numPr"))
                       for node in candidates
                       if node.find("./" + qn("w:pPr") + "/" + qn("w:numPr")) is not None), None)
    assert properties is not None, f"{paragraph.text!r} has no native numbering"
    num_id = properties.find(qn("w:numId")).get(qn("w:val"))
    ilvl_node = properties.find(qn("w:ilvl"))
    ilvl = ilvl_node.get(qn("w:val")) if ilvl_node is not None else "0"
    numbering = document.part.numbering_part.element
    num = numbering.xpath(f'./w:num[@w:numId="{num_id}"]')[0]
    abstract_id = num.find(qn("w:abstractNumId")).get(qn("w:val"))
    level = numbering.xpath(f'./w:abstractNum[@w:abstractNumId="{abstract_id}"]/w:lvl[@w:ilvl="{ilvl}"]')[0]
    return {
        "num_id": num_id,
        "level": int(ilvl),
        "format": level.find(qn("w:numFmt")).get(qn("w:val")),
        "text": level.find(qn("w:lvlText")).get(qn("w:val")),
        "level_element": level,
    }


@pytest.fixture
def exported(tmp_path):
    project = {
        "name": "电子会计档案项目",
        "buyer": "合成采购单位有限公司",
        "project_number": "QA-ITERATE-2-001",
        "document_title": "商务、技术文件",
        "_omit_response_table": True,
        "_omit_attachments": True,
    }
    company = {"name": "合成投标单位有限公司"}
    sections = [
        {"title": "一、技术方案", "content": (
            "# 1. 功能设计\n"
            "普通正文 **不可加粗的业务说明**，API 3.2、2026年、99.9%、50000条保持原值。\n\n"
            "## 1.1. 数据接口\n"
            "接口正文包含OAuth 2.0和GB/T 18894—2016。\n\n"
            "### 1.1.1. 安全机制\n安全机制正文。\n\n"
            "#### 1.1.1.1. 明细层\n明细正文。\n\n"
            "# 2. 实施方案\n实施正文。\n\n"
            "1. 保留人工条款编号\n2. 保留第二条编号\n\n"
            "| 字段 | 内容 |\n| --- | --- |\n| API版本 | 3.2 |\n| 并发记录 | 50000条 |"
        )},
        {"title": "二、服务方案", "content": "# 1. 服务安排\n人员安排正文。\n\n## 1.1. 例行巡检\n巡检正文。"},
    ]
    original = copy.deepcopy((project, company, sections))
    path = tmp_path / "format-contract.docx"
    result = compose_docx(project, [], sections, str(path), company, final=True)
    return Document(path), result, (project, company, sections), original


def test_iterate2_fonts_and_heading_hierarchy(exported):
    document, _, _, _ = exported
    expected = {"技术方案": 1, "功能设计": 2, "数据接口": 3,
                "安全机制": 4, "明细层": 5, "实施方案": 2,
                "服务方案": 1, "服务安排": 2, "例行巡检": 3}
    headings = {p.text: p for p in document.paragraphs if p.style.name.startswith("Heading ")}
    assert set(expected).issubset(headings)
    for text, level in expected.items():
        paragraph = headings[text]
        assert paragraph.style.name == f"Heading {level}"
        assert _value(paragraph, "first_line_indent") in (None, 0)
        assert _value(paragraph, "left_indent") in (None, 0)
        for run in paragraph.runs:
            if run.text:
                assert _east_asia(run, paragraph) in {"黑体", "SimHei"}
                assert _font_value(run, paragraph, "size").pt == (18 if level == 1 else 15)


def test_iterate2_native_numbering_without_duplicate_text_prefix(exported):
    document, _, _, _ = exported
    paragraphs = {p.text: p for p in document.paragraphs}
    first = _numbering(document, paragraphs["技术方案"])
    assert first["format"] in {"chineseCounting", "chineseCountingThousand"}
    assert first["text"] == "%1、"
    for text, level, pattern in (("功能设计", 1, "%2."), ("数据接口", 2, "%2.%3."),
                                 ("安全机制", 3, "%2.%3.%4."), ("明细层", 4, "%2.%3.%4.%5.")):
        detail = _numbering(document, paragraphs[text])
        assert detail["level"] == level
        assert detail["format"] == "decimal"
        assert detail["text"] == pattern
    assert not any(re.match(r"^(?:一、|1\.)", p.text) for p in document.paragraphs if p.style.name.startswith("Heading "))


def test_iterate2_second_level_resets_at_next_chapter(exported):
    document, _, _, _ = exported
    paragraphs = {p.text: p for p in document.paragraphs}
    first = _numbering(document, paragraphs["功能设计"])
    second = _numbering(document, paragraphs["服务安排"])
    assert first["level"] == second["level"] == 1
    # A shared multilevel list restarts subordinate levels by default, or with
    # an explicit lvlRestart=1. Independent list instances must start at 1.
    if first["num_id"] == second["num_id"]:
        restart = second["level_element"].find(qn("w:lvlRestart"))
        assert restart is None or restart.get(qn("w:val")) == "1"
    else:
        start = second["level_element"].find(qn("w:start"))
        assert start is not None and start.get(qn("w:val")) == "1"


def test_iterate2_body_unbold_and_two_character_indent_preserves_values(exported):
    document, _, _, _ = exported
    paragraph = next(p for p in document.paragraphs if p.text.startswith("普通正文"))
    assert paragraph.text == "普通正文 不可加粗的业务说明，API 3.2、2026年、99.9%、50000条保持原值。"
    assert _value(paragraph, "first_line_indent").pt == 24
    for run in paragraph.runs:
        assert _east_asia(run, paragraph) in {"宋体", "SimSun"}
        assert _font_value(run, paragraph, "size").pt == 12
        assert not _font_value(run, paragraph, "bold")
    text = "\n".join(p.text for p in document.paragraphs)
    assert "OAuth 2.0和GB/T 18894—2016" in text
    assert "1. 保留人工条款编号" in text
    assert "2. 保留第二条编号" in text


def test_iterate2_table_zero_first_indent_and_same_body_type(exported):
    document, _, _, _ = exported
    table = next(table for table in document.tables if table.cell(0, 0).text == "字段")
    assert table.rows[0]._tr.trPr.find(qn("w:tblHeader")) is not None
    assert table.cell(2, 1).text == "50000条"
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                assert _value(paragraph, "first_line_indent") in (None, 0)
                for run in paragraph.runs:
                    assert _east_asia(run, paragraph) in {"宋体", "SimSun"}
                    assert _font_value(run, paragraph, "size").pt == 12
                    assert not _font_value(run, paragraph, "bold")


def test_iterate2_cover_has_reference_roles_and_geometry(exported):
    document, _, _, _ = exported
    paragraphs = {p.text: p for p in document.paragraphs}
    for text, size in (("合成采购单位有限公司", 26), ("电子会计档案项目", 26), ("商务、技术文件", 28)):
        paragraph = paragraphs[text]
        assert paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER
        assert _value(paragraph, "first_line_indent") in (None, 0)
        for run in paragraph.runs:
            if run.text:
                assert _east_asia(run, paragraph) in {"宋体", "SimSun"}
                assert _font_value(run, paragraph, "size").pt == size
                assert _font_value(run, paragraph, "bold")
    first_page = document.sections[0]
    assert first_page.top_margin.cm == pytest.approx(2.54, abs=.02)
    assert first_page.bottom_margin.cm == pytest.approx(2.54, abs=.02)
    assert first_page.left_margin.cm == pytest.approx(3.17, abs=.02)
    assert first_page.right_margin.cm == pytest.approx(3.17, abs=.02)
    assert len(document.sections) >= 2  # cover geometry must not narrow existing body tables
    assert document.sections[-1].page_width - document.sections[-1].left_margin - document.sections[-1].right_margin >= 16 * 360000
    whole = "\n".join(p.text for p in document.paragraphs)
    assert "QA-ITERATE-2-001" in whole
    assert "供应商名称" in whole and "（盖章）" in whole and "（签字）" in whole
    assert "编制日期" not in whole  # reference has signature date blanks, not today's date


def test_iterate2_export_does_not_mutate_source_objects(exported):
    _, result, current, original = exported
    assert current == original
    assert result["path"].endswith("format-contract.docx")


def test_iterate2_appendix_identifiers_are_preserved(tmp_path):
    path = tmp_path / "appendices.docx"
    compose_docx({"name": "合成项目", "_deviation_tables": True},
                 [{"id": "t", "category": "technical", "text": "原技术要求", "response": "技术响应"}],
                 [{"title": "附件16 初次报价一览表", "content": "独立附表正文。"}], str(path))
    document = Document(path)
    headings = [p.text for p in document.paragraphs if p.style.name.startswith("Heading ")]
    assert "附件16 初次报价一览表" in headings
    assert "附件11 商务条款偏离表" in headings
    assert "附件12 技术偏离表" in headings
    # The appendix's source number is distinct from its outline position.
    assert all("原技术要求" not in heading for heading in headings)


def test_cover_name_comes_from_unique_saved_tender_text_not_internal_label():
    project={'name':'内部项目 · 验收','buyer':'合成采购人'}
    chunks=[{'id':'a','document_id':'t','locator':'段4','text':'合成采购人'},
            {'id':'b','document_id':'t','locator':'段5','text':'电子会计档案项目'}]
    identity=cover_identity(project,chunks)
    assert identity['project_name']=='电子会计档案项目'
    assert identity['status']=='source_verified' and identity['sources'][0]['id']=='b'
    assert project['name']=='内部项目 · 验收'
    conflict=cover_identity(project,chunks+[{'id':'c','document_id':'t2','text':'项目名称：费控项目'}])
    assert conflict['status']=='conflict' and conflict['project_name']==project['name']
    # A buyer at the end of one file is not joined to a title in another file.
    chunks[1]['document_id']='other'
    assert cover_identity(project,chunks)['status']=='project_field'


def test_official_title_is_used_in_cover_headers_and_deviation_tables(tmp_path):
    project={'name':'内部验收任务','buyer':'合成采购人','_deviation_tables':True,
             '_cover_identity':{'project_name':'来源项目名','buyer':'合成采购人','status':'source_verified','sources':[]}}
    before=copy.deepcopy(project)
    path=tmp_path/'official.docx'
    compose_docx(project,[],[{'title':'方案','content':'未改变的正文。'}],str(path))
    document=Document(path)
    text='\n'.join(p.text for p in document.paragraphs)
    assert '来源项目名' in text and '采购项目：来源项目名' in text
    assert '内部验收任务' not in text
    assert project==before


def test_actual_export_api_uses_saved_cover_without_rewriting_sections(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app import db,provider,workflow,review_rules
    from app.main import app
    monkeypatch.setattr(db,'DATA',tmp_path/'data')
    monkeypatch.setenv('MX_TESTING','1');monkeypatch.setenv('LANGFUSE_ENABLED','false')
    monkeypatch.setattr(provider,'chat_json',lambda *a,**k:pytest.fail('No model calls'))
    db.init()
    db.insert('projects',{'id':'p','name':'工作台内部名 · 验收','buyer':'合成采购人',
        'project_number':'TEST-14','company_name':'合成投标人','metadata':{'field_overrides':{'buyer':{'value':'合成采购人'},'project_number':{'value':'TEST-14'}}},'created_at':db.now(),'updated_at':db.now()})
    db.insert('documents',{'id':'d','project_id':'p','name':'采购文件.docx','path':'not-opened-original.docx',
        'sha256':'synthetic','source_type':'tender','created_at':db.now(),'updated_at':db.now()})
    for i,text in enumerate(('合成采购人','来源电子档案项目')):
        db.insert('chunks',{'id':f'c{i}','document_id':'d','ordinal':i,'locator':f'段落{i+1}','text':text})
    db.insert('sections',{'id':'s','project_id':'p','ordinal':0,'title':'方案','content':'实际正文API 3.2不改变。','created_at':db.now(),'updated_at':db.now()})
    db.set_setting('review_rules',{id:False for id in review_rules.IDS})
    workflow.refresh_project_basics('p')
    tables=('sections','documents','chunks','requirements','reviews','jobs','settings')
    before={t:db.all('SELECT * FROM '+t) for t in tables}
    client=TestClient(app)
    response=client.post('/api/projects/p/export',json={'format':'docx','final':True})
    assert response.status_code==200,response.text
    export=db.one('SELECT * FROM exports WHERE id=?',(response.json()['id'],))
    doc=Document(export['path']);text='\n'.join(p.text for p in doc.paragraphs)
    assert '来源电子档案项目' in text and '工作台内部名' not in text
    assert '商务、技术文件' in text and '实际正文API 3.2不改变。' in text
    assert before=={t:db.all('SELECT * FROM '+t) for t in tables}
    assert db.one('SELECT name FROM projects WHERE id="p"')['name']=='工作台内部名 · 验收'


def test_iterate2_numbering_schema_order_and_identifier(exported):
    document, _, _, _ = exported
    numbering = document.part.numbering_part.element
    children = list(numbering)
    abstract_positions = [i for i, item in enumerate(children) if item.tag == qn("w:abstractNum")]
    instance_positions = [i for i, item in enumerate(children) if item.tag == qn("w:num")]
    assert abstract_positions and instance_positions
    # Word may quietly ignore later outline levels when a new abstractNum is
    # appended after existing num instances. A parsed numPr alone misses this.
    assert max(abstract_positions) < min(instance_positions)
    paragraph = next(p for p in document.paragraphs if p.text == "技术方案")
    detail = _numbering(document, paragraph)
    abstract = detail["level_element"].getparent()
    identifier = abstract.find(qn("w:nsid"))
    assert identifier is not None
    assert re.fullmatch(r"[0-9A-Fa-f]{8}", identifier.get(qn("w:val"), ""))
