"""Pure, source-backed proposal planning; transport batches never define chapters.

This module consumes parsed procurement blocks and the requirements ledger. It
does not read a reference bid, trust enterprise claims, write a database, or call
a model. A source-derived writing outline is distinct from evidence of capability.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import re

VERSION = "proposal-blueprint-1"


def _norm(value):
    return re.sub(r"\s+", "", str(value or "")).replace("（", "(").replace("）", ")")


def _key(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:24]


def _obj(value):
    if isinstance(value, dict):
        return value
    try:
        value = json.loads(value or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _table(block):
    return block.get("table") or _obj(block.get("metadata")).get("table") or {}


def _ref(block, quote=None):
    return {"chunk_id": block.get("chunk_id") or block.get("id"),
            "document_id": block.get("document_id"),
            "locator": block.get("locator", ""),
            "quote": str(quote if quote is not None else block.get("text") or block.get("quote") or "")}


def _dedup_refs(refs):
    seen, result = set(), []
    for ref in refs:
        key = json.dumps(ref, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            result.append(ref)
    return result


def _source_key(block):
    # Prefer document + original locator: import may split one large source row
    # into several chunks. The child offset does not change its module owner.
    locator = re.sub(r"（字符 \d+–\d+）$", "", str(block.get("locator") or ""))
    return (str(block.get("document_id") or ""), locator)


def extract_response_slots(blocks):
    """Read the explicit technical-volume contents, not arbitrary document headings."""
    slots, active = [], False
    for block in blocks:
        text = str(block.get("text") or "").strip()
        if re.fullmatch(r"(?:商务[、，,及和/\s]*)?技术(?:响应)?文件目录", text):
            active = True
            continue
        if not active or not text:
            continue
        if _table(block) or re.match(r"^附件\s*\d+\s*[：:]", text) or len(text) > 180:
            break
        title = re.sub(r"^[（(]?(?:\d+|[一二三四五六七八九十]+)[）).、．]\s*", "", text)
        title = re.split(r"[（(](?:格式|根据|详见|见附件|主要用于)", title, maxsplit=1)[0].strip()
        if not title or len(title) > 70:
            break
        if not any(s["title"] == title for s in slots):
            slots.append({"title": title, "source_refs": [_ref(block)], "order": len(slots) + 1})
    return slots


def extract_features(blocks):
    """Reconstruct both vertically merged module and function columns per table."""
    tables, result, issues = {}, [], []
    for block in blocks:
        table = _table(block)
        if not table:
            continue
        identity = (block.get("document_id"), table.get("index"))
        cells = [str(c or "").strip() for c in table.get("cells", [])]
        if len(cells) >= 3 and "模块" in cells[0] and "功能" in cells[1] and re.search("描述|要求|说明", cells[2]):
            tables[identity] = {"module": "", "function": "", "header": _ref(block)}
            continue
        if identity not in tables or len(cells) < 3:
            continue
        state = tables[identity]
        if cells[0] and cells[0] != state["module"]:
            state["module"], state["function"] = cells[0], ""
        if cells[1]:
            state["function"] = cells[1]
        description = "\n".join(cells[2:]).strip()
        if not description:
            continue
        if not state["module"] or not state["function"]:
            issues.append({"type": "unresolved_feature_owner", "source": _ref(block),
                           "message": "功能表缺少可继承的模块或功能名称，保留来源供核对"})
            continue
        result.append({"module": state["module"], "function": state["function"],
                       "description": description, "source": _ref(block),
                       "table_header": state["header"], "source_key": _source_key(block)})
    return result, issues


def extract_scoring(blocks):
    active, result = set(), []
    for block in blocks:
        table = _table(block)
        if not table:
            continue
        identity = (block.get("document_id"), table.get("index"))
        cells = [str(c or "").strip() for c in table.get("cells", [])]
        text = " | ".join(cells)
        if any(re.fullmatch("评审标准|评分因素|评审因素|评分标准", c) for c in cells) and "分值" in cells:
            active.add(identity)
            continue
        if identity in active and len(cells) >= 4 and re.fullmatch(r"\d+", cells[0]) and re.fullmatch(r"\d+(?:\.\d+)?", cells[-1]):
            result.append({"number": cells[0], "title": cells[1], "points": float(cells[-1]),
                           "text": "\n".join(cells[2:-1]), "source": _ref(block),
                           "source_key": _source_key(block)})
    return result


def canonicalize_requirements(requirements):
    """Merge exact duplicate meanings only; identical short quotes alone are insufficient."""
    groups = defaultdict(list)
    for row in requirements:
        identity = (row.get("category", "other"), _norm(row.get("quote") or row.get("text")), _norm(row.get("text")))
        groups[identity].append(row)
    result = []
    for rows in groups.values():
        rows = sorted(rows, key=lambda row: str(row["id"]))
        result.append({"canonical_id": rows[0]["id"], "alias_ids": [r["id"] for r in rows],
                       "requirement": dict(rows[0]), "source_refs": _dedup_refs([_ref(r, r.get("quote")) for r in rows]),
                       "rows": rows})
    return result


def extract_form_templates(blocks, titles):
    """Keep procurement's empty form structure, never populate bidder facts."""
    result, active = {}, None
    for block in blocks:
        text = str(block.get("text") or "").strip()
        attachment = re.match(r"^附件\s*\d+\s*[：:]\s*(.+)", text)
        if attachment:
            name = attachment[1].strip()
            aliases = {"技术条款偏离表": "技术偏离表", "类似项目业绩": "类似业绩"}
            form_name = lambda value: aliases.get(_norm(value), _norm(value))
            active = next((title for title in titles if form_name(title) == form_name(name)), None)
            # Template names may include a form suffix, never use unbounded fuzzy matches.
            if active is None:
                active = next((title for title in titles if _norm(name) in (_norm(title) + "表", _norm(title) + "清单")), None)
            if active:
                result.setdefault(active, {"source_refs": [], "paragraphs": [], "tables": []})
        if active is None:
            continue
        template = result[active]
        template["source_refs"].append(_ref(block))
        table = _table(block)
        if table:
            identity = (block.get("document_id"), table.get("index"))
            target = next((row for row in template["tables"] if tuple(row["table_key"]) == identity), None)
            if target is None:
                target = {"table_key": list(identity), "rows": [], "source_refs": []}
                template["tables"].append(target)
            target["rows"].append({"cells": list(table.get("cells", [])), "spans": list(table.get("spans", [])), "row": table.get("row")})
            target["source_refs"].append(_ref(block))
        else:
            template["paragraphs"].append({"text": text, "source": _ref(block)})
    return result


# These are writing intents, not assertions about the bidder or sample client.
_PURPOSES = (
    (r"评审.*索引", "form", "将评分因素指向实际响应章节；页码由最终排版更新，不猜测页码", ["评分因素", "响应章节", "页码"]),
    (r"商务.*偏离", "form", "按采购表头比较生效商务条款与实际响应，不用格式清单堆积正文", ["条款", "实际响应", "偏离说明"]),
    (r"技术.*偏离", "form", "以功能方案的已保存响应形成技术偏离表并关联实际章节", ["技术条款", "章节锚点", "偏离说明"]),
    (r"业绩", "attachment", "列出有据业绩及所需合同关键页；未获取或未经认可的合同不编造", ["业绩清单", "采购适用条件", "证明附件"]),
    (r"认证|证书", "attachment", "对应采购认证类别、有效期和范围，保留实际证书交付槽", ["证书目录", "有效期及范围", "实际附件"]),
    (r"人员", "form", "组织岗位职责与项目投入计划，姓名、经历、证书和人天须有项目依据", ["岗位职责", "人员清单", "简历及证明"]),
    (r"需求理解|重难点", "narrative", "按项目现状—重难点—解决路径—依赖风险—验证方式形成连续方案", ["项目范围", "重难点", "方案路径", "风险与验证"]),
    (r"整体设计|总体设计|架构", "narrative", "说明应用、技术、数据、安全及业务流程设计，关联实际架构资产", ["应用架构", "技术路线", "数据流向", "安全边界", "业务流程"]),
    (r"集成|接口", "narrative", "按接口对象、数据流、责任边界、同步补偿及异常追踪说明集成方案", ["系统边界", "接口清单", "同步机制", "异常及验证"]),
    (r"功能模块|功能演示|功能设计", "narrative", "按采购功能模块讲解业务流程、规则、异常、操作界面及可验收输出", ["业务目标", "操作流程", "规则与权限", "异常处理", "演示验证"]),
    (r"进度|实施", "narrative", "按采购阶段编排任务、资源、交付件、验收及风险；冲突时间不擅自改写", ["实施阶段", "任务责任", "交付与验收", "里程碑风险"]),
    (r"质量|工期", "narrative", "明确质量检查点、缺陷闭环及进度保障，与实施阶段保持一致", ["质量目标", "检查点", "缺陷闭环", "工期保障"]),
    (r"售后|运维", "narrative", "说明服务范围、故障分级、处理闭环、巡检升级与应急保障", ["服务范围", "事件流程", "巡检升级", "应急与保障"]),
    (r"培训|知识转移", "narrative", "按使用对象组织知识转移、培训计划、教材及效果验收", ["培训对象", "课程计划", "材料交付", "效果验收"]),
    (r"增值|优惠", "form", "逐项列出获认可的增值方案，金额、期限、赠送与人天不得从参考标书挪用", ["服务内容", "适用条件", "项目确认", "费用边界"]),
)


def _intent(title):
    for pattern, kind, purpose, topics in _PURPOSES:
        if re.search(pattern, title):
            return kind, purpose, list(topics)
    return "attachment", "仅编入与当前采购相关且实际可取得的补充文件，采购程序留在内部台账", ["资料目录", "适用说明"]


def build_blueprint(project_id, requirements, source_blocks, domain="archive"):
    """Return immutable semantic owners and a complete internal coverage ledger.

    All original IDs receive exactly one ledger entry, including procurement
    process, empty forms, and delivery duties. Only narrative owners are model
    writing units. Capacity is a writing target, never a claim of model limits.
    """
    requirements, blocks = list(requirements), list(source_blocks)
    ids = [r["id"] for r in requirements]
    if len(ids) != len(set(ids)):
        raise ValueError("要求ID重复，不能建立唯一覆盖台账")
    slots = extract_response_slots(blocks)
    features, issues = extract_features(blocks)
    scores = extract_scoring(blocks)
    explicit = bool(slots)
    if not slots:
        # Generic fallback is visibly a suggested plan, not invented tender wording.
        names = ["需求理解与解决方案", "整体设计方案", "系统集成方案", "功能模块设计及系统功能演示", "项目进度安排", "质量保证与工期保障", "售后运维服务方案", "知识转移与培训"]
        slots = [{"title": name, "source_refs": [], "order": i + 1} for i, name in enumerate(names)]
        issues.append({"type": "suggested_outline", "message": "未识别明确商务技术目录；当前为可编辑建议目录，不能宣称采购指定格式"})
    groups, sections, by_title, feature_owners = [], [], {}, {}
    form_templates = extract_form_templates(blocks, [slot["title"] for slot in slots])

    def add_group(title, volume, refs, source_kind="tender_outline"):
        group = {"group_key": _key(project_id, volume, title), "title": title,
                 "volume": volume, "source_refs": list(refs), "source_kind": source_kind, "section_keys": []}
        groups.append(group)
        return group

    def add_section(group, title, kind, purpose, topics, refs=()):
        row = {"section_key": _key(project_id, group["group_key"], title), "group_key": group["group_key"],
               "group_title": group["title"], "title": title, "volume": group["volume"],
               "content_kind": kind, "purpose": purpose, "suggested_subtopics": list(topics),
               "source_refs": _dedup_refs([*group["source_refs"], *refs]), "requirement_ids": [],
               "canonical_requirement_ids": [], "feature_rows": [], "score_factors": [],
               "form_schema": {"tables": [], "paragraphs": [], "source_refs": []},
               "recommended_chars": {"min": 0, "target": 0, "max": 0},
               "enterprise_fact_evidence": False}
        sections.append(row)
        group["section_keys"].append(row["section_key"])
        return row

    for slot in slots:
        title = slot["title"]
        group = add_group(title, "technical", slot["source_refs"], "tender_outline" if explicit else "suggested")
        kind, purpose, topics = _intent(title)
        matching_scores = [s for s in scores if _norm(s["title"]) == _norm(title)]
        if re.search(r"功能模块|功能演示|功能设计", title) and features:
            modules = list(dict.fromkeys(f["module"] for f in features))
            for module in modules:
                member_rows = [f for f in features if f["module"] == module]
                leaf = add_section(group, module, "narrative", purpose, topics, [f["source"] for f in member_rows])
                leaf["feature_rows"] = member_rows
                leaf["score_factors"] = matching_scores
                for f in member_rows:
                    feature_owners[f["source_key"]] = leaf
            # Scoring the complete demonstration belongs to its own narrative;
            # it is not silently assigned to the first product module.
            leaf = add_section(group, "系统功能演示与验收验证", "narrative", "建立与功能项相对应的演示步骤、输入、预期结果及验证记录；不宣称演示已经完成", ["演示范围", "演示脚本", "验证记录"], [s["source"] for s in matching_scores])
        else:
            suffix = "编制" if kind == "form" else "" if kind == "attachment" and title.endswith("资料") else "资料" if kind == "attachment" else "方案" if not title.endswith("方案") else ""
            leaf = add_section(group, title + suffix, kind, purpose, topics)
        leaf["score_factors"] = matching_scores
        if kind in ("form", "attachment"):
            leaf["form_schema"] = form_templates.get(title, leaf["form_schema"])
        leaf["source_refs"] = _dedup_refs([*leaf["source_refs"], *[s["source"] for s in matching_scores]])
        by_title[title] = leaf

    qualification = add_section(add_group("资格文件", "qualification", [], "delivery_slot"), "资格声明与证明资料", "form", "按采购模板集中组织资格声明、授权与证明附件，不机械为每个子条款新增证明", ["资格声明", "授权", "证明附件"])
    pricing = add_section(add_group("报价文件", "pricing", [], "delivery_slot"), "报价一览与明细", "form", "依据项目人工确认报价编制表单，预算不得充当报价", ["报价一览", "报价明细", "签章"])
    internal = add_section(add_group("内部响应与交付台账", "internal", [], "internal_ledger"), "采购程序与编制交付核对", "internal", "保存程序知悉、格式字段、交付待办及未能归类要求；不自动写入技术标正文", [])
    # Qualification and price templates are discovered by their actual names,
    # independently of the technical contents. These remain neutral delivery
    # slots even when the ledger has no extracted row for an empty form field.
    other_form_owners = {}
    for block in blocks:
        match = re.match(r"^附件\s*\d+\s*[：:]\s*(.+)", str(block.get("text") or "").strip())
        if not match or re.search(r"封面|目录", match[1]):
            continue
        name = match[1]
        if name in by_title:
            continue
        if re.search(r"报价", name):
            other_form_owners[name] = pricing
        elif re.search(r"资格|响应函|授权书|承诺书|承诺函|控股.*关系", name):
            other_form_owners[name] = qualification
    extra_templates = extract_form_templates(blocks, list(other_form_owners))
    for name, template in extra_templates.items():
        owner = other_form_owners[name]
        for key in ("source_refs", "paragraphs", "tables"):
            owner["form_schema"][key].extend(template[key])
        owner["source_refs"] = _dedup_refs([*owner["source_refs"], *template["source_refs"]])

    def find_title(pattern):
        return next((leaf for title, leaf in by_title.items() if re.search(pattern, title)), None)

    score_owners = {score["source_key"]: by_title[title] for title in by_title for score in scores if _norm(score["title"]) == _norm(title)}
    slot_owners = {_source_key(ref): by_title[slot["title"]] for slot in slots for ref in slot["source_refs"]}
    form_owners = {_source_key(ref): by_title[title] for title, template in form_templates.items() for ref in template["source_refs"]}
    form_owners.update({_source_key(ref): other_form_owners[title] for title, template in extra_templates.items() for ref in template["source_refs"]})
    patterns = [
        (r"培训|知识转移|教材", r"培训|知识转移"),
        (r"驻场|项目经理|项目组|人员简历|社保", r"人员"),
        (r"认证证书|ISO\s*\d|CMMI", r"认证|证书"),
        (r"类似业绩|项目业绩|合同关键页", r"业绩"),
        (r"增值|优惠|赠送|免费人天|维保期后", r"增值|优惠"),
        (r"进度|上线|里程碑|实施周期|实施阶段|工期", r"进度|实施"),
        (r"质量保证|质量保障|缺陷闭环", r"质量|工期"),
        (r"售后|运维|巡检|故障响应|维保", r"售后|运维"),
        (r"对接|接口|集成|SAP|NCC|ERP", r"集成|接口"),
        (r"架构|部署|数据库|容器|微服务|负载均衡|加密|安全|权限模型", r"整体设计|总体设计|架构"),
        (r"重难点|项目背景|项目目标|建设目标|需求理解", r"需求理解|重难点"),
    ]
    ledger, canonical = [], canonicalize_requirements(requirements)
    for item in canonical:
        row = item["requirement"]
        text = str(row.get("title") or "") + "\n" + str(row.get("text") or "")
        category = row.get("category", "other")
        owner, reason = None, ""
        for alias in item["rows"]:
            source = _source_key(alias)
            if source in feature_owners:
                owner, reason = feature_owners[source], "功能表原始模块及功能归属"
                break
            if source in score_owners:
                owner, reason = score_owners[source], "采购评分因素指定内容"
                break
            if source in slot_owners:
                owner, reason = slot_owners[source], "采购商务技术文件目录交付项"
                break
            if source in form_owners:
                owner, reason = form_owners[source], "采购附件表单及字段结构"
                break
        if owner is None:
            for pattern, title_pattern in patterns:
                if re.search(pattern, text, re.I) and (category not in ("format", "other") or re.search(r"方案|提供|提交|应|须", text)):
                    owner = find_title(title_pattern)
                    if owner:
                        reason = "按要求明确主题映射；保留来源供复核"
                        break
        if owner is None:
            if category == "qualification":
                owner, reason = qualification, "资格声明及材料独立交付"
            elif category == "pricing":
                owner, reason = pricing, "报价与金额独立分册"
            elif category == "commercial":
                owner, reason = find_title(r"商务.*偏离") or internal, "商务条款在偏离表核对"
            elif category in ("technical", "implementation", "security", "service"):
                pattern = {"technical": "需求理解|整体设计", "implementation": "进度|实施", "security": "整体设计|架构", "service": "售后|运维"}[category]
                owner, reason = find_title(pattern) or internal, "按业务类别分配，具体引用不等于企业能力已成立"
            else:
                owner, reason = internal, "采购程序、格式字段或未分类交付要求保留内部核对，不逐条堆入技术正文"
        owner["requirement_ids"].extend(item["alias_ids"])
        owner["canonical_requirement_ids"].append(item["canonical_id"])
        owner["source_refs"] = _dedup_refs([*owner["source_refs"], *item["source_refs"]])
        disposition = "narrative" if owner["content_kind"] == "narrative" else "internal" if owner["volume"] == "internal" else "deliverable"
        for rid in item["alias_ids"]:
            ledger.append({"requirement_id": rid, "canonical_id": item["canonical_id"], "alias_ids": item["alias_ids"],
                           "owner_section_key": owner["section_key"], "volume": owner["volume"], "disposition": disposition,
                           "reason": reason, "source_refs": item["source_refs"]})

    for section in sections:
        section["requirement_ids"].sort()
        section["canonical_requirement_ids"].sort()
        if section["content_kind"] == "narrative":
            feature_count = len(section["feature_rows"])
            target = min(14000, max(3000, feature_count * 650)) if feature_count else 5000
            section["recommended_chars"] = {"min": target // 2, "target": target, "max": target * 2}
        section["generate_body"] = section["content_kind"] == "narrative"
        section["requires_project_confirmation"] = section["content_kind"] in ("attachment", "form")
    return {"version": VERSION, "project_id": project_id, "domain": domain, "recognized": explicit,
            "profile": "technical_proposal" if explicit else "suggested_technical_outline",
            "groups": groups, "sections": sections, "ledger": sorted(ledger, key=lambda row: row["requirement_id"]),
            "canonical_requirements": [{k: v for k, v in item.items() if k != "rows"} for item in canonical],
            "feature_rows": features, "score_factors": scores, "issues": issues,
            "coverage": {"input_requirements": len(ids), "mapped_requirements": len(ledger),
                         "canonical_requirements": len(canonical), "all_ids_preserved": set(ids) == {r["requirement_id"] for r in ledger}}}
