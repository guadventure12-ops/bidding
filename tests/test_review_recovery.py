"""Pure adversarial contracts for narrow review-output normalization."""
from copy import deepcopy

import pytest

from app import provider, workflow


BATCH = [
    {'target_id': 'R:alpha', 'target_type': 'requirement', 'requirement_id': 'alpha', 'title': '目标甲'},
    {'target_id': 'S:beta:0', 'target_type': 'section', 'section_id': 'beta', 'title': '目标乙'},
]
KNOWN = [
    {'target_id': 'R:alpha', 'verdict': 'supported', 'reason': '企业原文支持甲的全部陈述。'},
    {'target_id': 'S:beta:0', 'verdict': 'gap', 'reason': '乙仍明确待补产品证明。'},
]


def normalize(rows):
    return workflow._normalize_review_result(BATCH, {'assessments': deepcopy(rows)})


def assert_rejected(rows):
    normalized, corrections = normalize(rows)
    assert corrections == []
    with pytest.raises(provider.ProviderError):
        workflow._validate_review_result(BATCH, normalized)
    return normalized


def test_complete_unique_targets_allow_only_exact_redundant_unknown_rows():
    unknown = {**KNOWN[0], 'target_id': 'R:not-in-this-batch'}
    raw = {'assessments': [KNOWN[1], unknown, KNOWN[0]], '_usage': {'total_tokens': 99}}
    saved = deepcopy(raw)
    result, corrections = workflow._normalize_review_result(BATCH, raw)
    assert raw == saved  # Diagnostic evidence must retain the original raw output.
    assert result['_usage'] == raw['_usage']
    assert result['assessments'] == [KNOWN[1], KNOWN[0]]
    assert len(corrections) == 1 and corrections[0]['discarded_row'] == unknown
    assert corrections[0]['identical_to_targets'] == ['R:alpha']
    findings = workflow._validate_review_result(BATCH, result)
    assert len(findings) == 1 and findings[0]['verdict'] == 'gap'


def test_multiple_proven_duplicate_extras_preserve_every_known_verdict():
    extras = [{**r, 'target_id': 'R:extra-' + str(i)} for i, r in enumerate(KNOWN)]
    result, corrections = normalize([*extras, *KNOWN])
    assert result['assessments'] == KNOWN and len(corrections) == 2
    assert [r['verdict'] for r in result['assessments']] == ['supported', 'gap']


def test_exact_batch_aliases_resolve_without_mutating_reasons_or_verdicts():
    rows = [{**KNOWN[1], 'target_id': 'T02'}, {**KNOWN[0], 'target_id': 'T01'}]
    result, corrections = normalize(rows)
    assert result['assessments'] == [KNOWN[1], KNOWN[0]] and corrections == []
    assert workflow._validate_review_result(BATCH, result)[0]['verdict'] == 'gap'


@pytest.mark.parametrize('alias', ['T1', 't01', ' T01', 'T01 ', 'T03'])
def test_alias_resolution_does_not_guess_missing_target(alias):
    rows = [{**KNOWN[0], 'target_id': alias}, KNOWN[1]]
    result = assert_rejected(rows)
    assert result['assessments'][0]['target_id'] == alias


def test_unknown_duplicate_must_not_fill_a_missing_expected_target():
    assert_rejected([KNOWN[0], {**KNOWN[1], 'target_id': 'R:nearly-beta'}])


@pytest.mark.parametrize('duplicate', [
    KNOWN[0],
    {**KNOWN[0], 'verdict': 'gap', 'reason': '与已有甲结论冲突。'},
    {**KNOWN[0], 'target_id': 'T01'},
])
def test_known_duplicate_or_alias_collision_cannot_be_dropped(duplicate):
    assert_rejected([*KNOWN, duplicate])


@pytest.mark.parametrize('delta', [
    {'reason': KNOWN[0]['reason'] + ' '},
    {'reason': KNOWN[0]['reason'].replace('。', '!')},
    {'verdict': 'uncertain'},
    {'reason': '未知独有事项应由模型实际评估。'},
])
def test_unknown_extra_requires_literal_equality_not_semantic_similarity(delta):
    assert_rejected([*KNOWN, {**KNOWN[0], 'target_id': 'R:extra', **delta}])


@pytest.mark.parametrize('extra', [
    None, 'not-a-row', 7,
    {'verdict': 'supported', 'reason': KNOWN[0]['reason']},
    {**KNOWN[0], 'target_id': None},
    {**KNOWN[0], 'target_id': 123},
    {**KNOWN[0], 'target_id': ''},
    {**KNOWN[0], 'target_id': 'R:extra', 'verdict': 'pass'},
    {**KNOWN[0], 'target_id': 'R:extra', 'reason': ''},
    {**KNOWN[0], 'target_id': 'R:extra', 'reason': '   '},
])
def test_malformed_extra_is_not_silently_deleted(extra):
    assert_rejected([*KNOWN, extra])


def test_all_extras_must_be_proven_redundant_before_any_are_deleted():
    rows = [*KNOWN, {**KNOWN[0], 'target_id': 'R:redundant'},
            {**KNOWN[1], 'target_id': 'R:unique', 'reason': '独有事项。'}]
    result = assert_rejected(rows)
    assert result['assessments'] == rows


@pytest.mark.parametrize('invalid', [{'verdict': 'pass'}, {'reason': ''}, {'reason': None}])
def test_invalid_known_target_blocks_extra_cleanup(invalid):
    rows = [{**KNOWN[0], **invalid}, KNOWN[1], {**KNOWN[1], 'target_id': 'R:extra'}]
    assert_rejected(rows)
