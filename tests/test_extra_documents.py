from pathlib import Path
from zipfile import ZipFile

from app.extra_documents import parse_extra


def output():
    return {"blocks": [], "warnings": [], "text_chars": 0, "truncated": False, "page_count": None}


def write_zip(path, files):
    with ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def test_xlsx_preserves_coordinates_zero_strings_and_formula_gaps(tmp_path):
    path = tmp_path / "functions.xlsx"
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    write_zip(path, {
        "xl/workbook.xml": f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="功能清单" r:id="s1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships><Relationship Id="s1" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/sharedStrings.xml": f'<sst xmlns="{ns}"><si><t>共享正文</t></si></sst>',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{ns}"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>0</v></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>行内正文</t></is></c><c r="B2"><f>B1+1</f></c></row></sheetData><mergeCells><mergeCell ref="A1:A2"/></mergeCells></worksheet>',
    })
    result = output()
    parse_extra(path, result)
    text = "\n".join(x["text"] for x in result["blocks"])
    assert "A1（合并A1:A2）：共享正文" in text and "B1：0" in text
    assert "行内正文" in text and "公式缺少缓存值" in text
    assert result["blocks"][1]["locator"] == "工作表 功能清单 / 第 2 行"


def test_pptx_uses_presentation_order_instead_of_filename_order(tmp_path):
    path = tmp_path / "architecture.pptx"
    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    slide = lambda text: f'<p:sld xmlns:p="{p}" xmlns:a="{a}"><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:sld>'
    write_zip(path, {
        "ppt/presentation.xml": f'<p:presentation xmlns:p="{p}" xmlns:r="{r}"><p:sldIdLst><p:sldId r:id="second"/><p:sldId r:id="first"/></p:sldIdLst></p:presentation>',
        "ppt/_rels/presentation.xml.rels": '<Relationships><Relationship Id="first" Target="slides/slide1.xml"/><Relationship Id="second" Target="slides/slide2.xml"/></Relationships>',
        "ppt/slides/slide1.xml": slide("实际第二页"),
        "ppt/slides/slide2.xml": slide("实际第一页"),
    })
    result = output()
    parse_extra(path, result)
    assert [b["text"] for b in result["blocks"]] == ["实际第一页", "实际第二页"]
    assert [b["page"] for b in result["blocks"]] == [1, 2]


def test_csv_keeps_quoted_newline_and_zero(tmp_path):
    path = tmp_path / "报价.csv"
    path.write_text('产品,价格\n"跨行\n说明",0\n', encoding="utf-8-sig")
    result = output()
    parse_extra(path, result)
    assert len(result["blocks"]) == 2
    assert "跨行\n说明" in result["blocks"][1]["text"]
    assert "列2：0" in result["blocks"][1]["text"]


def test_html_discards_executable_content_and_keeps_table(tmp_path):
    path = tmp_path / "资料.html"
    path.write_text('<style>body{color:red}</style><script>steal()</script><p>企业&amp;产品</p><table><tr><td>归档</td><td>支持</td></tr></table>', encoding="utf-8")
    result = output()
    parse_extra(path, result)
    text = "\n".join(b["text"] for b in result["blocks"])
    assert "steal" not in text and "color:red" not in text
    assert "企业&产品" in text and "归档 | 支持" in text
