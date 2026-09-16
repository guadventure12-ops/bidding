"""Synthetic source structures; no database, network, provider, or customer files."""
import copy
import json
import re

import pytest

from app.proposal_blueprint import (
    build_blueprint, canonicalize_requirements, extract_features,
    extract_response_slots, extract_scoring,
)


def paragraph(id, text, doc="tender"):
    return {"id": id, "document_id": doc, "text": text, "locator": "正文 / 段落 " + id, "kind": "paragraph"}


def table(id, cells, index=2, row=1, doc="tender", stored=False):
    result = {"id": id, "document_id": doc, "text": " | ".join(cells),
              "locator": f"正文 / 表格 {index} / 第 {row} 行", "kind": "table_row"}
    value = {"index": index, "row": row, "cells": cells, "spans": [1] * len(cells)}
    result["metadata" if stored else "table"] = {"table": value} if stored else value
    return result


def requirement(id, block, text=None, category="technical"):
    return {"id": id, "chunk_id": block["id"], "document_id": block["document_id"],
            "locator": block["locator"], "title": text or block["text"], "text": text or block["text"],
            "quote": block["text"], "category": category}


def sources():
    return [
        paragraph("a", "商务、技术文件目录"),
        paragraph("b", "评审索引表（格式见附件10）"),
        paragraph("c", "需求理解与解决方案（根据评标细则内容提供资料，格式自拟）"),
        paragraph("d", "功能模块设计及系统功能演示（格式自拟）"),
        paragraph("e", "认证证书（格式自拟）"),
        paragraph("f", "附件10：评审索引表"),
        table("form-head", ["评审内容", "章节", "页码"], index=8),
        table("form-row", ["需求理解", "", ""], index=8, row=2),
        paragraph("stop", "附件11：其他表格"),
        table("fh", ["模块", "功能名称", "功能描述"]),
        table("f1", ["基础配置", "登录安全", "提供密码及账号登录配置"], row=2),
        table("f2", ["", "", "提供登录异常处理"], row=3, stored=True),
        table("f3", ["智能检索", "全文查询", "按关键词检索归档凭证"], row=4),
        table("sh", ["序号", "评审标准", "分值"], index=3),
        table("s1", ["1", "需求理解与解决方案", "识别重难点并提供可落地方案", "10"], index=3, row=2),
    ]


def test_explicit_slots_keep_order_and_original_provenance():
    rows = extract_response_slots(sources())
    assert [r["title"] for r in rows] == ["评审索引表", "需求理解与解决方案", "功能模块设计及系统功能演示", "认证证书"]
    assert rows[0]["source_refs"][0]["chunk_id"] == "b"
    assert "格式见附件10" in rows[0]["source_refs"][0]["quote"]


def test_feature_merged_columns_inherit_only_inside_same_table_and_document():
    blocks = sources() + [table("foreign", ["", "", "未知模块的功能"], row=5, doc="second")]
    features, issues = extract_features(blocks)
    assert [(r["module"], r["function"]) for r in features] == [("基础配置", "登录安全"), ("基础配置", "登录安全"), ("智能检索", "全文查询")]
    assert not issues
    assert features[1]["description"] == "提供登录异常处理"
    assert features[1]["source"]["chunk_id"] == "f2"


def test_incomplete_feature_owner_is_reported_not_guessed():
    blocks = [table("h", ["模块", "功能名称", "功能描述"]), table("x", ["新模块", "", "输入数据"], row=2)]
    features, issues = extract_features(blocks)
    assert not features
    assert issues[0]["type"] == "unresolved_feature_owner"


def test_scoring_preserves_original_factors_not_company_claims():
    scores = extract_scoring(sources())
    assert len(scores) == 1
    assert scores[0]["title"] == "需求理解与解决方案"
    assert scores[0]["points"] == 10.0
    assert scores[0]["source"]["chunk_id"] == "s1"


def test_scoring_body_mentions_factors_and_points_without_becoming_header():
    blocks = [table("h", ["序号", "评审标准", "分值"], index=3),
              table("s", ["3", "项目人员配置", "人员评分因素应当体现，分值范围为0至7分。", "7"], index=3, row=2)]
    assert extract_scoring(blocks)[0]["title"] == "项目人员配置"


def test_canonical_duplicates_retain_every_id_and_location_and_do_not_merge_different_meaning():
    first = paragraph("one", "供应商应提供方案")
    second = paragraph("two", "供应商应提供方案")
    rows = [requirement("z", first), requirement("a", second), requirement("different", second, "方案须包含风险计划")]
    merged = canonicalize_requirements(rows)
    assert len(merged) == 2
    duplicate = next(r for r in merged if len(r["alias_ids"]) == 2)
    assert duplicate["canonical_id"] == "a"
    assert duplicate["alias_ids"] == ["a", "z"]
    assert {r["locator"] for r in duplicate["source_refs"]} == {first["locator"], second["locator"]}
    assert len(canonicalize_requirements([rows[0], {**rows[0], "id": "b", "category": "commercial"}])) == 2


def test_complete_ledger_separates_narrative_forms_and_internal_procurement():
    blocks = sources()
    byid = {b["id"]: b for b in blocks}
    ordinary = paragraph("proc", "评审小组组织评审并推荐成交候选人")
    rows = [requirement("feature", byid["f2"]), requirement("score", byid["s1"]),
            requirement("format", byid["form-row"], category="format"), requirement("proc", ordinary, category="other")]
    before = copy.deepcopy((rows, blocks))
    result = build_blueprint("p", rows, blocks)
    assert result["recognized"] is True and result["profile"] == "technical_proposal"
    assert result["coverage"]["all_ids_preserved"]
    assert result["coverage"]["mapped_requirements"] == 4
    ledger = {r["requirement_id"]: r for r in result["ledger"]}
    assert ledger["feature"]["disposition"] == "narrative"
    assert ledger["format"]["disposition"] == "deliverable"
    assert ledger["proc"]["volume"] == "internal"
    assert (rows, blocks) == before


def test_all_technical_source_slots_exist_even_without_model_requirements():
    result = build_blueprint("p", [], sources())
    assert [r["title"] for r in result["groups"] if r["volume"] == "technical"] == ["评审索引表", "需求理解与解决方案", "功能模块设计及系统功能演示", "认证证书"]
    certificate = next(r for r in result["sections"] if r["group_title"] == "认证证书")
    assert certificate["content_kind"] == "attachment"
    assert certificate["requires_project_confirmation"] is True
    assert not certificate["generate_body"]
    assert certificate["enterprise_fact_evidence"] is False


def test_form_schema_keeps_empty_cells_and_source_rows():
    result = build_blueprint("p", [], sources())
    form = next(r for r in result["sections"] if r["group_title"] == "评审索引表")
    assert form["form_schema"]["tables"][0]["rows"][1]["cells"] == ["需求理解", "", ""]
    assert form["form_schema"]["tables"][0]["source_refs"][1]["chunk_id"] == "form-row"
    assert not any(ref["chunk_id"] == "stop" for ref in form["form_schema"]["source_refs"])


def test_tender_template_aliases_and_separate_qualification_price_schemas():
    blocks = [paragraph("h", "商务技术文件目录"), paragraph("t", "技术条款偏离表（格式见附件12）"),
              paragraph("a", "附件12：技术偏离表"), table("b", ["技术条款", "响应", "偏离"], index=5),
              paragraph("c", "附件3：法定代表人授权书"), paragraph("d", "被授权人：________（签名）"),
              paragraph("e", "附件16：初次报价一览表"), table("f", ["项目", "金额", "备注"], index=6)]
    result = build_blueprint("p", [], blocks)
    technical = next(s for s in result["sections"] if s["group_title"] == "技术条款偏离表")
    assert technical["form_schema"]["tables"][0]["rows"][0]["cells"] == ["技术条款", "响应", "偏离"]
    qualification = next(s for s in result["sections"] if s["volume"] == "qualification")
    assert qualification["form_schema"]["paragraphs"][-1]["text"] == "被授权人：________（签名）"
    pricing = next(s for s in result["sections"] if s["volume"] == "pricing")
    assert pricing["form_schema"]["tables"][0]["rows"][0]["cells"] == ["项目", "金额", "备注"]


def test_model_batch_size_and_requirement_order_do_not_create_chapters():
    blocks = sources()
    feature = next(r for r in blocks if r["id"] == "f1")
    rows = [requirement("r-" + str(i), feature, "功能说明" + str(i)) for i in range(37)]
    a = build_blueprint("p", rows, blocks)
    b = build_blueprint("p", reversed(rows), blocks)
    assert [(s["section_key"], s["title"]) for s in a["sections"]] == [(s["section_key"], s["title"]) for s in b["sections"]]
    owners = {r["owner_section_key"] for r in a["ledger"]}
    assert len(owners) == 1
    assert all(re.fullmatch(r"[a-f0-9]{24}", s["section_key"]) for s in a["sections"])
    assert len({s["section_key"] for s in a["sections"]}) == len(a["sections"])


def test_unknown_tender_is_not_falsely_labelled_explicit_format():
    result = build_blueprint("p", [], [paragraph("a", "普通采购背景")])
    assert not result["recognized"]
    assert result["issues"][0]["type"] == "suggested_outline"
    assert all(not g["source_refs"] for g in result["groups"])
    json.dumps(result, ensure_ascii=False)


def test_duplicate_requirement_ids_are_rejected():
    row = requirement("r", paragraph("a", "重复ID"))
    with pytest.raises(ValueError, match="要求ID重复"):
        build_blueprint("p", [row, row], sources())


def test_split_large_source_row_retains_module_assignment():
    blocks = sources()
    row = requirement("r", next(b for b in blocks if b["id"] == "f2"))
    row["locator"] += "（字符 5001–9000）"
    result = build_blueprint("p", [row], blocks)
    owner = next(s for s in result["sections"] if s["section_key"] == result["ledger"][0]["owner_section_key"])
    assert owner["title"] == "基础配置"
