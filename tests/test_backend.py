"""Meaningful local safety and lifecycle checks; no paid API calls are made."""
import json
import os
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import db, model_jobs, provider, workflow
from app.main import app


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path / "data")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("MX_TESTING", "1")
    workflow._evidence_index.cache_clear()
    db.init()
    return tmp_path


def make_project():
    id = db.uid()
    db.insert("projects", {"id": id, "name": "电子档案投标验收", "domain": "archive", "company_name": "示例企业", "created_at": db.now(), "updated_at": db.now()})
    return id


def document(tmp_path, text, project_id=None, name="资料.txt"):
    source = tmp_path / name
    source.write_text(text, encoding="utf-8")
    return workflow.ingest(source, project_id)


def job(project_id, mode, cp=None):
    id = db.uid()
    db.insert("jobs", {"id": id, "project_id": project_id, "mode": mode, "created_at": db.now(), "checkpoint": cp or {}})
    return id, db.one("SELECT * FROM jobs WHERE id=?", (id,))


def approve(doc, scope="archive"):
    db.update("documents", doc["id"], {"status": "approved", "scope": scope, "updated_at": db.now()})


def seed_review(database):
    p = make_project()
    db.update("projects", p, {"project_number": "TEST-001", "buyer": "测试采购人", "metadata": {"field_overrides": {"project_number": {"value": "TEST-001"}, "buyer": {"value": "测试采购人"}}}})
    tender = document(database, "系统必须支持电子档案检索和权限管理。", p, "招标.txt")
    enterprise = document(database, "示例会计电子档案系统支持电子档案检索和权限管理。", name="企业.txt")
    approve(enterprise)
    source = db.one("SELECT * FROM chunks WHERE document_id=?", (tender["id"],))
    evidence = db.one("SELECT * FROM chunks WHERE document_id=?", (enterprise["id"],))
    req_id = workflow._save_requirement(p, source, {"quote": source["text"], "title": "检索和权限", "category": "technical"}, "ai")
    db.update("requirements", req_id, {"response": "支持检索和权限管理", "status": "confirmed", "evidence_ids": [evidence["id"]]})
    sec = db.uid()
    db.insert("sections", {"id": sec, "project_id": p, "ordinal": 0, "title": "技术方案", "content": f"支持检索和权限管理。[E:{evidence['id']}]", "requirement_ids": [req_id], "evidence_ids": [evidence["id"]], "status": "approved", "created_at": db.now(), "updated_at": db.now()})
    db.touch(p, analysis_status="complete", analysis_fingerprint=workflow.fingerprint(p))
    return p, tender, enterprise, req_id, sec, evidence


def test_dpapi_roundtrip_and_no_key_leak(database):
    if os.name != "nt":
        pytest.skip("DPAPI is Windows-specific")
    with TestClient(app) as client:
        result = client.patch("/api/settings", json={"api_key": "test-secret-not-a-real-key"})
        assert result.status_code == 200
        assert result.json()["key_configured"] is True
        assert "test-secret" not in result.text
        assert provider.get_key() == "test-secret-not-a-real-key"
        raw = db.one("SELECT value FROM settings WHERE key='api_key_encrypted'")["value"]
        assert "test-secret" not in raw
        assert "test-secret" not in client.get("/api/dashboard").text
        client.patch("/api/settings", json={"api_key": ""})
        assert not provider.key_configured()


def test_host_origin_and_settings_url_boundaries(database):
    with TestClient(app) as client:
        assert client.get("/api/health", headers={"host": "attacker.example"}).status_code == 403
        assert client.post("/api/projects", headers={"origin": "https://evil.example"}, json={"name": "bad"}).status_code == 403
        assert client.get("/api/health", headers={"sec-fetch-site": "cross-site"}).status_code == 403
        for url in ("http://api.deepseek.com", "https://api.deepseek.com.evil.example", "https://api.deepseek.com@evil.example", "https://127.0.0.1", "https://api.deepseek.com/?key=x"):
            assert client.patch("/api/settings", json={"base_url": url}).status_code == 400
        assert client.patch("/api/settings", json={"base_url": "https://api.deepseek.com/v1"}).status_code == 200


def test_missing_key_cannot_generate_but_all_local_chunks_scanned(database):
    p = make_project()
    text = "\n".join(f"第{i}项：系统必须提供电子档案查询能力，支持权限管理与归档验收。" for i in range(550))
    doc = document(database, text, p)
    assert doc["parse_status"] == "ready"
    id, record = job(p, "analyze")
    result = workflow.run_analyze(id, record)
    assert result["ai_completed"] is False
    assert workflow.project(p)["analysis_status"] == "local_candidates"
    reqs = db.all("SELECT * FROM requirements WHERE project_id=?", (p,))
    assert any("第549项" in req["quote"] for req in reqs)
    assert all(r["origin"] == "local" and not r["verified"] for r in reqs)
    db.update("jobs", id, {"status": "succeeded"})
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{p}/run", json={"mode": "generate"})
        assert response.status_code == 400
        assert "API Key" in response.json()["detail"]
        assert client.post(f"/api/projects/{p}/export", json={"format": "md", "final": True}).status_code == 400
        draft = client.post(f"/api/projects/{p}/export", json={"format": "md"})
        assert draft.status_code == 200
        assert "第549项" in client.get(draft.json()["url"]).text


def test_knowledge_is_pending_deduplicated_and_expiry_scoped(database):
    d = document(database, "电子档案支持检索和归档。")
    assert d["status"] == "pending"
    assert not workflow.search_evidence("电子档案归档", "archive")
    duplicate = workflow.ingest(database / "资料.txt")
    assert duplicate["id"] == d["id"] and duplicate["duplicate"]
    approve(d)
    assert workflow.search_evidence("电子档案归档", "archive")[0]["document_id"] == d["id"]
    assert not workflow.search_evidence("电子档案归档", "expense")
    db.update("documents", d["id"], {"valid_until": "2000-01-01"})
    assert not workflow.search_evidence("电子档案归档", "archive")
    db.update("documents", d["id"], {"valid_until": "", "scope": "historical"})
    assert not workflow.search_evidence("电子档案归档", "archive")


def test_batched_extraction_validates_locators_and_reuses_checkpoint(database, monkeypatch):
    p = make_project()
    document(database, "\n".join(f"第{i}项：系统必须支持第{i}类档案检索。" for i in range(1400)), p)
    db.set_setting("batch_chars", 6000)
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    calls = []

    def model(system, prompt, cancel=None):
        chunks = json.loads(prompt.split("文档数据：\n")[1])
        calls.append([c["chunk_id"] for c in chunks])
        return {"requirements": [{"chunk_id": c["chunk_id"], "quote": c["text"][:50], "text": "检索要求", "title": "档案检索", "category": "technical"} for c in chunks]}

    monkeypatch.setattr(provider, "chat_json", model)
    id, record = job(p, "analyze")
    result = workflow.run_analyze(id, record)
    expected = {c["id"] for c in db.all("SELECT * FROM chunks")}
    assert set(c for batch in calls for c in batch) == expected
    assert len(calls) > 2 and result["ai_completed"]
    cp = db.one("SELECT * FROM jobs WHERE id=?", (id,))["checkpoint"]
    calls.clear()
    retry_id, retry = job(p, "analyze", cp)
    workflow.run_analyze(retry_id, retry)
    assert calls == []
    assert all(r["verified"] for r in db.all("SELECT * FROM requirements"))


def test_invalid_source_quote_rejects_whole_batch(database, monkeypatch):
    p = make_project()
    document(database, "系统必须支持电子档案检索。", p)
    chunk = db.one("SELECT * FROM chunks")
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", lambda *a, **k: {"requirements": [{"chunk_id": chunk["id"], "quote": "这个要求根本不在原文里面"}]})
    id, record = job(p, "analyze")
    with pytest.raises(provider.ProviderError, match="原文校验"):
        workflow.run_analyze(id, record)
    assert db.all("SELECT * FROM requirements") == []
    assert db.one("SELECT * FROM jobs WHERE id=?", (id,))["checkpoint"]["batches"] == {}


def test_extraction_corrects_only_unique_exact_location(database, monkeypatch):
    p = make_project()
    document(database, "系统必须支持电子档案检索。\n供应商必须提供三年质保服务。", p)
    chunks = db.all("SELECT c.*,d.name AS document_name FROM chunks c JOIN documents d ON c.document_id=d.id ORDER BY ordinal")
    calls = []
    def model(*args, **kwargs):
        calls.append(1)
        return {"requirements": [{"chunk_id": "copied-wrong-id", "quote": chunks[-1]["text"], "text": "质保服务"}]}
    monkeypatch.setattr(provider, "chat_json", model)
    id, _ = job(p, "analyze")
    items, _ = workflow._extract_validated_batch(id, chunks, "unique")
    assert len(calls) == 1 and items[0]["chunk_id"] == chunks[-1]["id"]
    diagnostic = json.loads((db.DATA / "diagnostics" / "extraction" / id / "unique-initial.json").read_text(encoding="utf-8"))
    assert diagnostic["response"]["location_corrections"][0]["old_chunk_id"] == "copied-wrong-id"


def test_extraction_ambiguous_or_non_exact_source_is_never_guessed(database):
    batch = [{"id": "a", "text": "系统必须支持电子档案检索。"}, {"id": "b", "text": "系统必须支持电子档案检索。"}]
    problems, corrections = workflow._extraction_problems([{"chunk_id": [], "quote": batch[0]["text"]}], batch)
    assert len(problems) == 1 and corrections == []
    problems, corrections = workflow._extraction_problems([{"chunk_id": "a", "quote": "系统 必须支持电子档案检索。"}], batch)
    assert len(problems) == 1 and corrections == []


@pytest.mark.parametrize("space", ["\u00a0", "   ", "\t\n ", "\u3000"])
def test_extraction_restores_exact_whitespace_span_with_position_mapping(database, space):
    quote = "安全性要求： 支持200并发用户和SSL 加密。"
    original = f"安全性要求：{space}支持200并发用户和SSL{space}加密。"
    source = {"id": "source", "text": "前文。" + original + "后文。"}
    item = {"chunk_id": "source", "quote": quote, "text": "原始语义不改"}
    problems, corrections = workflow._extraction_problems([item], [source])
    assert not problems and len(corrections) == 1
    assert item["quote"] == original and item["text"] == "原始语义不改"
    assert source["text"][corrections[0]["source_start"]:corrections[0]["source_end"]] == item["quote"]
    assert corrections[0]["kind"] == "formatting_whitespace"
    assert workflow._extraction_problems([item], [source], correct_location=False) == ([], [])


@pytest.mark.parametrize("source,quote", [("系统须支持100 0并发用户", "系统须支持1000并发用户"),
                                          ("系统支持Micro service模式", "系统支持Microservice模式"),
                                          ("本项目要求200并发用户", "本项目要求20并发用户"),
                                          ("支持7×24小时运行", "支持7*24小时运行"),
                                          ("系统要求：支持检索", "系统要求:支持检索")])
def test_whitespace_recovery_never_removes_separators_or_changes_facts(database, source, quote):
    item = {"chunk_id": "a", "quote": quote}
    problems, corrections = workflow._extraction_problems([item], [{"id": "a", "text": source}])
    assert len(problems) == 1 and not corrections and item["quote"] == quote


def test_whitespace_recovery_rejects_ambiguous_occurrences(database):
    source = {"id": "a", "text": "系统要求：\u00a0支持检索。系统要求：  支持检索。"}
    item = {"chunk_id": "a", "quote": "系统要求： 支持检索。"}
    problems, corrections = workflow._extraction_problems([item], [source])
    assert len(problems) == 1 and not corrections


def test_short_neutral_headings_are_preserved_as_context_not_requirements(database, monkeypatch):
    p = make_project()
    document(database, "30.评审", p, "标题.txt")
    document(database, "系统必须支持电子档案检索。", p, "要求.txt")
    chunks = db.all("SELECT * FROM chunks ORDER BY ordinal")
    def model(*args, **kwargs):
        return {"requirements": [{"chunk_id": c["id"], "quote": c["text"], "text": "评审。" if c["text"] == "30.评审" else c["text"],
                                  "category": "other" if c["text"] == "30.评审" else "technical", "mandatory": c["text"] != "30.评审", "score": None} for c in chunks]}
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", model)
    id, record = job(p, "analyze")
    workflow.run_analyze(id, record)
    assert len(db.all("SELECT * FROM requirements")) == 1
    cp = db.one("SELECT * FROM jobs WHERE id=?", (id,))["checkpoint"]
    contexts = [c for batch in cp["batches"].values() for c in batch["structural_context"]]
    assert len(contexts) == 1 and contexts[0]["item"]["text"] == "评审。"
    assert contexts[0]["quote"] == "30.评审" and contexts[0]["locator"]


@pytest.mark.parametrize("quote,text,mandatory,score", [("不得分包", "不得分包", False, None), ("30.评审", "评审必须达标", False, None),
                                                       ("30.评审", "评审。", True, None), ("30.评审", "评审。", False, "5分")])
def test_short_actionable_or_changed_items_are_not_classified_as_headings(database, quote, text, mandatory, score):
    source = {"id": "a", "text": quote}
    item = {"chunk_id": "a", "quote": quote, "text": text, "category": "other", "mandatory": mandatory, "score": score}
    requirements, context = workflow._partition_structural_headings([item], [source])
    assert requirements == [item] and not context
    problems, _ = workflow._extraction_problems(requirements, [source])
    assert not problems  # Complete short blocks remain literal requirements.


def test_short_complete_table_row_keeps_requirement_and_adjacent_source_context(database, monkeypatch):
    from docx import Document
    p = make_project()
    path = database / "短表格招标.docx"
    document_file = Document()
    table = document_file.add_table(rows=3, cols=3)
    for row, values in zip(table.rows, [("一级分类", "二级分类", "功能要求"), ("", "登录与安全", "账号登录"), ("", "", "SSO")]):
        for cell, value in zip(row.cells, values):
            cell.text = value
    document_file.save(path)
    doc = workflow.ingest(path, p)
    source = next(c for c in db.all("SELECT * FROM chunks WHERE document_id=?", (doc["id"],)) if c["text"].endswith("SSO"))
    assert len(workflow._normal(source["text"])) < 6 and source["kind"] == "table_row"
    def model(system, prompt, cancel=None):
        rows = json.loads(prompt.split("文档数据：\n")[1])
        emitted = next(c for c in rows if c["chunk_id"] == source["id"])
        assert len(emitted["source_context"]["table_context"]) == 2
        return {"requirements": [{"chunk_id": source["id"], "quote": source["text"], "text": "登录与安全需支持SSO。", "category": "security", "mandatory": True}]}
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", model)
    id, record = job(p, "analyze")
    workflow.run_analyze(id, record)
    requirement = db.one("SELECT * FROM requirements")
    assert requirement["quote"] == source["text"] and requirement["mandatory"] == 1 and requirement["verified"] == 1
    cp = db.one("SELECT * FROM jobs WHERE id=?", (id,))["checkpoint"]
    contexts = [x for b in cp["batches"].values() for x in b["source_context"]]
    assert len(contexts) == 1 and contexts[0]["quote"] == source["text"]
    assert [x["relation"] for x in contexts[0]["table_context"]] == ["表格首行", "上一表格行"]


@pytest.mark.parametrize("kind,text,quote", [("paragraph", "地址：请填写具体地址", "地址："), ("table_row", "|  | SSO", "SSO"),
                                           ("table_row", "|  | A", "|  | A"), ("table_row", "|  | --", "|  | --")])
def test_short_quote_never_accepts_partial_or_empty_source(database, kind, text, quote):
    source = {"id": "a", "kind": kind, "text": text, "metadata": {"table": {"index": 1, "row": 2}}}
    assert not workflow._valid_source_quote(source, quote)
    problems, _ = workflow._extraction_problems([{"chunk_id": "a", "quote": quote}], [source])
    assert len(problems) == 1


@pytest.mark.parametrize("quote", ["资格文件", "年    月    日", "响 应 函", "地址：", "邮政编码：", "电话：", "电子邮箱：", "SSO", "不得分包"])
def test_complete_short_source_fields_preserve_requirements(database, quote):
    p = make_project()
    doc = document(database, quote, p)
    source = db.one("SELECT * FROM chunks WHERE document_id=?", (doc["id"],))
    item = {"chunk_id": source["id"], "quote": source["text"], "text": "此原始字段须按招标格式响应", "category": "format", "mandatory": True}
    assert not workflow._extraction_problems([item], [source])[0]
    id = workflow._save_requirement(p, source, item, "ai")
    row = db.one("SELECT * FROM requirements WHERE id=?", (id,))
    assert row["quote"] == source["text"] and row["mandatory"] == 1


def test_short_paragraph_context_preserves_previous_and_next_sources(database):
    p = make_project()
    doc = document(database, "填写联系人信息\n\n地址：\n\n请填写投标人详细地址", p)
    source = next(c for c in db.all("SELECT * FROM chunks WHERE document_id=? ORDER BY ordinal", (doc["id"],)) if c["text"] == "地址：")
    context = workflow._short_source_context(source)
    assert context["type"] == "short_original_block"
    assert [c["quote"] for c in context["neighbor_context"]] == ["填写联系人信息", "请填写投标人详细地址"]
    assert all(c["chunk_id"] and c["locator"] for c in context["neighbor_context"])


def test_model_explicit_whole_chunk_repair_preserves_semantics_and_is_cached(database, monkeypatch):
    p = make_project()
    doc = document(database, "供应商须承诺在信用中国（www.example.com）没有不良记录。", p)
    source = db.one("SELECT c.*,d.name AS document_name FROM chunks c JOIN documents d ON c.document_id=d.id WHERE d.id=?", (doc["id"],))
    original_text = "供应商在信用中国（网址：www.example.com）没有不良记录的承诺要求"
    calls = []
    def model(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return {"requirements": [{"chunk_id": source["id"], "quote": "供应商须承诺在信用中国（网址：www.example.com）没有不良记录。", "text": original_text}]}
        return {"repairs": [{"index": 0, "chunk_id": source["id"], "whole_chunk": True}]}
    monkeypatch.setattr(provider, "chat_json", model)
    id, _ = job(p, "analyze")
    items, _ = workflow._extract_validated_batch(id, [source], "whole-chunk")
    assert len(calls) == 2 and items[0]["quote"] == source["text"] and items[0]["text"] == original_text
    diagnostic = json.loads((db.DATA / "diagnostics" / "extraction" / id / "whole-chunk-repair-1.json").read_text(encoding="utf-8"))
    assert diagnostic["response"]["whole_chunk_selections"][0]["original_item"]["text"] == original_text
    retry_id, _ = job(p, "analyze")
    replay, usage = workflow._extract_validated_batch(retry_id, [source], "whole-chunk")
    assert len(calls) == 2 and usage["cached_response"] and replay == items


def test_prior_partial_repair_is_reused_only_for_same_source_and_semantic_item(database, monkeypatch):
    p = make_project()
    doc = document(database, "供应商须提供真实信用证明，并承诺信息完整。", p)
    source = db.one("SELECT c.*,d.name AS document_name FROM chunks c JOIN documents d ON c.document_id=d.id WHERE d.id=?", (doc["id"],))
    original = {"chunk_id": source["id"], "quote": "供应商须提供错误抄写的信用证明。", "text": "要求提供真实信用证明", "category": "qualification"}
    old_id, _ = job(p, "analyze")
    workflow._extraction_diagnostic(old_id, "prior-repair", "initial", [source], {"requirements": [original]})
    workflow._extraction_diagnostic(old_id, "prior-repair", "repair-1", [source], {"coverage_valid": True,
        "repair_response": {"repairs": [{"index": 0, "chunk_id": source["id"], "quote": source["text"]}]}})
    id, _ = job(p, "analyze")
    restored = workflow._recover_prior_provenance_repairs(id, [source], "prior-repair", {"requirements": [original]})
    assert restored["requirements"][0]["quote"] == source["text"]
    assert restored["requirements"][0]["text"] == original["text"]
    changed_item = {**original, "text": "含不同义务的新要求"}
    assert workflow._recover_prior_provenance_repairs(id, [source], "prior-repair", {"requirements": [changed_item]})["requirements"] == [changed_item]
    changed_source = {**source, "text": source["text"] + "新条款"}
    assert workflow._recover_prior_provenance_repairs(id, [changed_source], "prior-repair", {"requirements": [original]})["requirements"] == [original]


@pytest.mark.parametrize("choice", [{"chunk_id": "outside-batch", "whole_chunk": True}, {"chunk_id": "source", "whole_chunk": "true"}])
def test_whole_chunk_repair_requires_explicit_boolean_and_in_batch_id(database, monkeypatch, choice):
    p = make_project()
    doc = document(database, "供应商必须提供有效的信用记录证明。", p)
    source = db.one("SELECT c.*,d.name AS document_name FROM chunks c JOIN documents d ON c.document_id=d.id WHERE d.id=?", (doc["id"],))
    calls = []
    def model(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return {"requirements": [{"chunk_id": source["id"], "quote": "供应商必须提供编造出来的证明材料。"}]}
        row = {"index": 0, **choice}
        if row["chunk_id"] == "source":
            row["chunk_id"] = source["id"]
        return {"repairs": [row]}
    monkeypatch.setattr(provider, "chat_json", model)
    id, _ = job(p, "analyze")
    with pytest.raises(provider.ProviderError, match="最多2次修复"):
        workflow._extract_validated_batch(id, [source], "invalid-whole-chunk")
    assert len(calls) == 3


def test_saved_failed_batch_is_revalidated_on_retry_without_paid_extraction(database, monkeypatch):
    p = make_project()
    document(database, "30.评审", p, "标题.txt")
    document(database, "系统必须支持电子档案检索。", p, "要求.txt")
    batch = db.all("SELECT c.*,d.name AS document_name FROM chunks c JOIN documents d ON c.document_id=d.id ORDER BY d.created_at,c.ordinal")
    key = __import__("hashlib").sha256("|".join(c["id"] for c in batch).encode()).hexdigest()
    rows = [{"chunk_id": c["id"], "quote": c["text"], "text": "评审。" if c["text"] == "30.评审" else c["text"],
             "category": "other" if c["text"] == "30.评审" else "technical", "mandatory": c["text"] != "30.评审", "score": None} for c in batch]
    old_id, _ = job(p, "analyze")
    workflow._extraction_diagnostic(old_id, key, "initial", batch, {"requirements": rows, "usage": {"total_tokens": 123}})
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", lambda *a, **k: pytest.fail("saved extraction must not be billed again"))
    retry_id, retry = job(p, "analyze")
    workflow.run_analyze(retry_id, retry)
    cp = db.one("SELECT * FROM jobs WHERE id=?", (retry_id,))["checkpoint"]
    assert cp["batches"][key]["usage"]["cached_response"]
    assert len(cp["batches"][key]["structural_context"]) == 1
    assert len(db.all("SELECT * FROM requirements")) == 1
    changed = [{**c, "text": c["text"] + "已变更"} for c in batch]
    assert workflow._cached_extraction_response(retry_id, changed, key)[0] is None


def test_extraction_repairs_exact_quote_and_preserves_every_original_row(database, monkeypatch):
    p = make_project()
    document(database, "供应商必须提供质量保障，服务期限为三年。", p)
    chunk = db.one("SELECT * FROM chunks")
    calls = []
    def model(system, prompt, **kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            return {"requirements": [{"chunk_id": chunk["id"], "quote": "供应商必须提供质量保障", "text": "有效条款", "mandatory": True},
                                      {"chunk_id": chunk["id"], "quote": "供应商必须提供质量保障，质保服务期限为三年。", "text": "原始三年服务要求", "mandatory": True}]}
        assert "原始三年服务要求" in prompt
        return {"repairs": [{"index": 1, "chunk_id": chunk["id"], "quote": chunk["text"], "text": "恶意修改原始含义"}]}
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", model)
    id, record = job(p, "analyze")
    workflow.run_analyze(id, record)
    rows = db.all("SELECT * FROM requirements")
    assert len(calls) == 2 and len(rows) == 2
    assert {r["text"] for r in rows} == {"有效条款", "原始三年服务要求"}
    assert all(r["quote"] in chunk["text"] for r in rows)


@pytest.mark.parametrize("repair", [{"repairs": []}, {"repairs": [{"index": 0, "unresolved": True, "reason": "找不到原文"}]},
                                    {"repairs": [{"index": 0, "chunk_id": "bad-id", "quote": "仍然是虚构原文"}]}])
def test_extraction_repairs_are_bounded_and_never_drop_failed_rows(database, monkeypatch, repair):
    p = make_project()
    document(database, "系统必须支持电子档案检索。", p)
    chunk = db.one("SELECT * FROM chunks")
    calls = []
    def model(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return {"requirements": [{"chunk_id": chunk["id"], "quote": "完全不存在的原文内容"}]}
        return repair
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", model)
    id, record = job(p, "analyze")
    with pytest.raises(provider.ProviderError, match="最多2次修复"):
        workflow.run_analyze(id, record)
    assert len(calls) == 3
    assert not db.all("SELECT * FROM requirements")
    assert not db.one("SELECT * FROM jobs WHERE id=?", (id,))["checkpoint"]["batches"]
    assert len(list((db.DATA / "diagnostics" / "extraction" / id).glob("*.json"))) == 3


def test_truncated_extraction_splits_without_accepting_partial_output(database, monkeypatch):
    p = make_project()
    document(database, "系统必须支持电子档案检索。", p, "采购要求.txt")
    document(database, "供应商必须提供三年质保服务。", p, "服务要求.txt")
    calls = []
    def model(system, prompt, **kwargs):
        sources = json.loads(prompt.split("文档数据：\n")[1])
        calls.append([c["chunk_id"] for c in sources])
        if len(calls) == 1:
            raise provider.OutputTruncatedError('{"requirements":[{"quote":"不完整不得入库"}', {"completion_tokens": 1})
        return {"requirements": [{"chunk_id": c["chunk_id"], "quote": c["text"], "text": c["text"]} for c in sources]}
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", model)
    id, record = job(p, "analyze")
    workflow.run_analyze(id, record)
    assert len(calls) == 3 and set(calls[0]) == set(calls[1] + calls[2])
    rows = db.all("SELECT * FROM requirements")
    assert len(rows) == len(calls[0])
    assert all("不完整不得入库" not in r["quote"] for r in rows)


def test_review_numbers_and_reference_expiry_block_final(database):
    p, tender, enterprise, req, sec, evidence = seed_review(database)
    assert [c["code"] for c in workflow.review_project(p) if c["severity"] == "error"] == ["model_review_missing"]
    db.update("sections", sec, {"content": f"承诺在999小时完成。[E:{evidence['id']}]"})
    assert any(c["code"] == "unverified_numbers" for c in workflow.review_project(p))
    with pytest.raises(ValueError, match="正式导出"):
        workflow.export_project(p, "md", True)
    db.update("documents", enterprise["id"], {"valid_until": "2000-01-01"})
    codes = {c["code"] for c in workflow.review_project(p)}
    assert "invalid_evidence" in codes and "broken_citation" in codes


def test_tender_number_cannot_substantiate_bidder_response(database):
    p, tender, enterprise, req, sec, evidence = seed_review(database)
    db.update("requirements", req, {"text": "要求支持200并发用户", "response": "满足200并发用户"})
    db.update("chunks", evidence["id"], {"text": "本企业产品支持20并发用户"})
    db.update("sections", sec, {"content": f"本系统满足200并发用户。[E:{evidence['id']}]"})
    issue = next(c for c in workflow.review_project(p) if c["code"] == "unverified_numbers")
    assert any(x.get("section_id") == sec for x in issue["details"]["items"])
    assert any(x.get("requirement_id") == req for x in issue["details"]["items"])


@pytest.mark.parametrize("source,claim", [("1200并发用户", "200并发用户"), ("120小时", "20小时"), ("15.20元", "5.20元")])
def test_numeric_claims_cannot_match_inside_larger_evidence_numbers(database, source, claim):
    p, tender, enterprise, req, sec, evidence = seed_review(database)
    db.update("chunks", evidence["id"], {"text": "企业原文明确数值为" + source})
    db.update("requirements", req, {"response": "本项目承诺" + claim})
    db.update("sections", sec, {"content": f"本项目承诺{claim}。[E:{evidence['id']}]"})
    issue = next(c for c in workflow.review_project(p) if c["code"] == "unverified_numbers")
    assert {x["value"] for x in issue["details"]["items"]} == {claim}
    assert len(issue["details"]["items"]) == 2
    assert workflow.number_key("100.00元") == workflow.number_key("100元")


def test_curated_provenance_does_not_consume_prompt_excerpt_or_prove_numbers(database, monkeypatch):
    import hashlib
    p, tender, enterprise, req, sec, evidence = seed_review(database)
    body = "示例会计电子档案系统支持电子档案检索和权限管理，支持20并发用户。"
    wrapper = "来源备注200并发用户，仅为定位标签。" + "重复来源信息" * 160 + "\n原文：\n" + body
    metadata = {"evidence_kind": "curated_extract", "source_date": "2025-01-01", "limitations": "不可外推本项目承诺",
                "audited_excerpts": [{"quote": body, "sha256": hashlib.sha256(body.encode()).hexdigest(), "locator": "正文产品说明"}]}
    db.update("documents", enterprise["id"], {"metadata": metadata, "updated_at": db.now()})
    db.update("chunks", evidence["id"], {"text": wrapper})
    db.update("requirements", req, {"response": "本项目满足200并发用户"})
    db.update("sections", sec, {"content": f"本项目满足200并发用户。[E:{evidence['id']}]"})
    prompt, _, _ = workflow._prepare_generation_request(workflow.project(p), {"title": "功能", "requirements": [db.one("SELECT * FROM requirements WHERE id=?", (req,))]})
    data = json.loads(prompt.split("\n证据数据：\n")[1])
    assert data[0]["text"] == body and "重复来源信息" not in prompt
    assert data[0]["source_constraints"]["source_date"] == "2025-01-01"
    issue = next(c for c in workflow.review_project(p) if c["code"] == "unverified_numbers")
    assert len(issue["details"]["items"]) == 2
    def model(system, prompt, cancel=None):
        targets = json.loads(prompt.split("审核目标数据：\n")[1])
        for target in targets:
            assert target["evidence"][0]["text"] == body
            assert target["evidence"][0]["source_constraints"]["limitations"] == "不可外推本项目承诺"
        return review_result(prompt, "uncertain")
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", model)
    id, record = job(p, "review")
    workflow.run_review(id, record)


def test_project_metadata_conflicts_require_manual_resolution_and_preserve_it(database):
    p = make_project()
    document(database, "项目编号：ZJZT-2026-001\n采购人：浙江测试有限公司\n投标截止时间：2026年10月20日14:00", p, "原招标.txt")
    with TestClient(app) as client:
        detail = client.get(f"/api/projects/{p}").json()["project"]
        assert detail["project_number"] == "ZJZT-2026-001"
        assert detail["buyer"] == "浙江测试有限公司"
        assert detail["deadline"] == "2026年10月20日14:00"
        assert detail["metadata"]["field_sources"]["project_number"][0]["locator"]
        document(database, "项目编号：ZJZT-2026-002", p, "补遗.txt")
        detail = client.get(f"/api/projects/{p}").json()["project"]
        assert detail["project_number"] == ""
        assert detail["metadata"]["field_conflicts"]["project_number"] == ["ZJZT-2026-001", "ZJZT-2026-002"]
        assert "project_field_conflicts" in {x["code"] for x in workflow.review_project(p)}
        updated = client.patch(f"/api/projects/{p}", json={"project_number": "ZJZT-2026-002"})
        assert updated.status_code == 200
        document(database, "招标编号：ZJZT-2026-003", p, "追加说明.txt")
        detail = client.get(f"/api/projects/{p}").json()["project"]
        assert detail["project_number"] == "ZJZT-2026-002"
        assert detail["metadata"]["field_overrides"]["project_number"]["confirmed_at"]
        assert "project_field_conflicts" not in {x["code"] for x in workflow.review_project(p)}


def test_missing_project_basics_are_visible_and_block_final_export(database):
    p, tender, enterprise, req, sec, evidence = seed_review(database)
    with TestClient(app) as client:
        response = client.patch(f"/api/projects/{p}", json={"project_number": "", "buyer": ""})
        assert response.status_code == 200
        checks = client.get(f"/api/projects/{p}").json()["checks"]
        missing = next(c for c in checks if c["code"] == "project_basics_missing")
        assert missing["severity"] == "error"
        assert set(missing["details"]["fields"]) == {"project_number", "buyer"}
        assert "项目信息" in missing["message"]
        result = client.post(f"/api/projects/{p}/export", json={"format": "md", "final": True})
        assert result.status_code == 400 and "项目基本信息缺少" in result.json()["detail"]
        result = client.patch(f"/api/projects/{p}", json={"project_number": "TEST-001", "buyer": "测试采购人"})
        assert result.status_code == 200
        assert "project_basics_missing" not in {c["code"] for c in workflow.review_project(p)}


def test_actual_tender_buyer_cross_reference_and_response_deadline(database):
    p = make_project()
    document(database, "采购人：示例采购单位有限公司\n采购人 | 采购人名称：示例采购单位有限公司\n2.1采购人：见 磋商须知前附表\n2.响应截止时间：2026年7月20日9时30分", p, "合成字段样例.txt")
    result = workflow.refresh_project_basics(p)
    assert result["buyer"] == "示例采购单位有限公司"
    assert result["deadline"] == "2026年7月20日9时30分"
    assert "buyer" not in result["metadata"]["field_conflicts"]
    sources = result["metadata"]["field_sources"]["buyer"]
    assert {s["value"] for s in sources} == {"示例采购单位有限公司"}
    assert any("采购人名称：" in s["quote"] for s in sources)
    metadata = result["metadata"]
    metadata["basics_fingerprint"] = "legacy-parser-fingerprint"
    metadata["field_overrides"] = {"deadline": {"value": "人工核定时间", "confirmed_at": db.now()}}
    db.update("projects", p, {"buyer": "", "deadline": "人工核定时间", "metadata": metadata})
    result = workflow.refresh_project_basics(p)
    assert result["buyer"] == "示例采购单位有限公司"
    assert result["deadline"] == "人工核定时间"
    assert result["metadata"]["basics_fingerprint"] != "legacy-parser-fingerprint"


def test_project_quotation_validation_export_and_review_invalidation(database, monkeypatch):
    from app import documents
    p, tender, enterprise, req, sec, evidence = seed_review(database)
    db.update("requirements", req, {"category": "pricing", "response": "含税报价100.00元", "evidence_ids": []})
    db.update("sections", sec, {"title": "报价说明", "content": "本项目含税报价100元", "evidence_ids": []})
    quote = {"confirmed": True, "total_including_tax": "100.00", "items": [{"name": "软件许可", "quantity": "2", "unit_price": "50.00", "subtotal": "100.00", "note": ""}]}
    with TestClient(app) as client:
        bad = {**quote, "total_including_tax": "101.00"}
        assert client.patch(f"/api/projects/{p}", json={"quotation": bad}).status_code == 400
        draft = client.patch(f"/api/projects/{p}", json={"quotation": {**bad, "confirmed": False}})
        assert draft.status_code == 200 and draft.json()["project"]["quotation"]["issues"]
        before = workflow.review_fingerprint(p)
        update = client.patch(f"/api/projects/{p}", json={"quotation": quote, "project_number": "TEST-001", "buyer": "测试采购人"})
        assert update.status_code == 200
        confirmed = update.json()["project"]["quotation"]
        assert confirmed["confirmed"] and confirmed["confirmed_at"] and not confirmed["issues"]
        assert before != workflow.review_fingerprint(p)
        assert db.one("SELECT * FROM requirements WHERE id=?", (req,))["status"] == "drafted"
        assert db.one("SELECT * FROM sections WHERE id=?", (sec,))["status"] == "draft"
        assert client.patch(f"/api/requirements/{req}", json={"status": "confirmed"}).status_code == 200
        codes = {x["code"] for x in workflow.review_project(p)}
        assert "unverified_numbers" not in codes and "confirmed_without_evidence" not in codes
        captures = []

        def compose(project, requirements, sections, output, company=None, final=False):
            captures.append((project, company))
            Path(output).write_bytes(b"test export plumbing")
            return {"warnings": []}

        monkeypatch.setattr(documents, "compose_bid_package", compose)
        result = client.post(f"/api/projects/{p}/export", json={"format": "zip", "final": False})
        assert result.status_code == 200
        assert captures[0][0]["project_number"] == "TEST-001"
        assert captures[0][1]["quotation"]["total_including_tax"] == "100.00"
        assert "path" not in result.json()


def test_quotation_item_notes_are_not_product_numeric_evidence(database):
    p, tender, enterprise, req, sec, evidence = seed_review(database)
    db.update("requirements", req, {"category": "pricing", "response": "支持200并发用户，含税报价100元", "evidence_ids": []})
    db.update("sections", sec, {"title": "报价说明", "content": "支持200并发用户，含税报价100元", "evidence_ids": []})
    with TestClient(app) as client:
        quote = {"confirmed": True, "total_including_tax": "100", "items": [{"name": "200并发用户软件", "quantity": "1", "unit_price": "100", "subtotal": "100", "note": "支持200并发用户"}]}
        assert client.patch(f"/api/projects/{p}", json={"quotation": quote}).status_code == 200
        issue = next(c for c in workflow.review_project(p) if c["code"] == "unverified_numbers")
        assert {x["value"] for x in issue["details"]["items"]} == {"200并发用户"}


def test_generation_checkpoint_changes_with_product_domain(database, monkeypatch):
    p, tender, enterprise, req, sec, evidence = seed_review(database)
    approve(enterprise, "general")
    # Directory planning precedes writing. Keep the existing real section ID;
    # this test exercises generation cache invalidation, not directory creation.
    db.update("sections", sec, {"content": "", "status": "draft", "evidence_ids": [], "user_edited": 0,
        "outline_group_id": p + "-technical", "outline_group_title": "技术方案"})
    db.update("requirements", req, {"status": "pending", "response": "", "evidence_ids": []})
    calls = []
    monkeypatch.setattr(provider, "key_configured", lambda: True)

    def model(system, prompt, cancel=None):
        calls.append(prompt)
        return {"content": f"支持检索。[E:{evidence['id']}]", "responses": [{"requirement_id": req, "response": f"支持检索。[E:{evidence['id']}]", "evidence_ids": [evidence["id"]], "gap": False}]}

    monkeypatch.setattr(provider, "chat_json", model)
    id, record = job(p, "generate")
    workflow.run_generate(id, record)
    cp = db.one("SELECT * FROM jobs WHERE id=?", (id,))["checkpoint"]
    retry_id, retry = job(p, "generate", cp)
    workflow.run_generate(retry_id, retry)
    assert len(calls) == 1
    db.update("projects", p, {"domain": "expense"})
    next_id, next_record = job(p, "generate", cp)
    workflow.run_generate(next_id, next_record)
    assert len(calls) == 2
    assert "产品方向：费控系统" in calls[-1]


def seed_generation_chapters(database, count=37, *, planned=False):
    p = make_project()
    sentences = [f"第{i}项：系统必须支持电子档案检索和权限管理。" for i in range(count)]
    tender = document(database, "\n".join(sentences), p, "多章节招标.txt")
    source = db.one("SELECT * FROM chunks WHERE document_id=?", (tender["id"],))
    for sentence in sentences:
        workflow._save_requirement(p, source, {"quote": sentence, "text": sentence, "category": "technical"}, "ai")
    enterprise = document(database, "示例会计电子档案系统支持电子档案检索和权限管理。", name="企业产品.txt")
    approve(enterprise)
    evidence = db.one("SELECT * FROM chunks WHERE document_id=?", (enterprise["id"],))
    db.touch(p, analysis_status="complete", analysis_fingerprint=workflow.fingerprint(p))
    if planned:
        # Synthetic, already-planned H2. Never bypass the production readiness
        # guard; sibling/concurrency tests still build their own logical leaves.
        requirements = db.all("SELECT * FROM requirements WHERE project_id=? ORDER BY created_at,id", (p,))
        db.insert("sections", {"id": p + "-planned", "project_id": p, "ordinal": 0,
            "title": "检索与权限方案", "outline_group_id": p + "-technical", "outline_group_title": "技术方案",
            "requirement_ids": [row["id"] for row in requirements], "content": "", "status": "draft",
            "evidence_ids": [], "user_edited": 0, "created_at": db.now(), "updated_at": db.now()})
    return p, evidence


def seed_two_logical_generation_leaves(project_id):
    """Two fixed topics, with transport batches contained inside each leaf."""
    reqs=db.all('SELECT * FROM requirements WHERE project_id=? ORDER BY category,created_at,id',(project_id,))
    groups=[reqs[:12],reqs[12:]]
    for ordinal,(title,rows) in enumerate(zip(('归档与检索','借阅与权限'),groups)):
        db.insert('sections',{'id':f'{project_id}-logical-{ordinal}','project_id':project_id,'ordinal':ordinal,
            'title':title,'outline_group_id':project_id+'-technical','outline_group_title':'技术与功能方案',
            'requirement_ids':[r['id'] for r in rows],'created_at':db.now(),'updated_at':db.now()})
    return groups


def generation_result(prompt, evidence):
    requirements = json.loads(prompt.split("要求数据：\n")[1].split("\n证据数据：\n")[0])
    return {"content": f"支持电子档案检索。[E:{evidence['id']}]", "responses": [
        {"requirement_id": r["id"], "response": f"支持电子档案检索。[E:{evidence['id']}]", "evidence_ids": [evidence["id"]], "gap": False}
        for r in requirements]}


def test_model_response_cache_binds_prompt_sources_and_model_without_key_logging(database, monkeypatch):
    p = make_project()
    id, _ = job(p, "generate")
    db.set_setting("api_key_encrypted", "not-for-any-log")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-test-key-not-for-log")
    calls = []
    monkeypatch.setattr(provider, "chat_json", lambda *a, **k: calls.append(1) or {"content": "original response"})
    first = model_jobs.chat_json(id, "generation", "u", provider.SYSTEM, "prompt", {"source_sha": "a"})
    repeated = model_jobs.chat_json(id, "generation", "u", provider.SYSTEM, "prompt", {"source_sha": "a"})
    assert len(calls) == 1 and repeated["_cached_response"] and first["content"] == repeated["content"]
    model_jobs.chat_json(id, "generation", "u", provider.SYSTEM, "changed prompt", {"source_sha": "a"})
    model_jobs.chat_json(id, "generation", "u", provider.SYSTEM, "prompt", {"source_sha": "b"})
    db.set_setting("model", "another-test-model")
    model_jobs.chat_json(id, "generation", "u", provider.SYSTEM, "prompt", {"source_sha": "a"})
    assert len(calls) == 4
    logged = "".join(path.read_text(encoding="utf-8") for path in db.DATA.rglob("*.json"))
    assert "not-for-any-log" not in logged and "secret-test-key" not in logged


@pytest.mark.parametrize("citation", ["[E:证据ID]", "[E: E01]", "[E:]", "[E:E01", "[ E:E01]", "[E:foo[E:E01]"])
def test_generation_rejects_all_malformed_or_unknown_citation_forms(database, citation):
    p, evidence = seed_generation_chapters(database, count=1)
    req = db.one("SELECT * FROM requirements")
    section = {"title": "技术", "requirements": [req]}
    raw = {"content": "证据陈述" + citation, "responses": [{"requirement_id": req["id"], "response": "待补充", "evidence_ids": []}]}
    with pytest.raises(provider.ProviderError):
        translated = workflow._resolve_generation_aliases(raw, {evidence["id"]: evidence})
        workflow._validate_generation_result(section, {evidence["id"]: evidence}, translated)


def test_generation_short_aliases_map_bijectively_and_do_not_guess_source_block_ids(database):
    p, evidence = seed_generation_chapters(database, count=1)
    req = db.one("SELECT * FROM requirements")
    section = {"title": "技术", "requirements": [req]}
    raw = {"content": "检索支持。[E:E01]", "responses": [{"requirement_id": req["id"], "response": "支持检索。[E:E01]", "evidence_ids": ["E01"]}]}
    translated = workflow._resolve_generation_aliases(raw, {evidence["id"]: evidence})
    assert translated["responses"][0]["evidence_ids"] == [evidence["id"]]
    assert "[E:" + evidence["id"] + "]" in translated["content"]
    workflow._validate_generation_result(section, {evidence["id"]: evidence}, translated)
    raw["content"] = "检索支持。[E:b000001]"
    with pytest.raises(provider.ProviderError, match="b000001"):
        workflow._validate_generation_result(section, {evidence["id"]: evidence}, workflow._resolve_generation_aliases(raw, {evidence["id"]: evidence}))


def test_generation_citation_repair_requires_source_quote_or_turns_claim_into_gap(database, monkeypatch):
    p, evidence = seed_generation_chapters(database, count=1)
    req = db.one("SELECT * FROM requirements")
    section = {"title": "技术", "requirements": [req]}
    prompt, sources, _ = workflow._prepare_generation_request(workflow.project(p), section)
    calls = []
    def model(system, prompt, cancel=None):
        calls.append(1)
        if len(calls) == 1:
            return {"content": "支持档案检索。[E:错误ID]", "responses": [{"requirement_id": req["id"], "response": "支持检索", "evidence_ids": ["错误ID"]}]}
        return {"content": "支持档案检索。[E:E01]", "responses": [{"requirement_id": req["id"], "response": "支持检索。[E:E01]", "evidence_ids": ["E01"]}],
                "citation_support": [{"evidence_id": "E01", "quote": evidence["text"]}]}
    monkeypatch.setattr(provider, "chat_json", model)
    id, _ = job(p, "generate")
    repaired = workflow._generate_with_repairs(id, "repair-test", section, sources, prompt)
    assert len(calls) == 2 and repaired["responses"][0]["evidence_ids"] == [evidence["id"]]
    raw_logs = "".join(path.read_text(encoding="utf-8") for path in (db.DATA / "diagnostics" / "generation" / id).glob("*.json"))
    assert "错误ID" in raw_logs and "原始" not in repaired["content"]
    # A retry reuses both paid original and repair responses rather than billing again.
    workflow._generate_with_repairs(id, "repair-test", section, sources, prompt)
    assert len(calls) == 2


def test_generation_invalid_citations_stop_after_two_repairs_and_no_rows_save(database, monkeypatch):
    p, evidence = seed_generation_chapters(database, count=1, planned=True)
    original_plan = db.all("SELECT * FROM sections ORDER BY id")
    req = db.one("SELECT * FROM requirements")
    calls = []
    def model(*args, **kwargs):
        calls.append(1)
        return {"content": "无证据能力。[E:不存在]", "responses": [{"requirement_id": req["id"], "response": "支持", "evidence_ids": ["不存在"]}]}
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", model)
    id, record = job(p, "generate")
    with pytest.raises(provider.ProviderError, match="最多2次修复"):
        workflow.run_generate(id, record)
    planned=db.all("SELECT * FROM sections")
    assert len(calls)==3 and len(planned)==1 and planned[0]['content']=='' and planned[0]['status']=='draft'
    assert db.all("SELECT * FROM sections ORDER BY id") == original_plan
    planned_id=planned[0]['id']
    assert db.one("SELECT * FROM requirements")["response"] == ""
    # The same job is replayable without new spend; a new explicit retry refreshes
    # both the original and the previously invalid repair responses.
    with pytest.raises(provider.ProviderError, match="最多2次修复"):
        workflow.run_generate(id, db.one("SELECT * FROM jobs WHERE id=?", (id,)))
    assert len(calls) == 3
    retry_id, retry = job(p, "generate")
    with pytest.raises(provider.ProviderError, match="最多2次修复"):
        workflow.run_generate(retry_id, retry)
    planned=db.all("SELECT * FROM sections")
    assert len(calls)==6 and len(planned)==1 and planned[0]['id']==planned_id and planned[0]['content']==''
    assert db.all("SELECT * FROM sections ORDER BY id") == original_plan
    assert not db.all("SELECT * FROM project_snapshots")


@pytest.mark.parametrize("fact_response", ["我方为独立法人。", "示例投标单位为独立法人，营业执照复印件已按要求提供。\n【待企业确认的承诺模板】其余条件待补充。"])
def test_unverified_qualification_records_internal_gap_without_wrapping_body(database, fact_response):
    reqs = [{"id": "facts", "title": "独立法人", "category": "qualification", "quote": "具备独立法人资格", "text": "具备独立法人资格"},
            {"id": "procedure", "title": "采购人审查", "category": "qualification", "quote": "采购人负责资格审查", "text": "采购人负责资格审查"}]
    raw = {"content": "我方所有资质有效且完全符合条件。", "responses": [
        {"requirement_id": "facts", "response": fact_response, "evidence_ids": [], "gap": False},
        {"requirement_id": "procedure", "response": "我方知悉采购人的资格审查程序。", "evidence_ids": [], "gap": False}]}
    result = workflow._qualification_templates({"title": "资格", "requirements": reqs}, raw)
    if "【待企业确认的承诺模板】" in fact_response:
        # R010 already moved this legacy internal line out of formal response.
        assert result["responses"][0]["response"] == "示例投标单位为独立法人，营业执照复印件已按要求提供。\n"
        assert any("【待企业确认的承诺模板】其余条件待补充。" in n['raw'] for n in result['_internal_notes'])
    else:
        assert result["responses"][0]["response"] == fact_response
    assert result["responses"][0]["gap"] and result["responses"][0]["gap_reason"]
    assert "【待企业确认的承诺模板】" not in result["responses"][1]["response"]
    assert not result["responses"][1]["gap"]
    assert result["content"] == "我方所有资质有效且完全符合条件。"
    # No automatic approval: the explicit unsupported fact remains an internal
    # content task; authoring must not silently replace the user's prose.
    first = json.dumps(result, ensure_ascii=False)
    assert json.dumps(workflow._qualification_templates({"title": "资格", "requirements": reqs}, result), ensure_ascii=False) == first


def test_generation_limits_inflight_to_two_and_commits_on_coordinator(database, monkeypatch):
    p, evidence = seed_generation_chapters(database)
    seed_two_logical_generation_leaves(p)
    id, record = job(p, "generate")
    coordinator = threading.get_ident()
    lock = threading.Lock()
    barrier = threading.Barrier(2, timeout=5)
    active, maximum, calls = 0, 0, 0
    def model(system, prompt, cancel=None):
        nonlocal active, maximum, calls
        assert threading.get_ident() != coordinator
        with lock:
            active += 1
            maximum = max(maximum, active)
            calls += 1
            index = calls
        if index <= 2:
            barrier.wait()
        time.sleep(0.02)
        with lock:
            active -= 1
        return generation_result(prompt, evidence)
    def track_write(original):
        def checked(*args, **kwargs):
            assert threading.get_ident() == coordinator
            return original(*args, **kwargs)
        return checked
    for method in ("update", "insert", "execute", "event"):
        monkeypatch.setattr(db, method, track_write(getattr(db, method)))
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", model)
    result = workflow.run_generate(id, record)
    assert calls == 4 and maximum == 2 and result["sections"] == 2
    sections = db.all("SELECT * FROM sections ORDER BY ordinal")
    assert [s["ordinal"] for s in sections] == [0, 1]
    assert len(db.one("SELECT * FROM jobs WHERE id=?", (id,))["checkpoint"]["sections"]) == 2


def test_generation_failure_drains_sibling_and_retry_skips_completed_chapter(database, monkeypatch):
    p, evidence = seed_generation_chapters(database)
    leaves=seed_two_logical_generation_leaves(p)
    failed_ids={r["id"] for r in leaves[0]}
    id, record = job(p, "generate")
    barrier = threading.Barrier(2, timeout=5)
    calls, lock = [], threading.Lock()
    def failing_model(system, prompt, cancel=None):
        with lock:
            index = len(calls)
            calls.append(prompt)
        if index<2:barrier.wait()
        batch=json.loads(prompt.split("要求数据：\n")[1].split("\n证据数据：\n")[0])
        if any(r['id'] in failed_ids for r in batch):
            raise provider.ProviderError("第一章节实测模拟失败")
        time.sleep(0.05)
        return generation_result(prompt, evidence)
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", failing_model)
    with pytest.raises(provider.ProviderError, match="模拟失败"):
        workflow.run_generate(id, record)
    assert len(calls)==4
    planned=db.all("SELECT * FROM sections")
    completed=[row for row in planned if row['content']]
    assert len(planned)==2 and len(completed)==1
    assert next(row for row in planned if not row['content'])['requirement_ids']==[r['id'] for r in leaves[0]]
    cp = db.one("SELECT * FROM jobs WHERE id=?", (id,))["checkpoint"]
    assert len(cp["sections"]) == 1
    saved_id, saved_content = completed[0]["id"], completed[0]["content"]
    # Loading newly added prompt/context guidance must not redo a committed chapter.
    prepare = workflow._prepare_generation_request
    def revised_prompt(project, section):
        prompt, sources, quotation = prepare(project, section)
        return "新版生成约束，已填写数值不是空白。\n" + prompt, sources, quotation
    monkeypatch.setattr(workflow, "_prepare_generation_request", revised_prompt)
    monkeypatch.setattr(workflow.tender_context, "build_context", lambda project_id: {"project_facts": [{"value": "90日", "quote": "响应有效期90日"}]})
    retry_calls = []
    def succeeding_model(system, prompt, cancel=None):
        retry_calls.append(prompt)
        return generation_result(prompt, evidence)
    monkeypatch.setattr(provider, "chat_json", succeeding_model)
    retry_id, retry = job(p, "generate", cp)
    workflow.run_generate(retry_id, retry)
    assert len(retry_calls)==1 and len(db.all("SELECT * FROM sections"))==2
    assert all("新版生成约束" in prompt and '"value": "90日"' in prompt for prompt in retry_calls)
    assert db.one("SELECT * FROM sections WHERE id=?", (saved_id,))["content"] == saved_content


def test_generation_cancel_stops_new_chapter_dispatch(database, monkeypatch):
    p, evidence = seed_generation_chapters(database)
    seed_two_logical_generation_leaves(p)
    before=db.all("SELECT * FROM sections ORDER BY id")
    id, record = job(p, "generate")
    barrier = threading.Barrier(2, timeout=5)
    stop = threading.Event()
    calls = []
    def model(system, prompt, cancel=None):
        calls.append(prompt)
        barrier.wait()
        stop.set()
        assert cancel()
        raise provider.Cancelled("模拟用户取消")
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", model)
    monkeypatch.setattr(workflow, "cancelled", lambda job_id: stop.is_set())
    with pytest.raises(provider.Cancelled):
        workflow.run_generate(id, record)
    assert len(calls)==2 and db.all("SELECT * FROM sections ORDER BY id")==before


def test_generation_preserves_edits_made_during_model_request(database, monkeypatch):
    p, evidence = seed_generation_chapters(database, count=1, planned=True)
    requirement = db.one("SELECT * FROM requirements")
    id, record = job(p, "generate")
    def model(system, prompt, cancel=None):
        db.update("requirements", requirement["id"], {"response": "用户编辑的响应", "status": "drafted", "updated_at": db.now()})
        return generation_result(prompt, evidence)
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", model)
    workflow.run_generate(id, record)
    assert db.one("SELECT * FROM requirements")["response"] == "用户编辑的响应"
    section = db.one("SELECT * FROM sections")
    second_id, second = job(p, "generate")
    # A changed model setting produces a real in-flight request, not a cache hit.
    db.set_setting("temperature", 0.3)
    def edited_section_model(system, prompt, cancel=None):
        db.update("sections", section["id"], {"content": "用户编辑的章节", "user_edited": 1, "updated_at": db.now()})
        return generation_result(prompt, evidence)
    monkeypatch.setattr(provider, "chat_json", edited_section_model)
    workflow.run_generate(second_id, second)
    assert db.one("SELECT * FROM sections")["content"] == "用户编辑的章节"


def test_independent_ai_review_coverage_and_staleness(database, monkeypatch):
    p, tender, enterprise, req, sec, evidence = seed_review(database)
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    calls = []

    def review_model(system, prompt, cancel=None):
        targets = json.loads(prompt.split("审核目标数据：\n")[1])
        calls.extend(t["target_id"] for t in targets)
        assert "招标要求不能证明企业已有能力" in prompt
        return {"assessments": [{"target_id": t["target_id"], "verdict": "supported", "reason": "对应企业原文明确支持检索与权限管理"} for t in targets]}

    monkeypatch.setattr(provider, "chat_json", review_model)
    id, record = job(p, "review")
    outcome = workflow.run_review(id, record)
    assert outcome["ai_completed"] and len(calls) == 2
    assert not [c for c in workflow.review_project(p) if c["severity"] == "error"]
    assert workflow.export_project(p, "md", True)["final"] == 1
    db.update("sections", sec, {"content": "新编辑内容"})
    assert any(c["code"] == "model_review_missing" for c in workflow.review_project(p))


def test_review_covers_737_requirements_and_65_chapters_with_source_context(database, monkeypatch):
    p, tender, enterprise, req, sec, evidence = seed_review(database)
    document(database, "17.1 | 响应有效期 | 90日（从响应截止之日算起）。", p, "有效期.txt")
    document(database, "响应保证金 | ☑不需要 □需要", p, "保证金选项.txt")
    base = db.one("SELECT * FROM requirements WHERE id=?", (req,))
    req_ids = [req] + [db.uid() for _ in range(736)]
    section_ids = [sec] + [db.uid() for _ in range(64)]
    now = db.now()
    with db.connect() as conn:
        conn.executemany("INSERT INTO requirements (id,project_id,document_id,chunk_id,title,text,quote,locator,response,status,evidence_ids,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(id, p, tender["id"], base["chunk_id"], f"要求{i}", base["text"], base["quote"], base["locator"], base["response"], "confirmed", json.dumps([evidence["id"]]), now, now)
             for i, id in enumerate(req_ids[1:])])
        conn.executemany("INSERT INTO sections (id,project_id,ordinal,title,content,requirement_ids,evidence_ids,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [(id, p, i + 1, f"章节{i + 1}", "正文" * 7600 if i == 63 else "支持检索和权限管理。", json.dumps(req_ids[(i + 1) * 11:(i + 2) * 11]), "[]", now, now)
             for i, id in enumerate(section_ids[1:])])
    context = workflow.tender_context.build_context(p)
    generation_project = workflow.project(p)
    generation_project["_tender_context"] = context
    generation_prompt, _, _ = workflow._prepare_generation_request(generation_project, {"title": "技术", "requirements": [base]})
    assert '"value": "90日（从响应截止之日算起）。"' in generation_prompt
    assert '"value": "not_required"' in generation_prompt
    seen, lock = [], threading.Lock()
    def model(system, prompt, cancel=None):
        assert '"value": "90日（从响应截止之日算起）。"' in prompt
        assert '"value": "not_required"' in prompt
        targets = json.loads(prompt.split("审核目标数据：\n")[1])
        with lock:
            seen.extend(targets)
        for target in targets:
            if target["target_type"] == "section":
                linked = target["linked_requirements"]
                assert linked["items"] and not linked["omitted_count"]
                assert all(item["quote"] == base["quote"] and item["locator"] == base["locator"] for item in linked["items"])
        return review_result(prompt)
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", model)
    id, record = job(p, "review")
    result = workflow.run_review(id, record)
    assert result["ai_completed"]
    assert {t["requirement_id"] for t in seen if t["target_type"] == "requirement"} == set(req_ids)
    assert {t["section_id"] for t in seen if t["target_type"] == "section"} == set(section_ids)
    last = sorted((t for t in seen if t.get("section_id") == section_ids[-1]), key=lambda t: t["excerpt_offset"])
    assert [t["excerpt_offset"] for t in last] == [0, 7500, 15000]
    assert "".join(t["content"] for t in last) == "正文" * 7600
    canonical_ids = {"R:" + t["requirement_id"] if t["target_type"] == "requirement" else f"S:{t['section_id']}:{t['excerpt_offset']}" for t in seen}
    assert len(canonical_ids) == len(seen) == 804
    assert db.one("SELECT * FROM reviews")["targets"] == 804
    assert "_tender_context" not in db.one("SELECT * FROM projects WHERE id=?", (p,))


def test_review_linked_context_is_bounded_and_does_not_cut_source_quotes():
    requirements = {f"r{i}": {"text": "采购要求" * 100, "quote": "原文条件" * 100, "locator": f"第{i}段"} for i in range(40)}
    result = workflow._review_linked_requirements({"requirement_ids": list(requirements)}, requirements, max_chars=2000)
    assert 0 < len(result["items"]) < 40
    assert len(result["items"]) + result["omitted_count"] == 40
    assert sum(len(json.dumps(item, ensure_ascii=False)) for item in result["items"]) <= 2000
    assert all(item["quote"] == requirements[item["requirement_id"]]["quote"] for item in result["items"])


def test_rule_review_fetches_repeated_citations_once_per_request(database, monkeypatch):
    p, evidence = seed_generation_chapters(database, count=37)
    with db.connect() as conn:
        conn.execute("UPDATE requirements SET evidence_ids=?,response=?,status='confirmed' WHERE project_id=?",
                     (json.dumps([evidence["id"]]), "支持电子档案检索", p))
    original = db.all
    citation_queries = []
    def counted(sql, params=()):
        if "WHERE c.id IN (" in sql:
            citation_queries.append(tuple(params))
        return original(sql, params)
    monkeypatch.setattr(db, "all", counted)
    checks = workflow.review_project(p)
    assert citation_queries == [(evidence["id"],)]
    assert not any(check["code"] == "invalid_evidence" for check in checks)
    db.update("documents", evidence["document_id"], {"status": "pending"})
    checks = workflow.review_project(p)
    assert citation_queries == [(evidence["id"],), (evidence["id"],)]
    invalid = next(check for check in checks if check["code"] == "invalid_evidence")
    assert len(invalid["details"]["requirement_ids"]) == 37


def review_result(prompt, verdict="supported"):
    targets = json.loads(prompt.split("审核目标数据：\n")[1])
    return {"assessments": [{"target_id": t["target_id"], "verdict": verdict, "reason": "测试逐目标审核说明"} for t in targets]}


def test_review_limits_inflight_to_two_and_checkpoints_on_coordinator(database, monkeypatch):
    p, _ = seed_generation_chapters(database, count=31)
    id, record = job(p, "review")
    coordinator, lock, barrier = threading.get_ident(), threading.Lock(), threading.Barrier(2, timeout=5)
    calls, active, maximum = 0, 0, 0
    def model(system, prompt, cancel=None):
        nonlocal calls, active, maximum
        assert threading.get_ident() != coordinator
        with lock:
            calls += 1
            index = calls
            active += 1
            maximum = max(maximum, active)
        if index <= 2:
            barrier.wait()
        time.sleep(0.02)
        with lock:
            active -= 1
        return review_result(prompt)
    def track_write(original):
        def checked(*args, **kwargs):
            assert threading.get_ident() == coordinator
            return original(*args, **kwargs)
        return checked
    for method in ("update", "insert", "execute", "event"):
        monkeypatch.setattr(db, method, track_write(getattr(db, method)))
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", model)
    result = workflow.run_review(id, record)
    assert result["ai_completed"] and calls == 4 and maximum == 2
    cp = db.one("SELECT * FROM jobs WHERE id=?", (id,))["checkpoint"]
    assert len(cp["batches"]) == 4 and sum(b["targets"] for b in cp["batches"].values()) == 31
    assert db.one("SELECT * FROM reviews")["targets"] == 31


def test_review_failed_coverage_drains_sibling_and_retry_preserves_checkpoint(database, monkeypatch):
    p, _ = seed_generation_chapters(database, count=31)
    id, record = job(p, "review")
    barrier, lock, calls, failed_target = threading.Barrier(2, timeout=5), threading.Lock(), [], []
    def model(system, prompt, cancel=None):
        first_target = json.loads(prompt.split("审核目标数据：\n")[1])[0]["requirement_id"]
        with lock:
            index = len(calls)
            calls.append(prompt)
            if index == 0:
                failed_target.append(first_target)
        if index < 2:
            barrier.wait()
        if first_target == failed_target[0]:
            return {"assessments": []}
        time.sleep(0.05)
        return review_result(prompt, "gap")
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", model)
    with pytest.raises(provider.ProviderError, match="覆盖全部目标"):
        workflow.run_review(id, record)
    # While one worker performs its bounded repairs, the other may complete
    # another batch before failure is known. Every successful batch must survive.
    assert 4 <= len(calls) <= 6 and not db.all("SELECT * FROM reviews")
    cp = db.one("SELECT * FROM jobs WHERE id=?", (id,))["checkpoint"]
    assert 1 <= len(cp["batches"]) <= 3
    saved_findings = [finding for batch in cp["batches"].values() for finding in batch["findings"]]
    assert len(saved_findings) == sum(batch["targets"] for batch in cp["batches"].values())
    retry_calls = []
    def succeeding_model(system, prompt, cancel=None):
        retry_calls.append(prompt)
        return review_result(prompt)
    monkeypatch.setattr(provider, "chat_json", succeeding_model)
    retry_id, retry = job(p, "review", cp)
    workflow.run_review(retry_id, retry)
    assert len(retry_calls) == 4 - len(cp["batches"]) and db.one("SELECT * FROM reviews")["findings"] == saved_findings


def test_review_saved_extra_duplicate_restores_without_model_and_preserves_all_verdicts(database, monkeypatch):
    p = make_project()
    batch = [{"target_id": "R:a", "target_type": "requirement", "title": "A"}, {"target_id": "R:b", "target_type": "requirement", "title": "B"}]
    prompt = workflow._review_prompt(workflow.project(p), batch)
    raw = {"assessments": [{"target_id": "R:a", "verdict": "gap", "reason": "需要补充产品证明"},
                           {"target_id": "R:b", "verdict": "uncertain", "reason": "能力待确认"},
                           {"target_id": "R:unknown", "verdict": "gap", "reason": "需要补充产品证明"}]}
    monkeypatch.setattr(provider, "chat_json", lambda *a, **k: raw)
    old_id, _ = job(p, "review")
    saved = model_jobs.chat_json(old_id, "review", "batch", provider.SYSTEM, prompt, {"batch": batch, "project_id": p})
    model_jobs.mark_invalid(old_id, "review", saved, "多余未知目标")
    monkeypatch.setattr(provider, "chat_json", lambda *a, **k: pytest.fail("完整已付费评估不得重调模型"))
    retry_id, _ = job(p, "review")
    result = workflow._review_with_repairs(retry_id, "batch", batch, prompt, p)
    assert result["assessments"] == raw["assessments"][:2]
    assert result["_cached_response"]
    logged = json.loads((db.DATA / "diagnostics" / "review" / retry_id / "batch-validated.json").read_text(encoding="utf-8"))
    assert logged["repair_attempts"] == 0 and len(logged["corrections"]) == 1
    assert logged["original_response"]["assessments"] == raw["assessments"]


def test_review_missing_target_is_actually_assessed_and_valid_rows_cannot_change(database, monkeypatch):
    p = make_project()
    batch = [{"target_id": "R:a", "target_type": "requirement", "title": "A"}, {"target_id": "R:b", "target_type": "requirement", "title": "B"}]
    prompt = workflow._review_prompt(workflow.project(p), batch)
    calls = []
    def model(system, prompt, cancel=None):
        calls.append(prompt)
        original = {"target_id": "T01", "verdict": "gap", "reason": "需要补充产品证明"}
        if len(calls) == 1:
            return {"assessments": [original]}
        if len(calls) == 2:
            return {"assessments": [{**original, "verdict": "supported"}, {"target_id": "T02", "verdict": "uncertain", "reason": "缺少性能测试"}]}
        return {"assessments": [original, {"target_id": "T02", "verdict": "uncertain", "reason": "缺少性能测试"}]}
    monkeypatch.setattr(provider, "chat_json", model)
    id, _ = job(p, "review")
    result = workflow._review_with_repairs(id, "batch", batch, prompt, p)
    assert len(calls) == 3
    assert [(row["target_id"], row["verdict"]) for row in result["assessments"]] == [("R:a", "gap"), ("R:b", "uncertain")]
    assert "修改了已有效评估" in calls[-1]
    assert len(workflow._validate_review_result(batch, result)) == 2


def test_review_repair_attempts_are_bounded_and_cache_does_not_rebill_same_job(database, monkeypatch):
    p = make_project()
    batch = [{"target_id": "R:a", "target_type": "requirement", "title": "A"}]
    prompt = workflow._review_prompt(workflow.project(p), batch)
    calls = []
    monkeypatch.setattr(provider, "chat_json", lambda *a, **k: calls.append(1) or {"assessments": []})
    id, _ = job(p, "review")
    with pytest.raises(provider.ProviderError, match="最多2次修复"):
        workflow._review_with_repairs(id, "batch", batch, prompt, p)
    assert len(calls) == 3
    with pytest.raises(provider.ProviderError, match="最多2次修复"):
        workflow._review_with_repairs(id, "batch", batch, prompt, p)
    assert len(calls) == 3
    retry_id, _ = job(p, "review")
    with pytest.raises(provider.ProviderError, match="最多2次修复"):
        workflow._review_with_repairs(retry_id, "batch", batch, prompt, p)
    assert len(calls) == 5  # paid original is replayed; only invalid repairs refresh
    assert not db.all("SELECT * FROM reviews")


def test_review_cancel_stops_dispatch_and_never_applies_complete_review(database, monkeypatch):
    p, _ = seed_generation_chapters(database, count=31)
    id, record = job(p, "review")
    barrier, stop, calls = threading.Barrier(2, timeout=5), threading.Event(), []
    def model(system, prompt, cancel=None):
        calls.append(prompt)
        barrier.wait()
        stop.set()
        assert cancel()
        raise provider.Cancelled("模拟复核取消")
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", model)
    monkeypatch.setattr(workflow, "cancelled", lambda job_id: stop.is_set())
    with pytest.raises(provider.Cancelled):
        workflow.run_review(id, record)
    assert len(calls) == 2 and not db.all("SELECT * FROM reviews")


def test_parallel_review_staleness_never_marks_changed_content_reviewed(database, monkeypatch):
    p, _ = seed_generation_chapters(database, count=11)
    id, record = job(p, "review")
    edited = threading.Event()
    req = db.one("SELECT * FROM requirements")
    def model(system, prompt, cancel=None):
        if not edited.is_set():
            edited.set()
            db.update("requirements", req["id"], {"response": "用户在审核期间修改正文", "updated_at": db.now()})
        return review_result(prompt)
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", model)
    with pytest.raises(ValueError, match="发生变化"):
        workflow.run_review(id, record)
    assert not db.all("SELECT * FROM reviews")


def test_empty_ai_extraction_with_mandatory_markers_fails(database, monkeypatch):
    p = make_project()
    document(database, "▲本项目投标有效期必须为90日。", p)
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", lambda *a, **k: {"requirements": []})
    id, record = job(p, "analyze")
    with pytest.raises(provider.ProviderError, match="漏项疑点"):
        workflow.run_analyze(id, record)
    assert workflow.project(p)["analysis_status"] != "complete"


def test_edits_and_new_tender_invalidate_approval(database):
    p, tender, enterprise, req, sec, evidence = seed_review(database)
    with TestClient(app) as client:
        response = client.patch(f"/api/requirements/{req}", json={"response": "已修订的响应内容"})
        assert response.status_code == 200
        assert response.json()["requirement"]["status"] == "drafted"
        assert db.one("SELECT * FROM sections WHERE id=?", (sec,))["status"] == "draft"
        db.update("sections", sec, {"status": "approved"})
        client.patch(f"/api/knowledge/{enterprise['id']}", json={"status": "pending"})
        assert db.one("SELECT * FROM sections WHERE id=?", (sec,))["status"] == "draft"
    document(database, "新增附件：必须提供会计档案迁移方案。", p, "补遗.txt")
    assert workflow.project(p)["analysis_status"] == "pending"


def test_generation_requires_exact_coverage_and_known_evidence(database, monkeypatch):
    p, tender, enterprise, req, sec, evidence = seed_review(database)
    db.update("sections", sec, {"content": "", "status": "draft", "evidence_ids": [], "user_edited": 0,
        "outline_group_id": p + "-technical", "outline_group_title": "技术方案"})
    original_plan = db.all("SELECT * FROM sections WHERE project_id=? ORDER BY id", (p,))
    db.update("requirements", req, {"status": "pending", "response": "", "evidence_ids": []})
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    monkeypatch.setattr(provider, "chat_json", lambda *a, **k: {"content": "伪造事实。[E:invented]", "responses": [{"requirement_id": req, "response": "支持", "evidence_ids": ["invented"]}]})
    id, record = job(p, "generate")
    with pytest.raises(provider.ProviderError, match="证据引用"):
        workflow.run_generate(id, record)
    planned=db.all("SELECT * FROM sections WHERE project_id=?",(p,))
    assert len(planned)==1 and planned[0]['content']=='' and planned[0]['status']=='draft'
    assert db.all("SELECT * FROM sections WHERE project_id=? ORDER BY id", (p,)) == original_plan
    planned_id=planned[0]['id']
    monkeypatch.setattr(provider, "chat_json", lambda *a, **k: {"content": "正文", "responses": []})
    id, record = job(p, "generate")
    with pytest.raises(provider.ProviderError, match="完整覆盖"):
        workflow.run_generate(id, record)
    assert db.all("SELECT * FROM sections WHERE project_id=? ORDER BY id", (p,)) == original_plan
    monkeypatch.setattr(provider, "chat_json", lambda *a, **k: {"content": f"支持电子档案检索。[E:{evidence['id']}]", "responses": [{"requirement_id": req, "response": f"支持检索。[E:{evidence['id']}]", "evidence_ids": [evidence["id"]], "gap": False}]})
    id, record = job(p, "generate")
    result = workflow.run_generate(id, record)
    assert result["sections"]==1 and db.one("SELECT * FROM sections WHERE project_id=?",(p,))['id']==planned_id
    assert db.one("SELECT * FROM requirements WHERE id=?", (req,))["status"] == "drafted"
    assert db.one("SELECT * FROM sections WHERE project_id=?", (p,))["status"] == "draft"


def test_interrupted_jobs_and_cancel_keep_checkpoints(database):
    p = make_project()
    id, record = job(p, "analyze", {"batches": {"finished": {"chunks": ["x"]}}})
    db.update("jobs", id, {"status": "running"})
    db.init()
    interrupted = db.one("SELECT * FROM jobs WHERE id=?", (id,))
    assert interrupted["status"] == "interrupted"
    assert "finished" in interrupted["checkpoint"]["batches"]
    db.update("jobs", id, {"cancel_requested": 1})
    with pytest.raises(provider.Cancelled):
        workflow.progress(id, 50, "test")


@pytest.mark.parametrize("failure", [provider.ProviderError("模拟失败"), provider.Cancelled("模拟取消")])
@pytest.mark.parametrize("prior_state", ["analyzing", "complete"])
def test_failed_analysis_clears_running_status_without_downgrading_complete(database, monkeypatch, failure, prior_state):
    p = make_project()
    db.touch(p, analysis_status=prior_state, analysis_fingerprint="existing-fingerprint")
    id, _ = job(p, "analyze")
    def fail(*args):
        raise failure
    monkeypatch.setattr(workflow, "run_analyze", fail)
    workflow._run(id)
    project = workflow.project(p)
    assert project["analysis_status"] == ("partial" if prior_state == "analyzing" else "complete")
    assert project["analysis_fingerprint"] == ("" if prior_state == "analyzing" else "existing-fingerprint")


def test_startup_interrupted_analysis_updates_project_but_keeps_completed_state(database):
    p = make_project()
    db.touch(p, analysis_status="analyzing", analysis_fingerprint="previous-fingerprint")
    id, _ = job(p, "analyze", {"batches": {"completed": {"chunks": ["a"]}}})
    db.update("jobs", id, {"status": "running"})
    finished = make_project()
    db.touch(finished, analysis_status="complete", analysis_fingerprint="complete-fingerprint")
    failed_id, _ = job(finished, "analyze")
    db.update("jobs", failed_id, {"status": "failed"})
    db.init()
    assert workflow.project(p)["analysis_status"] == "partial" and workflow.project(p)["analysis_fingerprint"] == ""
    assert db.one("SELECT * FROM jobs WHERE id=?", (id,))["checkpoint"]["batches"]["completed"]
    assert workflow.project(finished)["analysis_status"] == "complete"


def test_reparse_backups_and_restore_preserve_human_content(database):
    p, tender, enterprise, req, sec, evidence = seed_review(database)
    db.update("sections", sec, {"content": "人工逐字修改过的章节，不能丢失", "user_edited": 1})
    id, record = job(p, "reparse")
    record["payload"] = {"document_id": tender["id"]}
    result = workflow.run_reparse(id, record)
    assert workflow.project(p)["analysis_status"] == "pending"
    assert not db.all("SELECT * FROM requirements WHERE project_id=?", (p,))
    assert not db.all("SELECT * FROM sections WHERE project_id=?", (p,))
    snapshot = db.one("SELECT * FROM project_snapshots WHERE id=?", (result["snapshot_id"],))
    assert snapshot["payload"]["sections"][0]["content"] == "人工逐字修改过的章节，不能丢失"
    export = db.one("SELECT * FROM exports WHERE id=?", (snapshot["payload"]["backup_export"],))
    assert "人工逐字修改过的章节" in Path(export["path"]).read_text(encoding="utf-8")
    workflow.restore_snapshot(result["snapshot_id"])
    assert db.one("SELECT * FROM sections WHERE id=?", (sec,))["content"] == "人工逐字修改过的章节，不能丢失"
    assert db.one("SELECT * FROM requirements WHERE id=?", (req,))["status"] == "confirmed"


def test_reparse_failure_retains_existing_content(database, monkeypatch):
    from app import documents
    p, tender, enterprise, req, sec, evidence = seed_review(database)
    monkeypatch.setattr(documents, "parse_document", lambda *a, **k: {"blocks": []})
    id, record = job(p, "reparse")
    record["payload"] = {"document_id": tender["id"]}
    with pytest.raises(ValueError, match="现有内容已保留"):
        workflow.run_reparse(id, record)
    assert db.one("SELECT * FROM requirements WHERE id=?", (req,))
    assert db.one("SELECT * FROM sections WHERE id=?", (sec,))


def transport(monkeypatch, handler):
    cls = httpx.Client
    mock = httpx.MockTransport(handler)
    monkeypatch.setattr(provider.httpx, "Client", lambda **kwargs: cls(transport=mock, timeout=5))
    monkeypatch.setattr(provider, "get_key", lambda: "test-secret")


def sse(content='{"ok":true}', finish="stop"):
    delta = {"choices": [{"delta": {"content": content}, "finish_reason": None}]}
    last = {"choices": [{"delta": {}, "finish_reason": finish}]}
    return "data: " + json.dumps(delta) + "\n\ndata: " + json.dumps(last) + "\n\ndata: [DONE]\n\n"


def test_provider_sse_contract_and_no_normal_finish_rejected(database, monkeypatch):
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(200, text=sse())

    transport(monkeypatch, handler)
    result = provider.chat_json("system JSON", "user")
    assert result["ok"] and result['_transport_attempts'] == 1
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert requests[0]["thinking"] == {"type": "disabled"}
    assert "test-secret" not in json.dumps(requests)


@pytest.mark.parametrize("body,match", [(sse(finish=None), "没有正常结束"), (sse(finish="length"), "长度限制"), (sse(content="broken"), "JSON 无法解析")])
def test_provider_bad_streams_fail_closed(database, monkeypatch, body, match):
    transport(monkeypatch, lambda request: httpx.Response(200, text=body))
    with pytest.raises(provider.ProviderError, match=match):
        provider.chat_json("JSON", "test")


def test_provider_429_retries_and_cancel(database, monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429) if len(calls) == 1 else httpx.Response(200, text=sse())

    transport(monkeypatch, handler)
    monkeypatch.setattr(provider.time, "sleep", lambda seconds: None)
    result = provider.chat_json("JSON", "test")
    assert result["ok"] and result['_transport_attempts'] == 2
    assert len(calls) == 2
    with pytest.raises(provider.Cancelled):
        provider.chat_json("JSON", "test", cancel=lambda: True)
    assert len(calls) == 2


@pytest.mark.parametrize("error_type", [httpx.ReadError, httpx.RemoteProtocolError])
def test_partial_network_failure_is_not_automatically_rebilled(database, monkeypatch, error_type):
    calls = []

    class Partial(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"{\\"ok\\":true}"}}]}\n\n'
            raise error_type("network dropped with sensitive transport details")

    def handler(request):
        calls.append(1)
        return httpx.Response(200, stream=Partial())

    transport(monkeypatch, handler)
    id, _ = job(make_project(), "generate")
    with pytest.raises(provider.OutputInterruptedError, match="没有自动重复请求"):
        model_jobs.chat_json(id, "generation", "partial", "JSON", "test", {})
    assert len(calls) == 1
    records = [json.loads(path.read_text(encoding="utf-8")) for path in (db.DATA / "diagnostics" / "generation" / id).glob("*.json")]
    assert len(records) == 1 and records[0]["state"] == "interrupted"
    assert records[0]["partial_text"] == '{"ok":true}'
    assert "sensitive transport" not in json.dumps(records) and "test-secret" not in json.dumps(records)
    assert not list((db.DATA / "cache" / "model-responses").rglob("*.json"))


def test_protocol_failure_before_body_is_bounded_retry_and_cancellable(database, monkeypatch):
    calls = []
    recover = True
    def handler(request):
        calls.append(1)
        if recover and len(calls) == 2:
            return httpx.Response(200, text=sse())
        raise httpx.RemoteProtocolError("transport detail must not become user error")
    transport(monkeypatch, handler)
    monkeypatch.setattr(provider.time, "sleep", lambda seconds: None)
    assert provider.chat_json("JSON", "test")["ok"] and len(calls) == 2
    recover = False
    calls.clear()
    with pytest.raises(provider.ProviderError, match="最多3次连接尝试") as raised:
        provider.chat_json("JSON", "test")
    assert len(calls) == 3 and "transport detail" not in str(raised.value)
    calls.clear()
    with pytest.raises(provider.Cancelled):
        provider.chat_json("JSON", "test", cancel=lambda: bool(calls))
    assert len(calls) == 1


def test_generation_registers_visible_citations_per_response_without_mutating_raw():
    section = {"requirements": [{"id": "r1"}, {"id": "r2"}]}
    evidence = {id: {} for id in ("a", "b", "c", "d")}
    raw = {"content": "章节参考[E:c]", "responses": [
        {"requirement_id": "r1", "response": "条款一[E:a][E:a]", "evidence_ids": ["b", "b"], "gap": True},
        {"requirement_id": "r2", "response": "条款二[E:d]", "gap": False}]}
    original = json.loads(json.dumps(raw))
    _, responses, _, cited = workflow._validate_generation_result(section, evidence, raw)
    assert responses[0]["evidence_ids"] == ["b", "a"] and responses[0]["gap"] is True
    assert responses[1]["evidence_ids"] == ["d"] and responses[1]["gap"] is False
    assert cited == set(evidence) and raw == original
    for marker in ("[E:unknown]", "[E:a", "[E:证据ID]", "[E:bad id]"):
        raw["responses"][1]["response"] = "条款二" + marker
        original = json.loads(json.dumps(raw))
        with pytest.raises(provider.ProviderError):
            workflow._validate_generation_result(section, evidence, raw)
        assert raw == original


@pytest.mark.parametrize("gap", [True, False])
def test_generation_persists_visible_alias_citation_registration(database, monkeypatch, gap):
    p, evidence = seed_generation_chapters(database, count=1, planned=True)
    monkeypatch.setattr(provider, "key_configured", lambda: True)
    def model(system, prompt, cancel=None):
        result = generation_result(prompt, evidence)
        result["responses"][0].update(response="支持电子档案检索。[E:E01]", evidence_ids=[], gap=gap)
        return result
    monkeypatch.setattr(provider, "chat_json", model)
    id, record = job(p, "generate")
    workflow.run_generate(id, record)
    response = db.one("SELECT * FROM requirements WHERE project_id=?", (p,))
    assert response["evidence_ids"] == [evidence["id"]]
    assert response["status"] == ("gap" if gap else "drafted")


def test_manual_response_missing_registration_saves_draft_but_blocks_confirmation(database):
    p, _, _, req, _, e1 = seed_review(database)
    doc2 = document(database, "示例系统还支持档案检索权限设置。", name="补充企业.txt")
    approve(doc2)
    e2 = db.one("SELECT * FROM chunks WHERE document_id=?", (doc2["id"],))
    with TestClient(app) as client:
        result = client.patch(f"/api/requirements/{req}", json={"response": f"支持档案检索。[E:{e2['id']}]"})
        assert result.status_code == 200 and result.json()["requirement"]["status"] == "drafted"
        checks = workflow.review_project(p)
        assert any(c["code"] == "response_citation_mismatch" and c["severity"] == "error" for c in checks)
        assert client.patch(f"/api/requirements/{req}", json={"status": "confirmed"}).status_code == 400
        result = client.patch(f"/api/requirements/{req}", json={"status": "confirmed", "evidence_ids": [e1["id"], e2["id"]]})
        assert result.status_code == 200
        assert result.json()["requirement"]["evidence_ids"] == [e1["id"], e2["id"]]
        assert not any(c["code"] == "response_citation_mismatch" for c in workflow.review_project(p))


@pytest.mark.parametrize("marker", ["[E:证据ID]", "[E:bad id]", "[E:]", "[E:unclosed", "[ E:wrong]", "[E:foo[E:nested]"])
def test_bad_response_citations_block_confirmation_not_applicable_and_final_export(database, marker):
    p, _, _, req, _, _ = seed_review(database)
    with TestClient(app) as client:
        assert client.patch(f"/api/requirements/{req}", json={"response": "本项目此采购程序不适用于本方。" + marker}).status_code == 200
        assert client.patch(f"/api/requirements/{req}", json={"status": "confirmed"}).status_code == 400
        assert client.patch(f"/api/requirements/{req}", json={"status": "not_applicable"}).status_code == 200
    assert any(c["code"] == "response_citation_mismatch" for c in workflow.review_project(p))
    before = set((db.DATA / "exports").iterdir())
    with pytest.raises(ValueError, match="引用"):
        workflow.export_project(p, "docx", True)
    assert not db.all("SELECT * FROM exports") and set((db.DATA / "exports").iterdir()) == before
    db.update("requirements", req, {"response": "本条仅规定采购人的评审程序，本方知悉并配合。", "evidence_ids": []})
    assert not any(c["code"] == "response_citation_mismatch" for c in workflow.review_project(p))


@pytest.mark.parametrize("revocation", [{"valid_until": "2000-01-01"}, {"status": "pending"}, {"scope": "expense"}, {"parse_status": "failed"}])
def test_status_only_confirmation_revalidates_all_registered_evidence(database, revocation):
    _, _, _, req, _, e1 = seed_review(database)
    second = document(database, "示例系统支持权限配置。", name="第二证据.txt")
    approve(second)
    e2 = db.one("SELECT * FROM chunks WHERE document_id=?", (second["id"],))
    db.update("requirements", req, {"evidence_ids": [e1["id"], e2["id"]], "status": "drafted"})
    db.update("documents", second["id"], revocation)
    with TestClient(app) as client:
        result = client.patch(f"/api/requirements/{req}", json={"status": "confirmed"})
        assert result.status_code == 400 and "证据" in result.json()["detail"]


@pytest.mark.parametrize("marker", ["[E:证据ID]", "[E:bad id]", "[E:unclosed"])
def test_section_bad_citations_save_draft_but_block_approval_and_export(database, marker):
    p, _, _, _, sec, evidence = seed_review(database)
    with TestClient(app) as client:
        assert client.patch(f"/api/sections/{sec}", json={"content": "支持档案检索。" + marker}).status_code == 200
        assert any(c["code"] == "broken_citation" for c in workflow.review_project(p))
        assert client.patch(f"/api/sections/{sec}", json={"status": "approved"}).status_code == 400
        assert client.patch(f"/api/sections/{sec}", json={"content": "支持档案检索。" + marker, "status": "approved"}).status_code == 400
        db.update("sections", sec, {"status": "approved"})
        with pytest.raises(ValueError, match="引用"):
            workflow.export_project(p, "docx", True)
        assert not db.all("SELECT * FROM exports")
        result = client.patch(f"/api/sections/{sec}", json={"content": f"支持档案检索。[E:{evidence['id']}]", "status": "approved"})
        assert result.status_code == 200 and result.json()["section"]["evidence_ids"] == [evidence["id"]]
