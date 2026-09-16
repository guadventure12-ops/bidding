from app.tender_context import context_from_chunks, _notes_for


def chunk(text, id="c1"):
    return {"id": id, "document_id": "d1", "locator": id, "text": text}


def test_checked_exemption_not_unchecked_alternative():
    c = context_from_chunks([chunk("18.1 | 响应保证金 | 本项目 ☑不需要/ □需要缴纳响应保证金。")])
    assert [(x["topic"], x["value"]) for x in c["effective_options"]] == [("响应保证金", "not_required")]
    assert not c["conflicts"]
    row = {"text": "未递交响应保证金将被否决", "quote": "未递交响应保证金将被否决", "category": "commercial"}
    _, notes, sources, downgrade = _notes_for(row, c)
    assert notes and sources and downgrade


def test_conflicting_selection_never_auto_downgraded():
    c = context_from_chunks([chunk("响应保证金 | ☑不需要 □需要"), chunk("响应保证金 | ☑需要 □不需要", "c2")])
    assert len(c["conflicts"]) == 1
    _, notes, _, downgrade = _notes_for({"text": "未提交响应保证金否决", "quote": "", "category": "commercial"}, c)
    assert not downgrade and "澄清" in notes[0]


def test_mixed_compensation_remains_independent():
    c = context_from_chunks([chunk("响应保证金 | ☑不需要 □需要")])
    _, notes, _, downgrade = _notes_for({"text": "未缴纳响应保证金且拒签合同需赔偿损失", "quote": "", "category": "commercial"}, c)
    assert notes and not downgrade


def test_definitions_and_unchecked_choices_are_not_options():
    c = context_from_chunks([chunk("“☑”系指适用；“☐”系指不适用"), chunk("响应保证金 | □不需要 □需要")])
    assert c["checked_sources"] == [] and c["effective_options"] == []


def test_rollout_conflict_keeps_both_exact_sources():
    a, b = chunk("以股份先行、成员企业后续推广"), chunk("各公司同时启动上线", "c2")
    c = context_from_chunks([a, b])
    assert [s["quote"] for s in c["conflicts"][0]["sources"]] == [a["text"], b["text"]]


def test_subcontract_prohibition_does_not_cancel_other_duties():
    c = context_from_chunks([chunk("4 | 分包、转包 | ☑不允许\n□允许，要求资质。")])
    _, notes, _, downgrade = _notes_for({"text": "分包商应具备相应资质", "quote": "", "category": "commercial"}, c)
    assert "不允许分包" in notes[0] and not downgrade


def test_failure_to_deliver_deposit_is_not_effective_rejection():
    c = context_from_chunks([chunk("响应保证金 | ☑不需要 □需要")])
    _, notes, _, downgrade = _notes_for({"text": "供应商未按采购文件的要求递交响应保证金的，否决其响应。", "quote": "", "category": "commercial"}, c)
    assert notes and downgrade


def test_optional_backup_does_not_exempt_demo_or_encrypted_original():
    c = context_from_chunks([chunk("供应商可以自行选择递交电子备份响应文件。")])
    _, notes, _, downgrade = _notes_for({"text": "供应商应按照采购文件的要求递交备份响应文件，具体要求见磋商须知前附表。", "quote": "", "category": "format"}, c)
    assert notes and downgrade
    assert "不免除正式电子响应文件" in notes[0] and "演示视频" in notes[0]
    _, notes, _, downgrade = _notes_for({"text": "必须提交演示视频U盘", "quote": "", "category": "format"}, c)
    assert not notes and not downgrade


def test_score_groups_follow_original_table_not_response_categories():
    rows = [chunk("商务部分（10分）", "header1"), chunk("企业资质 | 10", "r1"),
            chunk("技术部分（55分）", "header2"), chunk("增值服务及优惠 | 4", "r2"),
            chunk("价格部分（35分）", "header3"), chunk("报价 | 35", "r3")]
    for i, row in enumerate(rows):
        row['metadata'] = {'table': {'index': 3, 'row': i + 1}}
    groups = context_from_chunks(rows)['scoring_sections']
    assert [x['points'] for x in groups] == ['10', '55', '35']
    assert groups[1]['member_chunk_ids'] == ['r2']


def test_validity_is_shared_source_fact_without_inventing_empty_values():
    rows = [chunk("17.1 | 响应有效期 | 90日（从响应截止之日算起）。"),
            chunk("43 | 履约保证金 | 有效期不少于90日", "c2"),
            chunk("17.1 | 响应有效期 | 【】日", "c3")]
    facts = context_from_chunks(rows)['project_facts']
    assert len(facts) == 1 and facts[0]['value'] == '90日（从响应截止之日算起）。'
