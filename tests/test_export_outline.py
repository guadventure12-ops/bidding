"""Offline, synthetic checks for non-mutating export grouping."""
from copy import deepcopy

from app.export_outline import outline_sections


def test_groups_numbered_family_in_original_order_with_readable_part_titles():
    rows = [
        {"id": "a", "title": "资格与商务响应（2）", "content": "# 资格与商务响应（2）\n\n## 一、关于营业执照\n正文 2026 年\n## 二、信用记录\n保留"},
        {"id": "middle", "title": "独立章节", "content": "独立正文"},
        {"id": "b", "title": "资格与商务响应(1)", "content": "# 资格与商务响应(1)——响应函\n签章栏保持原样"},
    ]
    result = outline_sections(rows)
    assert [group["title"] for group in result] == ["资格与商务响应", "独立章节"]
    assert [part["section"]["id"] for part in result[0]["parts"]] == ["a", "b"]
    assert [part["title"] for part in result[0]["parts"]] == ["营业执照、信用记录", "响应函"]
    assert "# 资格与商务响应(1)——响应函" in result[0]["parts"][1]["content"]
    assert result[1]["parts"][0]["title"] is None


def test_preserves_originals_tables_numbers_images_and_only_removes_exact_initial_heading():
    content = "\n# **技术响应（1）**\r\n\r\n## 技术说明\n2026 年，金额 1,234.56 元。\n| 项目 | 数值 |\n| --- | --- |\n| 规格 | 30 |\n![图](local.png)\n# 技术响应（1）\n后续同名标题不删。"
    rows = [{"id": "a", "title": "技术响应（1）", "content": content, "evidence_ids": ["e1"]},
            {"id": "b", "title": "技术响应（2）", "content": "## 交付计划\n真实正文"}]
    before = deepcopy(rows)
    result = outline_sections(rows)
    part = result[0]["parts"][0]
    assert part["content"] == content.replace("# **技术响应（1）**\r\n", "", 1)
    assert part["section"] == rows[0]
    part["section"]["evidence_ids"].append("new")
    assert rows == before


def test_single_business_parentheses_and_years_are_not_grouped_or_renamed():
    rows = [{"title": title, "content": "正文"} for title in
            ("项目计划（2026）", "项目计划（2027）", "技术标准（GB/T）", "其他要求响应（1）")]
    result = outline_sections(rows)
    assert [group["title"] for group in result] == [row["title"] for row in rows]
    assert not any(group["grouped"] for group in result)


def test_near_match_plain_body_and_later_duplicate_headings_are_preserved():
    for content in ("# 技术响应（1）：额外说明\n实际内容", "技术响应（1）\n实际内容", "正文\n# 技术响应（1）\n实际内容"):
        result = outline_sections([{"title": "技术响应（1）", "content": content}])
        assert result[0]["parts"][0]["content"] == content


def test_unique_fallback_avoids_uuids_locators_and_facts_as_invented_titles():
    rows = [
        {"title": "其他要求响应（1）", "content": "### req_001\n公司 2026 年提供服务。"},
        {"title": "其他要求响应（2）", "content": "### 原文位置：第 3 页\n| 甲 | 乙 |\n| --- | --- |"},
        {"title": "其他要求响应（3）", "content": "### 123e4567-e89b-12d3-a456-426614174000\n实际承诺"},
    ]
    parts = outline_sections(rows)[0]["parts"]
    assert [part["title"] for part in parts] == ["补充说明", "补充说明之二", "补充说明之三"]
    assert [part["content"] for part in parts] == [row["content"] for row in rows]


def test_repeated_themes_choose_different_existing_labels_before_neutral_fallback():
    rows = [{"title": f"实施方案（{index}）", "content": "## 部署安排\n正文\n## 验收安排\n正文"}
            for index in range(1, 5)]
    assert [part["title"] for part in outline_sections(rows)[0]["parts"]] == [
        "部署安排、验收安排", "部署安排", "验收安排", "补充说明"]


def test_code_headings_do_not_become_subtitles_and_repeated_calls_are_stable():
    rows = [{"title": f"技术响应（{index}）", "content": "```markdown\n## 错误示例\n```\n## 实际说明\n内容"}
            for index in range(1, 3)]
    result = outline_sections(rows)
    assert result == outline_sections(rows)
    assert [part["title"] for part in result[0]["parts"]] == ["实际说明", "补充说明"]


def test_numbered_business_sentences_are_not_promoted_to_titles():
    rows=[{'title':'资格响应（1）','content':'## 主体资格\n1. 我方已提供营业执照。\n## 信用情况\n说明。'},
          {'title':'资格响应（2）','content':'## 授权委托\n正文。'}]
    assert outline_sections(rows)[0]['parts'][0]['title']=='主体资格、信用情况'


def test_single_existing_topic_promoted_to_h2_is_not_repeated_as_h3():
    from app.export_outline import part_body
    rows=[{'title':'资格响应（1）','content':'# 资格响应（1）\n## 一、主体资格\n实际正文。'},
          {'title':'资格响应（2）','content':'## 授权委托\n实际说明。'}]
    first=outline_sections(rows)[0]['parts'][0]
    assert first['title']=='主体资格' and '主体资格' not in part_body(first)
    assert '实际正文。' in part_body(first)


def test_nine_synthetic_families_form_eight_groups_and_one_standalone():
    counts = [4, 9, 11, 3, 2, 1, 4, 19, 12]
    rows = [{"id": f"s-{family}-{part}", "title": f"领域{family}（{part}）", "content": f"## 功能项{part}\n数值{part}"}
            for family, count in enumerate(counts, 1) for part in range(1, count + 1)]
    result = outline_sections(rows)
    assert len(result) == 9
    assert sum(group["grouped"] for group in result) == 8
    assert sum(len(group["parts"]) for group in result if group["grouped"]) == 64
    assert [part["section"]["id"] for group in result for part in group["parts"]] == [row["id"] for row in rows]
