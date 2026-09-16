"""The existing model channel is exercised with a mock provider and isolated DB."""
import copy
import json
import socket

import pytest

from app import db, provider, review_rules, workflow
from app.proposal_generation import generate_proposal_section


def unit(count=1, kind="narrative", volume="technical", sources=True):
    requirements = [{"id": f"r{i}", "title": f"功能要求{i}", "text": f"提供功能流程{i}", "quote": f"提供功能流程{i}",
                     "category": "technical", "mandatory": 0, "score": ""} for i in range(count)]
    return {"id": "section-one", "project_id": "p", "title": "档案检索方案", "outline_group_id": "g", "outline_group_title": "功能模块设计",
            "legacy_title": "", "requirement_ids": [r["id"] for r in requirements], "requirements": requirements,
            "_proposal_spec": {"title": "档案检索方案", "group_title": "功能模块设计", "content_kind": kind, "volume": volume,
                               "purpose": "组织可验证的档案检索业务流程", "suggested_subtopics": ["操作流程", "异常处理", "验证方法"],
                               "recommended_chars": {"min": 1000, "target": 3000, "max": 5000},
                               "source_refs": [{"chunk_id": "tender-one", "locator": "正文 / 表格2 / 第3行", "quote": "系统应支持档案检索", "path": "C:/never-send-source-path"}] if sources else [],
                               "feature_rows": [], "score_factors": [], "form_schema": {}}}


def answer(target, content="## 操作流程\n\n拟按授权范围组织查询与结果核对。"):
    return {"content": content,
            "responses": [{"requirement_id": r["id"], "response": "按本节所述流程设计与验证。", "evidence_ids": [], "gap": False, "gap_reason": ""}
                          for r in target["requirements"]], "_usage": {"prompt_tokens": 10, "completion_tokens": 20}}


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    monkeypatch.setenv("MX_TESTING", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(db, "DATA", tmp_path / "isolated-data")
    db.init()
    calls = []

    def prohibited(*args, **kwargs):
        raise AssertionError("Network is prohibited during proposal tests")

    monkeypatch.setattr(socket.socket, "connect", prohibited)
    monkeypatch.setattr(socket, "getaddrinfo", prohibited)

    def prepare(p, working):
        return "要求数据：\n" + json.dumps(working["requirements"], ensure_ascii=False) + "\n证据数据：\n[]", {}, False

    monkeypatch.setattr(workflow, "_prepare_generation_request", prepare)

    def use_response(result):
        def fake_provider(system, prompt, cancel=None):
            calls.append({"system": system, "prompt": prompt})
            return copy.deepcopy(result)
        monkeypatch.setattr(provider, "chat_json", fake_provider)

    return {"calls": calls, "respond": use_response, "p": {"id": "p", "name": "合成项目", "company_name": "合成企业", "domain": "archive"}}


def test_whole_semantic_section_uses_one_provider_request_for_37_requirements(setup):
    target = unit(37)
    original = copy.deepcopy(target)
    setup["respond"](answer(target))
    result, evidence = generate_proposal_section("job-one", setup["p"], target, "operation-one")
    assert len(setup["calls"]) == 1
    assert len(result["responses"]) == 37
    assert result["_primary_request_units"] == 1
    assert result["_generation_mode"] == "proposal_whole_section"
    assert result["_usage"]["completion_tokens"] == 20
    assert target == original and evidence == {}
    prompt = setup["calls"][0]["prompt"]
    assert "完整二级章节" in prompt and "不是每条采购要求的解释清单" in prompt
    assert "组织可验证的档案检索业务流程" in prompt and '"R37"' in prompt
    assert '"r36"' not in prompt
    assert "C:/never-send-source-path" not in prompt
    assert db.all("SELECT * FROM sections") == []


def test_source_backed_narrative_without_requirement_rows_still_writes(setup):
    target = unit(0, sources=True)
    setup["respond"](answer(target))
    result, _ = generate_proposal_section("job", setup["p"], target, "operation")
    assert len(setup["calls"]) == 1
    assert result["content"] and result["responses"] == []


def test_missing_narrative_sources_returns_specific_internal_gap_without_model(setup):
    target = unit(0, sources=False)
    setup["respond"](answer(target))
    result, _ = generate_proposal_section("job", setup["p"], target, "operation")
    assert setup["calls"] == []
    assert result["content"] == ""
    assert "档案检索方案" in result["_internal_notes"][0]["message"]
    assert result["_generation_mode"] == "missing_writing_sources"


@pytest.mark.parametrize("kind", ["form", "attachment", "internal"])
def test_delivery_and_internal_slots_never_call_model(setup, kind):
    target = unit(3, kind=kind, volume="internal" if kind == "internal" else "technical")
    setup["respond"](answer(target, "不应使用的模型答案"))
    result, evidence = generate_proposal_section("job", setup["p"], target, "operation")
    assert setup["calls"] == [] and result["_model_calls"] == 0
    assert "不应使用的模型答案" not in result["content"]
    assert [r["requirement_id"] for r in result["responses"]] == ["r0", "r1", "r2"]
    assert all(r["gap"] and r["gap_reason"] for r in result["responses"])
    assert evidence == {} and result["_internal_notes"]


def test_form_template_retains_empty_fields_and_signature_unexecuted(setup):
    target = unit(kind="form")
    target["_proposal_spec"]["form_schema"] = {"tables": [{"rows": [{"cells": ["姓名", "职务"]}, {"cells": ["", ""]}]}],
                                                  "paragraphs": [{"text": "法定代表人：________________（签字）"}, {"text": "不得填写虚假数据。"}]}
    setup["respond"](answer(target))
    result, _ = generate_proposal_section("job", setup["p"], target, "operation")
    assert "| 姓名 | 职务 |" in result["content"]
    assert "|  |  |" in result["content"]
    assert "法定代表人：________________（签字）" in result["content"]
    assert "不得填写虚假数据" not in result["content"]
    assert "已签" not in result["content"]


@pytest.mark.parametrize("confirmed", [False, True])
def test_price_uses_only_project_confirmed_quotation_never_tender_budget(setup, confirmed):
    from test_procurement_forms import fixture
    target = unit(kind="form", volume="pricing")
    target['_proposal_spec']['form_schema'] = fixture([
        '附件16：报价表', {'rows':[['报价项目','含税报价（元）','备注'],['电子会计档案项目','','']]},
        '日期：________________'],volume='pricing')['form_schema']
    target["requirements"][0]["text"] = "招标预算500万元"
    setup["p"]["quotation"] = {"confirmed": confirmed, "total_including_tax": "123456.78", "items": [], "issues": []}
    result, _ = generate_proposal_section("job", setup["p"], target, "operation")
    assert "500万" not in result["content"]
    assert ("123456.78" in result["content"]) is confirmed
    assert "日期：________________" in result["content"]
    assert all(r["gap"] for r in result["responses"])
    assert setup["calls"] == []


def test_source_form_notes_use_the_existing_saved_todo_contract(setup):
    from test_procurement_forms import seven_forms
    from app import content_review
    target = unit(kind='form', volume='qualification')
    target['_proposal_spec']['form_schema'] = seven_forms()[0]['form_schema']
    result, _ = generate_proposal_section('job', setup['p'], target, 'operation')
    notes = content_review.separated_todos(target, result['_internal_notes'])
    assert notes and all('raw' in n for n in result['_internal_notes'])
    assert result['_form_audit']['source_count'] == 42
    assert setup['calls'] == []


def test_only_provided_asset_id_is_inserted_and_local_paths_never_sent(setup):
    target = unit()
    setup["p"]["_proposal_assets"] = [{"asset_id": "a-123", "title": "授权检索界面", "document_name": "产品说明", "locator": "图2", "path": "C:/private/company/picture.png"}]
    setup["respond"](answer(target, "## 检索操作\n\n按授权范围进行查询。\n\n![授权检索界面](asset:a-123)"))
    result, _ = generate_proposal_section("job", setup["p"], target, "operation")
    assert result["_selected_asset_ids"] == ["a-123"]
    assert "![授权检索界面](asset:a-123)" in result["content"]
    prompt = setup["calls"][0]["prompt"]
    assert "授权检索界面" in prompt and "产品说明" in prompt
    assert "C:/private" not in prompt and "picture.png" not in prompt


@pytest.mark.parametrize("markdown", ["![外图](https://example.com/a.png)", "![未知](asset:not-provided)", "![路径](C:/secret/a.png)", "![图][reference]", '<img src="https://example.com/a.png">'])
def test_unknown_external_or_malformed_image_fails_without_fetch(setup, markdown):
    target = unit()
    setup["respond"](answer(target, "本节实质方案内容。\n\n" + markdown))
    with pytest.raises(provider.ProviderError, match="图片|图资产"):
        generate_proposal_section("job", setup["p"], target, "operation")
    assert len(setup["calls"]) == 1
    assert db.all("SELECT * FROM sections") == []
    cache = list((db.DATA / "cache/model-responses/generation").glob("*.json"))
    assert len(cache) == 1
    assert json.loads(cache[0].read_text(encoding="utf-8"))["validation_failed"]


def test_all_off_legacy_rules_do_not_allow_missing_original_response_ids(setup):
    target = unit(2)
    result = answer(target)
    result["responses"] = result["responses"][:1]
    setup["respond"](result)
    with review_rules.scope({k: False for k in review_rules.IDS}):
        with pytest.raises(provider.ProviderError, match="全部原要求ID"):
            generate_proposal_section("job", setup["p"], target, "operation")
    assert len(setup["calls"]) == 1


def test_all_off_legacy_rules_still_separate_real_gaps_from_body(setup):
    target = unit()
    result = answer(target, "## 实施方案\n\n拟开展接口字段核对。\n\n【待补充：项目接口联系人姓名】")
    result["responses"][0]["response"] = "响应已有数据字段。\n【待补充：提供接口清单附件】"
    setup["respond"](result)
    with review_rules.scope({k: False for k in review_rules.IDS}):
        saved, _ = generate_proposal_section("job", setup["p"], target, "operation")
    assert "拟开展接口字段核对" in saved["content"] and "【待补充" not in saved["content"]
    assert "【待补充" not in saved["responses"][0]["response"]
    assert saved["responses"][0]["gap"]
    assert any("联系人姓名" in note["message"] for note in saved["_internal_notes"])


def test_all_off_does_not_accept_fabricated_evidence(setup):
    target = unit()
    setup["respond"](answer(target, "提供档案功能。[E:invented]"))
    with review_rules.scope({k: False for k in review_rules.IDS}):
        with pytest.raises(provider.ProviderError, match="未提供的企业证据"):
            generate_proposal_section("job", setup["p"], target, "operation")
    assert len(setup["calls"]) == 1


def test_cancellation_prevents_dispatch_and_checks_again_after_return(setup):
    target = unit()
    setup["respond"](answer(target))
    with pytest.raises(provider.Cancelled):
        generate_proposal_section("job", setup["p"], target, "operation", cancel=lambda: True)
    assert not setup["calls"]
    with pytest.raises(provider.Cancelled):
        generate_proposal_section("job", setup["p"], target, "operation", cancel=lambda: bool(setup["calls"]))
    assert len(setup["calls"]) == 1
    assert not db.all("SELECT * FROM sections")


def test_directory_wrappers_removed_but_internal_business_headings_remain(setup):
    target = unit()
    setup["respond"](answer(target, "# 档案检索方案\n\n## 操作流程\n\n按具体操作处理查询和结果。"))
    result, _ = generate_proposal_section("job", setup["p"], target, "operation")
    assert not result["content"].startswith("# 档案检索方案")
    assert "## 操作流程" in result["content"]


def test_heading_only_model_body_remains_an_explicit_gap(setup):
    target = unit()
    setup["respond"](answer(target, "# 档案检索方案\n\n## 操作流程"))
    result, _ = generate_proposal_section("job", setup["p"], target, "operation")
    assert result["responses"][0]["gap"]
    assert any("未形成可交付实质正文" in n["message"] for n in result["_internal_notes"])


def test_multiple_valid_images_on_one_line_are_individually_validated(setup):
    target = unit()
    setup["p"]["_proposal_assets"] = [{"asset_id": "a", "title": "A"}, {"asset_id": "b", "title": "B"}]
    setup["respond"](answer(target, "本节可执行的业务操作流程。\n\n![A](asset:a) ![B](asset:b)"))
    result, _ = generate_proposal_section("job", setup["p"], target, "operation")
    assert result["_selected_asset_ids"] == ["a", "b"]


def test_bad_spec_does_not_call_provider(setup):
    target = unit()
    del target["_proposal_spec"]
    with pytest.raises(ValueError, match="缺少采购方案蓝图"):
        generate_proposal_section("job", setup["p"], target, "operation")
    assert setup["calls"] == []
