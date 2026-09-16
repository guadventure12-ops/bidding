"""招投标 local application. Bind this server to 127.0.0.1 only."""
from __future__ import annotations

import json
import hashlib
import asyncio
import os
import re
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlsplit
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from .build_info import capture, asset_type
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from . import db, provider, workflow, telemetry, content_review, knowledge_review, review_rules


@asynccontextmanager
async def lifespan(app):
    db.init()
    telemetry.get()
    try:
        yield
    finally:
        telemetry.shutdown()


BUILD_INFO, BUILD_ASSETS = capture(db.ROOT)
VERSION = "1.0.2"
app = FastAPI(title="招投标", version=VERSION, lifespan=lifespan, docs_url="/api/docs", redoc_url=None)


@app.middleware("http")
async def local_only(request: Request, call_next):
    host = request.url.hostname or ""
    allowed = {"127.0.0.1", "localhost", "::1"}
    if os.environ.get("MX_TESTING") == "1":
        allowed.add("testserver")
    if host not in allowed:
        return JSONResponse({"detail": "仅允许本机访问"}, status_code=403)
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
            origin_matches = parsed.scheme in ("http", "https") and parsed.hostname == host and (parsed.port or (443 if parsed.scheme == "https" else 80)) == (request.url.port or (443 if request.url.scheme == "https" else 80))
        except ValueError:
            origin_matches = False
        if not origin_matches:
            return JSONResponse({"detail": "已阻止跨来源请求；请从招投标本机页面操作"}, status_code=403)
    if request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"detail": "已阻止跨站请求"}, status_code=403)
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        length = request.headers.get("content-length", "")
        if not length.isdigit():
            return JSONResponse({"detail": "上传请求必须提供文件总长度"}, status_code=411)
        if review_rules.enabled('file_parse_safety', review_rules.flags()) and int(length) > workflow.MAX_FILE_BYTES + 1024 * 1024:
            return JSONResponse({"detail": "单份文件不能超过 200 MB"}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(provider.ProviderError)
async def provider_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectInput(Input):
    name: str = Field(min_length=1, max_length=160)
    domain: str = "archive"
    company_name: str = Field(default="", max_length=160)
    project_number: str = Field(default="", max_length=160)
    buyer: str = Field(default="", max_length=200)
    deadline: str = Field(default="", max_length=100)


class QuotationLine(Input):
    name: str = Field(default="", max_length=300)
    quantity: str | int | float | None = ""
    unit_price: str | int | float | None = ""
    subtotal: str | int | float | None = ""
    note: str = Field(default="", max_length=1000)


class QuotationInput(Input):
    confirmed: bool = False
    total_including_tax: str | int | float | None = ""
    items: list[QuotationLine] = Field(default_factory=list, max_length=500)


class ProjectPatch(Input):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    domain: str | None = None
    company_name: str | None = Field(default=None, min_length=1, max_length=160)
    project_number: str | None = Field(default=None, max_length=160)
    buyer: str | None = Field(default=None, max_length=200)
    deadline: str | None = Field(default=None, max_length=100)
    quotation: QuotationInput | None = None


class ImportInput(Input):
    path: str = Field(min_length=1, max_length=2000)


class RunInput(Input):
    mode: str


class RegenerateSectionInput(Input):
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: str = Field(pattern=r"^[a-f0-9-]{32,36}$")
    instruction: str = Field(default="", max_length=2000)
    confirmed: bool = False
    outline_revision: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    complete_index_gaps: bool = False
    completion_revision: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    completion_ids: list[str] = Field(default_factory=list, max_length=50)
    update_index_pages: bool = False


class ExportInput(Input):
    format: str = "docx"
    final: bool = False


class SettingsInput(Input):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    company_name: str | None = None
    knowledge_path: str | None = None
    tender_path: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=1024, le=32768)
    batch_chars: int | None = Field(default=None, ge=6000, le=32000)
    ocr: bool | None = None
    section_approval_threshold: Literal["low", "medium", "high", "ignore"] | None = None


class ReviewRulesInput(Input):
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    enabled: dict[str, StrictBool] = Field(min_length=1, max_length=200)

    @field_validator('enabled')
    @classmethod
    def known_rules(cls, value):
        from . import review_rules
        if not set(value) <= review_rules.EDITABLE:
            raise ValueError('规则ID不存在，不能修改')
        return value


class RequirementInput(Input):
    response: str | None = Field(default=None, max_length=80000)
    status: str | None = None
    evidence_ids: list[str] | None = None
    title: str | None = Field(default=None, min_length=1, max_length=250)
    text: str | None = Field(default=None, min_length=1, max_length=15000)


class SectionInput(Input):
    title: str | None = Field(default=None, min_length=1, max_length=250)
    content: str | None = Field(default=None, max_length=500000)
    status: str | None = None


class KnowledgeInput(Input):
    status: str | None = None
    scope: str | None = None
    valid_until: str | None = None
    warnings_acknowledged: bool | None = None


class BatchContentInput(Input):
    section_ids: list[str] = Field(min_length=1, max_length=1000)
    action: str = 'cleanup'
    token: str = ''
    confirmed: bool = False


class TrustInput(Input):
    document_ids: list[str] = Field(max_length=1000)
    token: str
    confirmed: bool = False


class TodoResolutionInput(Input):
    signature: str
    note: str = Field(min_length=10, max_length=2000)
    confirmed: bool = False


class ConfirmationInput(Input):
    confirmed: bool = False


class OutlineLibraryInput(Input):
    revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    modules: list[dict] = Field(max_length=200)
    presets: list[dict] = Field(min_length=3, max_length=3)


class OutlineChildInput(Input):
    id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=250)
    enabled: StrictBool


class OutlineGroupInput(Input):
    id: str = Field(min_length=1, max_length=160)
    enabled: StrictBool
    children: list[OutlineChildInput] | None = Field(default=None, max_length=100)


class OutlineSelectionInput(Input):
    revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirmed: StrictBool = False
    groups: list[OutlineGroupInput] = Field(max_length=500)


class OutlineGenerationInput(Input):
    revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirmed: StrictBool = False


class ProductModuleInput(Input):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=250000)
    scope: Literal['general', 'archive', 'expense'] = 'general'


class ProductModulePatch(Input):
    revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    title: str | None = Field(default=None, min_length=1, max_length=160)
    content: str | None = Field(default=None, min_length=1, max_length=250000)
    scope: Literal['general', 'archive', 'expense'] | None = None


class ProductModuleDelete(Input):
    revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    confirmed: StrictBool = False


class ProductModuleSelection(Input):
    module_ids: list[str] = Field(min_length=1, max_length=100)


class ProductModuleApply(ProductModuleSelection):
    revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    request_id: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')
    confirmed: StrictBool = False


@app.get('/api/product-modules')
def product_modules_get():
    from . import product_modules
    return product_modules.list_modules()


@app.post('/api/product-modules')
def product_modules_create(data: ProductModuleInput):
    from . import product_modules
    return {'module': product_modules.create(data.model_dump())}


@app.get('/api/product-modules/{id}')
def product_module_get(id: str):
    from . import product_modules
    return product_modules.get_module(id)


@app.patch('/api/product-modules/{id}')
def product_module_update(id: str, data: ProductModulePatch):
    from . import product_modules
    return {'module': product_modules.update(id, data.revision, data.model_dump(exclude_none=True, exclude={'revision'}))}


@app.delete('/api/product-modules/{id}')
def product_module_delete(id: str, data: ProductModuleDelete):
    from . import product_modules
    return product_modules.delete(id, data.revision, data.confirmed)


@app.post('/api/sections/{id}/product-modules/preview')
def product_module_preview(id: str, data: ProductModuleSelection):
    from . import product_modules
    return product_modules.preview(id, data.module_ids)


@app.post('/api/sections/{id}/product-modules/apply')
def product_module_apply(id: str, data: ProductModuleApply):
    from . import product_modules
    return product_modules.apply(id, data.module_ids, data.revision, data.request_id, data.confirmed)


@app.post('/api/product-module-operations/{id}/undo')
def product_module_undo(id: str, data: ConfirmationInput):
    from . import product_modules
    return product_modules.undo(id, data.confirmed)


@app.get('/api/outline-library')
def outline_library_get():
    from . import outline_library
    return outline_library.get_library()


@app.put('/api/outline-library')
def outline_library_put(data: OutlineLibraryInput):
    from . import outline_library
    return outline_library.save_library(data.revision, data.modules, data.presets)


@app.get('/api/projects/{id}/outline-plan')
def outline_plan_get(id: str):
    from . import compilation_outline
    return compilation_outline.preview(id)


@app.put('/api/projects/{id}/outline-plan')
def outline_plan_put(id: str, data: OutlineSelectionInput):
    from . import compilation_outline
    return compilation_outline.save(id, data.revision, [g.model_dump(exclude_none=True) for g in data.groups], data.confirmed)


@app.post('/api/projects/{id}/outline-plan/generate')
def outline_plan_generate(id: str, data: OutlineGenerationInput):
    return {'job': workflow.create_job(id, 'outline', data.model_dump())}


@app.post('/api/outline-operations/{id}/undo')
def outline_plan_undo(id: str, data: ConfirmationInput):
    from . import compilation_outline
    return compilation_outline.undo(id, data.confirmed)


def public_doc(document):
    if not document:
        return document
    return {k: v for k, v in document.items() if k != "path"}


def public_settings():
    return {**{k:v for k,v in db.get_settings().items() if k!='outline_library'}, "key_configured": provider.key_configured(), "key_storage": "Windows DPAPI / 当前用户", "retrieval": "中文双字与英文词 BM25 本地检索"}


def get_document(id):
    document = db.one("SELECT * FROM documents WHERE id=?", (id,))
    if not document:
        raise HTTPException(404, "文档不存在")
    return document


def idle(project_id):
    if review_rules.enabled("operation_conflict", review_rules.flags()) and db.one("SELECT id FROM jobs WHERE project_id IS ? AND status IN ('running','queued')", (project_id,)):
        raise ValueError("任务正在运行，请完成或取消后再修改资料/内容")


def invalidate_evidence(document_id):
    ids = {r["id"] for r in db.all("SELECT id FROM chunks WHERE document_id=?", (document_id,))}
    affected = set()
    for req in db.all("SELECT * FROM requirements"):
        if ids.intersection(req["evidence_ids"]):
            if req["status"] == "confirmed":
                db.update("requirements", req["id"], {"status": "drafted", "updated_at": db.now()})
            affected.add(req["project_id"])
    for section in db.all("SELECT * FROM sections"):
        if ids.intersection(section["evidence_ids"]) or any(f"[E:{id}]" in section["content"] for id in ids):
            db.update("sections", section["id"], {"status": "draft", "updated_at": db.now()})
            affected.add(section["project_id"])
    for project_id in affected:
        workflow.review_project(project_id)
        db.touch(project_id, status="review")


@app.get("/api/health")
def health():
    return {"ok": True, "name": "招投标", "app_id": "bidding-local", "version": VERSION, "local_only": True, "key_configured": provider.key_configured(),
            "build": BUILD_INFO, "workspace_id": hashlib.sha256(os.path.normcase(str(db.ROOT)).encode()).hexdigest(), "process_id": os.getpid()}


@app.get("/api/settings")
def settings():
    return public_settings()


@app.get("/api/telemetry/status")
def telemetry_status():
    return telemetry.get().status()


@app.patch("/api/settings")
def save_settings(data: SettingsInput):
    with workflow.editing(None, whole_workspace=True):
        values = data.model_dump(exclude_none=True)
        if "base_url" in values:
            values["base_url"] = provider.validate_base_url(values["base_url"])
        if "model" in values and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", values["model"]):
            raise ValueError("模型名称格式不正确")
        key = values.pop("api_key", None)
        if key is not None:
            provider.save_key(key)
        for name, value in values.items():
            db.set_setting(name, value.strip() if isinstance(value, str) else value)
        return public_settings()


@app.get("/api/review-settings")
def get_review_settings():
    from . import review_rules
    return review_rules.describe()


@app.patch("/api/review-settings")
def save_review_settings(data: ReviewRulesInput):
    from . import review_rules
    return review_rules.save(data.enabled, data.revision)


@app.get("/api/settings/models")
def models():
    return {"models": provider.list_models()}


@app.post("/api/settings/test")
def test_settings():
    available = provider.list_models()
    selected = db.get_settings()["model"]
    if selected not in available:
        return {"ok": False, "models": available, "message": f"密钥连接成功，但当前模型 {selected} 不在可用列表，请选择可用模型"}
    return {"ok": True, "models": available, "model": selected, "message": "DeepSeek 身份认证与模型列表验证通过；没有发起文本生成请求"}


@app.get("/api/dashboard")
def dashboard():
    count = lambda query: db.one(query)["n"]
    stats = {
        "projects": count("SELECT COUNT(*) AS n FROM projects"), "documents": count("SELECT COUNT(*) AS n FROM documents WHERE project_id IS NOT NULL"),
        "knowledge": count("SELECT COUNT(*) AS n FROM documents WHERE project_id IS NULL"),
        "approved": db.one("SELECT COUNT(*) AS n FROM documents WHERE project_id IS NULL AND status='approved' AND (valid_until='' OR valid_until>=?)", (date.today().isoformat(),))["n"],
        "requirements": count("SELECT COUNT(*) AS n FROM requirements"), "jobs_running": count("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running')"),
    }
    projects = db.all("SELECT p.*,(SELECT COUNT(*) FROM documents d WHERE d.project_id=p.id) AS document_count,(SELECT COUNT(*) FROM requirements r WHERE r.project_id=p.id) AS requirement_count,(SELECT COUNT(*) FROM sections s WHERE s.project_id=p.id) AS section_count FROM projects p ORDER BY updated_at DESC")
    jobs = db.all("SELECT j.*,p.name AS project_name FROM jobs j LEFT JOIN projects p ON j.project_id=p.id ORDER BY j.created_at DESC LIMIT 12")
    return {"stats": stats, "projects": projects, "jobs": jobs, "recent_jobs": jobs, "settings": public_settings()}


@app.get("/api/projects")
def projects():
    return {"projects": db.all("SELECT * FROM projects ORDER BY updated_at DESC")}


@app.post("/api/projects")
def create_project(data: ProjectInput):
    if data.domain not in ("archive", "expense"):
        raise ValueError("项目类型请选择会计电子档案或费控系统")
    name = data.name.strip()
    if not name:
        raise ValueError("项目名称不能为空")
    metadata = {"field_overrides": {k: {"value": getattr(data, k).strip(), "confirmed_at": db.now()} for k in ("project_number", "buyer", "deadline") if getattr(data, k).strip()}}
    record = {"id": db.uid(), "name": name, "domain": data.domain, "company_name": data.company_name.strip() or db.get_settings()["company_name"], "project_number": data.project_number.strip(), "buyer": data.buyer.strip(), "deadline": data.deadline.strip(), "metadata": metadata, "created_at": db.now(), "updated_at": db.now()}
    db.insert("projects", record)
    p = workflow.project(record["id"])
    return {**p, "project": p}


@app.get("/api/projects/{id}")
def project_detail(id: str):
    p = workflow.refresh_project_basics(id)
    exports = db.all("SELECT * FROM exports WHERE project_id=? ORDER BY created_at DESC", (id,))
    for item in exports:
        item.pop("path", None)
        item["url"] = f"/api/exports/{item['id']}/download"
    reqs = db.all("SELECT * FROM requirements WHERE project_id=? ORDER BY created_at,id", (id,))
    checks,risk_rows=workflow.review_project(id,persist=False,_with_assessments=True)
    from . import chapter_outline, compilation_outline
    sections=db.all('SELECT * FROM sections WHERE project_id=? ORDER BY ordinal,id',(id,))
    projection=compilation_outline.projection(p,sections)
    sections=projection['active']
    from . import proposal_runtime
    reqs,response_issues=proposal_runtime.project_responses(p,sections,reqs)
    return {"response_version_issues":response_issues,"chapter_outline":chapter_outline.view(id,sections),"review_rule_flags": review_rules.flags(), "section_risks": [{k:row[k] for k in ('section_id','risk_level','risk_label','eligible','approval_reason','approval_threshold')} for row in risk_rows], "project": p, "documents": [public_doc(d) for d in db.all("SELECT * FROM documents WHERE project_id=? ORDER BY created_at", (id,))], "requirements": reqs,
            "sections": sections, "retained_sections": projection['retained'], "active_section_ids": [s['id'] for s in sections],
            "compilation_outline": compilation_outline.preview(id),
            "jobs": db.all("SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 30", (id,)),
            "checks": checks, "exports": exports,
            "snapshots": db.all("SELECT id,project_id,document_id,label,created_at FROM project_snapshots WHERE project_id=? AND COALESCE(json_extract(payload,'$.kind'),'') NOT IN ('section_regeneration','chapter_outline_migration','product_module_append') ORDER BY created_at DESC", (id,)),
            "stats": {"requirements": len(reqs), "confirmed": sum(r["status"] in ("confirmed", "not_applicable") for r in reqs), "gaps": sum(r["status"] == "gap" for r in reqs)}}


@app.patch("/api/projects/{id}")
def patch_project(id: str, data: ProjectPatch):
    p = workflow.project(id)
    with workflow.editing(id), review_rules.scope():
        p = workflow.project(id)
        values = data.model_dump(exclude_none=True)
        if "domain" in values and values["domain"] not in ("archive", "expense"):
            raise ValueError("项目类型请选择会计电子档案或费控系统")
        values = {k: v.strip() if isinstance(v, str) else v for k, v in values.items()}
        if "quotation" in values:
            values["quotation"] = validate_quotation(values["quotation"])
        if any(k in values and not values[k] for k in ("name", "company_name")):
            raise ValueError("项目名称和投标企业名称不能为空")
        metadata = p.get("metadata") or {}
        overrides = metadata.get("field_overrides", {})
        for key in ("project_number", "buyer", "deadline"):
            if key in values:
                overrides[key] = {"value": values[key], "confirmed_at": db.now()}
        values["metadata"] = {**metadata, "field_overrides": overrides}
        values["updated_at"] = db.now()
        values["status"] = "review" if p["status"] != "draft" else "draft"
        db.update("projects", id, values)
        if "quotation" in values:
            db.execute("UPDATE requirements SET status='drafted',updated_at=? WHERE project_id=? AND category='pricing' AND status='confirmed'", (db.now(), id))
            pricing_ids = {r["id"] for r in db.all("SELECT id FROM requirements WHERE project_id=? AND category='pricing'", (id,))}
            for section in db.all("SELECT * FROM sections WHERE project_id=?", (id,)):
                if pricing_ids.intersection(section["requirement_ids"]) or "报价" in section["title"]:
                    db.update("sections", section["id"], {"status": "draft", "updated_at": db.now()})
        workflow.review_project(id)
        return {"project": workflow.project(id)}


def validate_quotation(value):
    """Keep findings and user literals while only enabled rules block confirmation.

    The API operation must bind review_rules.scope(). Historical quote records
    are not rewritten when a rule setting changes.
    """
    from decimal import DecimalException
    issues, issue_details = [], []
    decimal_enabled = review_rules.active('quote_decimal')
    arithmetic_enabled = review_rules.active('quote_arithmetic')

    def finding(message, rule):
        if message not in issues:
            issues.append(message)
            issue_details.append({'message': message, 'rule_id': rule})

    def number(raw, label, precision=2):
        if raw is None or str(raw).strip() == '':
            return '', None
        literal = str(raw)
        text = literal.strip()
        if not re.fullmatch(r'\d+(?:\.\d{1,' + str(precision) + r'})?', text):
            message = label + f'必须为非负十进制数，最多 {precision} 位小数，不支持千分位或科学计数法'
            if decimal_enabled:
                raise ValueError(message)
            finding(message, 'quote_decimal')
        try:
            # Parsing supplies comparisons only; it never changes the literal
            # returned when the decimal-format check is disabled.
            amount = Decimal(text.replace(',', '') if not decimal_enabled else text)
        except (InvalidOperation, ValueError):
            if decimal_enabled:
                raise ValueError(label + '格式无效')
            raise ValueError('报价输入无法处理：' + label + '不是可解析数值') from None
        if not amount.is_finite():
            if decimal_enabled:
                raise ValueError(label + '格式无效')
            raise ValueError('报价输入无法处理：' + label + '不是有限数值')
        if amount > Decimal('1000000000000'):
            message = label + '超过可接受范围'
            if decimal_enabled:
                raise ValueError(message)
            finding(message, 'quote_decimal')
        return (format(amount, 'f') if decimal_enabled else literal), amount

    total, total_value = number(value.get('total_including_tax'), '含税总价')
    if total_value is None or total_value <= 0:
        finding('含税总价必须大于0元', 'quote_decimal')
    items = []
    subtotals = []
    for index, row in enumerate(value.get('items', []), 1):
        if not any(str(row.get(k) or '').strip() for k in ('name', 'quantity', 'unit_price', 'subtotal', 'note')):
            continue
        name = row.get('name', '').strip()
        quantity, quantity_value = number(row.get('quantity'), f'第{index}项数量', 6)
        price, price_value = number(row.get('unit_price'), f'第{index}项含税单价')
        subtotal, subtotal_value = number(row.get('subtotal'), f'第{index}项含税小计')
        if not name:
            finding(f'第{index}项名称为空', 'quote_decimal')
        if quantity_value is None or quantity_value <= 0 or price_value is None or subtotal_value is None:
            finding(f'第{index}项数量应大于0，单价和小计必须填写', 'quote_decimal')
        else:
            try:
                matches = (quantity_value * price_value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) == subtotal_value
            except DecimalException:
                finding(f'第{index}项数值超出当前十进制计算能力，未验证数量×含税单价', 'quote_arithmetic')
            else:
                if not matches:
                    finding(f'第{index}项小计与数量×含税单价不一致（四舍五入到分）', 'quote_arithmetic')
        if subtotal_value is not None:
            subtotals.append(subtotal_value)
        items.append({'name': name, 'quantity': quantity, 'unit_price': price, 'subtotal': subtotal, 'note': row.get('note', '').strip()})
    if items and total_value is not None:
        try:
            sum_matches = len(subtotals) == len(items) and sum(subtotals, Decimal(0)) == total_value
        except DecimalException:
            finding('明细数值超出当前十进制计算能力，未验证小计之和', 'quote_arithmetic')
        else:
            if not sum_matches:
                finding('明细小计之和与含税总价不一致', 'quote_arithmetic')
    enabled = {'quote_decimal': decimal_enabled, 'quote_arithmetic': arithmetic_enabled}
    active_issues = [item['message'] for item in issue_details if enabled[item['rule_id']]]
    confirmed = bool(value.get('confirmed'))
    if confirmed and active_issues:
        raise ValueError('报价不能确认：' + '；'.join(active_issues))
    return {'confirmed': confirmed, 'total_including_tax': total, 'currency': 'CNY', 'items': items,
            'issues': issues, 'issue_details': issue_details, 'active_issues': active_issues,
            'confirmed_at': db.now() if confirmed else '',
            'source': '用户在本机项目中明确录入并确认' if confirmed else '用户尚未确认的报价草稿'}

async def save_upload(file, project_id=None):
    if project_id:
        workflow.project(project_id)
    with workflow.editing(project_id), review_rules.scope():
        name = workflow.safe_name(file.filename or "upload")
        suffix = Path(name).suffix.lower()
        if suffix not in workflow.SUPPORTED:
            raise ValueError("不支持的文件格式；支持 PDF、DOCX、Markdown、TXT、XLSX、CSV、PPTX、HTML 和常见图片；旧版 DOC 请先另存为 DOCX")
        temporary = db.DATA / "temp" / (db.uid() + suffix)
        total = 0
        try:
            with temporary.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if review_rules.active('file_parse_safety') and total > workflow.MAX_FILE_BYTES:
                        raise HTTPException(413, "单份文件不能超过 200 MB")
                    output.write(chunk)
            if not total:
                raise ValueError("上传文件为空")
            # Parsing runs in a thread so the UI can continue polling or cancelling other work.
            from starlette.concurrency import run_in_threadpool
            parsing = asyncio.create_task(run_in_threadpool(workflow.ingest, temporary, project_id, name))
            try:
                document = await asyncio.shield(parsing)
            except asyncio.CancelledError:
                # A disconnected browser cannot stop a running parser thread.
                # Keep its file and reservation until it finishes committing.
                while not parsing.done():
                    try:
                        await asyncio.shield(parsing)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not parsing.cancelled():
                    parsing.exception()
                raise
            return {"document": public_doc(document), "duplicate": document.get("duplicate", False)}
        finally:
            temporary.unlink(missing_ok=True)
            await file.close()


@app.post("/api/projects/{id}/documents")
async def upload_tender(id: str, file: UploadFile = File(...)):
    return await save_upload(file, id)


@app.post("/api/knowledge/upload")
async def upload_knowledge(file: UploadFile = File(...)):
    return await save_upload(file)


def schedule_import(data, project_id=None):
    path = Path(data.path).expanduser().resolve()
    if not path.exists():
        raise ValueError("本地路径不存在，请填写完整文件或目录路径")
    if path.is_symlink():
        raise ValueError("请选择实际文件或文件夹，不能导入符号链接")
    if path.is_dir() and (path == Path(path.anchor) or path in (db.ROOT, db.DATA) or db.DATA.is_relative_to(path)):
        raise ValueError("请选择具体资料目录，不能导入整个磁盘、软件目录或其上级")
    return {"job": workflow.create_job(project_id, "import", {"path": str(path)})}


@app.post("/api/projects/{id}/import")
def import_tender(id: str, data: ImportInput):
    workflow.project(id)
    return schedule_import(data, id)


@app.post("/api/knowledge/import")
def import_knowledge(data: ImportInput):
    return schedule_import(data)


@app.get("/api/knowledge")
def knowledge(q: str = "", domain: str = "general", project_id: str | None = None):
    docs = [public_doc(d) for d in db.all("SELECT * FROM documents WHERE project_id IS NULL ORDER BY created_at DESC")]
    if q:
        # Without a domain, search both supported domains plus general approved evidence.
        domains = [domain] if domain in ("archive", "expense") else ["archive", "expense"]
        hits = {}
        for scope in domains:
            for hit in workflow.search_evidence(q[:3000], scope, limit=30, project_id=project_id):
                hits[hit["id"]] = hit
        results = sorted(hits.values(), key=lambda h: h["score"], reverse=True)[:30]
    else:
        results = []
    effective_status = lambda d: 'expired' if d['valid_until'] and d['valid_until'] < date.today().isoformat() else d['status']
    return {"documents": docs, "results": results, "stats": {"total": len(docs), "approved": sum(effective_status(d) == "approved" for d in docs), "pending": sum(effective_status(d) == "pending" for d in docs), "expired": sum(effective_status(d) == "expired" for d in docs)}, "retrieval": "中文双字与英文词 BM25；仅检索已批准、未过期且符合用途的企业资料"}


class KnowledgePreviewInput(Input):
    document_ids: list[str] = Field(min_length=1, max_length=2000)


class KnowledgeApplyInput(Input):
    ticket: str = Field(min_length=1, max_length=1000000)
    confirmed: bool = False


@app.post("/api/knowledge/approval/preview")
def preview_knowledge_approval(data: KnowledgePreviewInput):
    return knowledge_review.preview(data.document_ids)


@app.post("/api/knowledge/approval/apply")
def apply_knowledge_approval(data: KnowledgeApplyInput):
    if not data.confirmed:
        raise ValueError("请确认预览中的资料及认可范围")
    return knowledge_review.apply(data.ticket)


@app.post("/api/knowledge/approval/{id}/undo")
def undo_knowledge_approval(id: str, data: ConfirmationInput):
    if not data.confirmed:
        raise ValueError("请确认撤销本次资料批准")
    return knowledge_review.undo(id)


@app.get("/api/knowledge/approval/operations")
def knowledge_approval_operations():
    return {"operations": knowledge_review.operations()}


@app.patch("/api/knowledge/{id}")
def patch_knowledge(id: str, data: KnowledgeInput):
    with workflow.editing(None):
        before = get_document(id)
        document = knowledge_review.review_single(id, data.model_dump(exclude_none=True))
        # Preserve existing revocation behavior. Positive knowledge approval must
        # never edit/approve chapters or rewrite old audits.
        if before['status'] == 'approved' and (document['status'] != 'approved'
                or document['scope'] != before['scope'] or document['valid_until'] != before['valid_until']):
            invalidate_evidence(id)
        return {"document": public_doc(document)}


@app.get("/api/documents/{id}")
def document_detail(id: str):
    document = get_document(id)
    chunks = db.all("SELECT * FROM chunks WHERE document_id=? ORDER BY ordinal", (id,))
    return {"document": public_doc(document), "blocks": chunks, "chunks": chunks, "url": f"/api/documents/{id}/download"}


@app.patch("/api/documents/{id}")
def acknowledge_document(id: str, data: KnowledgeInput):
    document = get_document(id)
    if not document["project_id"]:
        return patch_knowledge(id, data)
    with workflow.editing(document["project_id"]):
        document = get_document(id)
        if data.warnings_acknowledged is None:
            raise ValueError("招标文件仅支持确认解析提示 warnings_acknowledged")
        db.update("documents", id, {"metadata": {**document["metadata"], "warnings_acknowledged": data.warnings_acknowledged}, "updated_at": db.now()})
        workflow.review_project(document["project_id"])
        return {"document": public_doc(get_document(id))}


@app.get("/api/documents/{id}/download")
def document_download(id: str):
    document = get_document(id)
    path = Path(document["path"])
    if not path.exists():
        raise HTTPException(404, "原始文件副本不存在")
    return FileResponse(path, filename=document["name"], media_type="application/octet-stream")


@app.post("/api/documents/{id}/reparse")
def reparse_document(id: str):
    document = get_document(id)
    if review_rules.enabled("operation_conflict", review_rules.flags()) and not document["project_id"] and db.one("SELECT id FROM jobs WHERE status IN ('running','queued') AND mode IN ('generate','review')"):
        raise ValueError("生成或审核任务正在使用企业证据，请稍后重解析")
    return {"job": workflow.create_job(document["project_id"], "reparse", {"document_id": id})}


@app.get("/api/projects/{id}/snapshots")
def project_snapshots(id: str):
    workflow.project(id)
    return {"snapshots": db.all("SELECT id,project_id,document_id,label,created_at FROM project_snapshots WHERE project_id=? AND COALESCE(json_extract(payload,'$.kind'),'') NOT IN ('section_regeneration','chapter_outline_migration','product_module_append') ORDER BY created_at DESC", (id,))}


@app.post("/api/snapshots/{id}/restore")
def restore_snapshot(id: str):
    candidate = db.one('SELECT * FROM project_snapshots WHERE id=?', (id,))
    if candidate and candidate['payload'].get('kind') in ('content_batch', 'knowledge_approval', 'section_regeneration', 'chapter_outline_migration', 'compilation_outline', 'product_module_append'):
        raise ValueError('批量操作快照请使用对应的撤销入口；不能按文档重解析快照恢复')
    snapshot = db.one("SELECT * FROM project_snapshots WHERE id=?", (id,))
    if not snapshot:
        raise HTTPException(404, "历史快照不存在")
    with workflow.editing(snapshot["project_id"]):
        return workflow.restore_snapshot(id)


@app.get("/api/chunks/{id}")
def chunk_detail(id: str):
    item = db.one("SELECT c.*,d.name AS document_name,d.status AS document_status,d.scope,d.valid_until FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.id=?", (id,))
    if not item:
        raise HTTPException(404, "证据文本不存在")
    return item


@app.post("/api/projects/{id}/run")
def run_project(id: str, data: RunInput):
    if data.mode not in ("analyze", "generate", "review"):
        raise ValueError("任务类型无效")
    workflow.project(id)
    if data.mode == "generate" and not provider.key_configured():
        raise ValueError("尚未配置 DeepSeek API Key；请在设置中保存并测试连接后生成")
    return {"job": workflow.create_job(id, data.mode)}


@app.get("/api/jobs/{id}")
def job_detail(id: str):
    job = db.one("SELECT * FROM jobs WHERE id=?", (id,))
    if not job:
        raise HTTPException(404, "任务不存在")
    events = db.all("SELECT * FROM events WHERE job_id=? ORDER BY id DESC LIMIT 200", (id,))
    return {**job, "job": job, "events": list(reversed(events)), "langfuse_trace_id": telemetry.job_trace_id(id)}


@app.post("/api/jobs/{id}/cancel")
def cancel_job(id: str):
    job = db.one("SELECT * FROM jobs WHERE id=?", (id,))
    if not job:
        raise HTTPException(404, "任务不存在")
    if job["status"] in ("queued", "running"):
        db.update("jobs", id, {"cancel_requested": 1, "message": "已请求取消；正在结束当前操作并保存检查点"})
        db.event(id, "用户请求取消任务", "warning")
    return job_detail(id)


@app.post("/api/jobs/{id}/retry")
def retry_job(id: str):
    job = db.one("SELECT * FROM jobs WHERE id=?", (id,))
    if not job:
        raise HTTPException(404, "任务不存在")
    if job["status"] not in ("failed", "cancelled", "interrupted"):
        raise ValueError("仅失败、取消或重启中断的任务可重试")
    if job['mode'] == 'outline':
        raise ValueError('请回到“调整编制目录”重新预览剩余模块并确认规划；已完成下级目录会保留，不重复调用')
    return {"job": workflow.create_job(job["project_id"], job["mode"], job["payload"], job["checkpoint"])}


@app.patch("/api/requirements/{id}")
def patch_requirement(id: str, data: RequirementInput):
    req = db.one("SELECT * FROM requirements WHERE id=?", (id,))
    if not req:
        raise HTTPException(404, "要求不存在")
    with workflow.editing(req["project_id"]), review_rules.scope():
        req = db.one("SELECT * FROM requirements WHERE id=?", (id,))
        values = data.model_dump(exclude_none=True)
        if "status" in values and values["status"] not in ("pending", "gap", "drafted", "confirmed", "not_applicable"):
            raise ValueError("要求审核状态无效")
        # Content-only edits become drafts before confirmation validation. A user
        # can save work in progress; its citation issues remain blocking checks.
        if any(k in values for k in ("response", "evidence_ids", "text", "title")) and "status" not in values:
            values["status"] = "drafted" if values.get("response", req["response"]) else "pending"
        merged = {**req, **values}
        p = workflow.project(req["project_id"])
        domain = p["domain"]
        if values.get("evidence_ids") is not None and review_rules.active("response_confirmation"):
            valid = workflow._valid_evidence(values["evidence_ids"], domain, req["project_id"])
            if len(valid) != len(set(values["evidence_ids"])):
                raise ValueError("引用证据必须是已批准、未过期、适用于本项目的企业文本块ID")
        if merged["status"] == "confirmed" and review_rules.active("response_confirmation"):
            gaps = [m.group() for m in content_review.PLACEHOLDER.finditer(merged['response']) if not content_review.DELIVERY.search(m.group()) or content_review.FIELD.search(m.group())]
            if not content_review.substantive(merged['response']) or gaps:
                raise ValueError("响应为空或存在具体正文缺项，不能标记已确认：" + '；'.join(gaps))
            citation_state = workflow._response_citation_state(merged["response"], merged["evidence_ids"])
            if citation_state["format_error"] or citation_state["unregistered"]:
                raise ValueError("确认前请修正正文引用格式，并将正文中的每个引用登记到本条证据列表")
            valid = workflow._valid_evidence(merged["evidence_ids"], domain, req["project_id"])
            if len(valid) != len(set(merged["evidence_ids"])):
                raise ValueError("确认前请替换已撤销、过期、不适用或无效的企业证据")
            if not valid and not (req["category"] == "pricing" and workflow.has_confirmed_quotation(p)):
                raise ValueError("确认前请添加直接有效的企业证据；报价条款也可使用本项目已人工确认的结构化报价")
        if merged["status"] == "not_applicable" and len(merged["response"].strip()) < 10 and review_rules.active("response_confirmation"):
            raise ValueError("不适用需填写至少10字理由，并由投标负责人核对")
        values["updated_at"] = db.now()
        db.update("requirements", id, values)
        for section in db.all("SELECT * FROM sections WHERE project_id=?", (req["project_id"],)):
            if id in section["requirement_ids"]:
                db.update("sections", section["id"], {"status": "draft", "updated_at": db.now()})
        workflow.review_project(req["project_id"])
        db.touch(req["project_id"], status="review")
        return {"requirement": db.one("SELECT * FROM requirements WHERE id=?", (id,))}


@app.get("/api/sections/{id}/regeneration")
def section_regeneration_preview(id: str):
    from . import section_generation
    return section_generation.preview(id)


@app.post("/api/sections/{id}/regenerate")
def regenerate_section(id: str, data: RegenerateSectionInput):
    from . import section_generation
    if not data.confirmed:
        raise ValueError("请确认本章范围、原文替换和模型调用后再执行")
    section = db.one("SELECT * FROM sections WHERE id=?", (id,))
    if not section:
        raise HTTPException(404, "章节不存在")
    payload = {"section_id":id, "section_title":section['title'], "revision":data.revision,
               "instruction":data.instruction, "request_id":data.request_id,'outline_revision':data.outline_revision}
    if data.complete_index_gaps:
        payload.update(complete_index_gaps=True,completion_revision=data.completion_revision,completion_ids=data.completion_ids)
    if data.update_index_pages:payload['update_index_pages']=True
    return {"job":workflow.create_job(section['project_id'], 'generate', payload)}


@app.get('/api/projects/{id}/index-pages')
def index_page_status(id:str):
    from . import index_pages
    return index_pages.current(workflow.project(id)) or {'valid':False,'reason':'本项目未启用评审索引目录','items':[]}


@app.post('/api/projects/{id}/index-pages')
def update_index_page_status(id:str):
    from . import index_materials,proposal_runtime
    project=workflow.project(id)
    if not proposal_runtime.blueprint(project):raise ValueError('本项目未启用评审索引目录')
    return {'job':workflow.create_job(id,'paginate',{'revision':index_materials.input_revision(project)})}


@app.get("/api/sections/{id}/generation-history")
def section_generation_history(id: str):
    from . import section_generation
    rows = db.all("SELECT * FROM project_snapshots WHERE json_extract(payload,'$.kind')=? AND json_extract(payload,'$.section_id')=? ORDER BY created_at DESC", (section_generation.KIND,id))
    return {"snapshots":[{"id":r['id'], "created_at":r['created_at'], "title":r['payload']['before']['title'], "content":r['payload']['before']['content']} for r in rows]}


@app.post("/api/section-generation/{id}/restore")
def restore_generated_section(id: str):
    from . import section_generation
    return section_generation.restore(id)


@app.patch("/api/sections/{id}")
def patch_section(id: str, data: SectionInput):
    section = db.one("SELECT * FROM sections WHERE id=?", (id,))
    if not section:
        raise HTTPException(404, "章节不存在")
    with workflow.editing(section["project_id"]), review_rules.scope():
        section = db.one("SELECT * FROM sections WHERE id=?", (id,))
        from . import compilation_outline
        if not compilation_outline.is_enabled(workflow.project(section['project_id']), section):
            raise ValueError('本节未编入当前目录，请先启用所属模块，再编辑或批准')
        values = data.model_dump(exclude_none=True)
        if "status" in values and values["status"] not in ("draft", "approved"):
            raise ValueError("章节状态仅支持 draft 或 approved")
        if 'title' in values and section.get('outline_group_id'):
            from .chapter_outline import validate_title
            values['title']=validate_title(section,values['title'])
            if values['title'] != section['title']:
                selection=(workflow.project(section['project_id']).get('metadata') or {}).get('outline_selection') or {}
                for group in selection.get('groups',[]):
                    if any(child.get('section_id')==id and child.get('title_locked') for child in group.get('children',[])):
                        raise ValueError('本节标题由招标文件明确规定，不能通过章节编辑改名；正文仍可正常编辑')
        if "content" in values or "title" in values:
            values["user_edited"] = 1
            values.setdefault("status", "draft")
        from . import bid_body
        split = bid_body.separate(values.get("content", section["content"])) if review_rules.active('body_separation') else {'content':values.get('content',section['content']),'items':[]}
        if split['items']:
            values.update(content=split['content'], status=values.get('status', 'draft'), user_edited=1)
        content = values.get("content", section["content"])
        citation_state = workflow._response_citation_state(content, section["evidence_ids"])
        if "content" in values:
            values["evidence_ids"] = citation_state["visible"]
        if values.get("status") == "approved":
            assessment = content_review.approval({**section, **values}, content_review.separated_todos({**section, **values}, split['items']))
            if not assessment["eligible"]:
                raise ValueError(assessment["approval_reason"])
        values["updated_at"] = db.now()
        with db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            content_review._update(conn, 'sections', id, values)
            if split['items'] or values.get('status') == 'approved' or 'title' in values:
                from . import approval_risk
                p = db.decode(conn.execute('SELECT * FROM projects WHERE id=?', (section['project_id'],)).fetchone())
                meta = p['metadata']
                if 'title' in values:
                    for group in (meta.get('outline_selection') or {}).get('groups',[]):
                        for child in group.get('children',[]):
                            if child.get('section_id') == id:
                                child['title'] = values['title']
                meta['content_todos'] = content_review.merge_todos(meta.get('content_todos', []) + content_review.separated_todos({**section, **values}, split['items']))
                if values.get('status') == 'approved':
                    meta.setdefault('section_approvals', {})[id] = approval_risk.record({**section, **values}, assessment)
                content_review._update(conn, 'projects', p['id'], {'metadata':meta})
        workflow.review_project(section["project_id"])
        db.touch(section["project_id"], status="review")
        return {"section": db.one("SELECT * FROM sections WHERE id=?", (id,)), "moved_notes":len(split["items"])}


@app.post('/api/projects/{id}/sections/batch/preview')
def preview_content_batch(id: str, data: BatchContentInput):
    return content_review.preview(id, data.section_ids, data.action)


@app.post('/api/projects/{id}/sections/batch/apply')
def apply_content_batch(id: str, data: BatchContentInput):
    return content_review.execute(id, data.section_ids, data.action, data.token, data.confirmed)


@app.post('/api/content-operations/{id}/undo')
def undo_content_batch(id: str, data: ConfirmationInput):
    return content_review.undo(id, data.confirmed)


@app.get('/api/projects/{id}/content-todos')
def content_todos(id: str):
    rows, todos = content_review.assessments(id)
    for item in todos:
        item['signature'] = content_review.todo_signature(item)
        item['can_resolve'] = content_review.can_resolve(item)
    return {'assessments': rows, 'todos': todos}


@app.post('/api/projects/{id}/content-todos/{todo_id}/resolve')
def resolve_content_todo(id: str, todo_id: str, data: TodoResolutionInput):
    return content_review.resolve_todo(id, todo_id, data.signature, data.note, data.confirmed)


@app.get('/api/projects/{id}/trusted-sources')
def trusted_source_preview(id: str):
    return content_review.trust_preview(id)


@app.post('/api/projects/{id}/trusted-sources')
def save_trusted_sources(id: str, data: TrustInput):
    return content_review.set_trust(id, data.document_ids, data.token, data.confirmed)


@app.post("/api/projects/{id}/export")
def export(id: str, data: ExportInput):
    with workflow.editing(id):
        if data.final:
            idle(id)
        result = workflow.export_project(id, data.format, data.final)
        result.pop("path", None)
        return result


@app.get("/api/exports/{id}/download")
def download_export(id: str):
    item = db.one("SELECT * FROM exports WHERE id=?", (id,))
    if not item:
        raise HTTPException(404, "导出文件不存在")
    path = Path(item["path"])
    if not path.is_file():
        raise HTTPException(404, "导出文件已不存在，请重新导出")
    mime = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "pdf": "application/pdf", "md": "text/markdown; charset=utf-8", "zip": "application/zip"}[item["format"]]
    return FileResponse(path, filename=item["name"], media_type=mime)


@app.get("/api/build")
def build():
    return JSONResponse(BUILD_INFO, headers={"Cache-Control": "no-store"})


@app.get("/static/{name:path}")
def static_asset(name: str):
    if name not in BUILD_ASSETS:
        raise HTTPException(404, "资源不存在")
    return Response(BUILD_ASSETS[name], media_type=asset_type(name), headers={"Cache-Control": "no-store"})


@app.get("/")
def index():
    html = BUILD_ASSETS["index.html"].decode("utf-8")
    html = html.replace('<html lang="zh-CN">', '<html lang="zh-CN" data-build="' + BUILD_INFO["build_id"] + '">')
    return Response(html, media_type="text/html", headers={"Cache-Control": "no-store"})
