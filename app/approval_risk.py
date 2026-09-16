"""Chapter content risk and configurable approval gate; never clears findings."""
import hashlib
import json
from . import db

VERSION = 'chapter-risk-1'
LEVELS = {'low':1, 'medium':2, 'high':3}
LABELS = {'low':'低风险', 'medium':'中风险', 'high':'高风险', 'ignore':'无视风险'}
DEFAULT = 'medium'
HIGH_KINDS = {'content_conflict', 'content_gap', 'qualification_declaration', 'body_content_gap'}


def policy():
    value = db.get_settings().get('section_approval_threshold', DEFAULT)
    # Invalid externally edited settings fail closed instead of ignoring risk.
    return value if isinstance(value, str) and value in LABELS else 'low'


def evaluate(row, threshold):
    """The original blockers remain intact for independent delivery checks."""
    active = [t for t in row['todos'] if t.get('status') != 'resolved' and t.get('blocks_content') and t.get('rule_enabled', True)]
    high = row.get('structural_risk', False) or any(t.get('kind') in HIGH_KINDS for t in active)
    level = 'high' if high else 'medium' if row['blockers'] else 'low'
    gate_enabled = row.get('approval_threshold_enabled', True)
    allowed = not gate_enabled or threshold == 'ignore' or LEVELS[level] < LEVELS[threshold]
    reason = ('批准风险阈值规则已关闭，允许人工批准正文；问题继续保留' if not gate_enabled else '当前为无视风险模式，允许人工批准正文，原风险继续保留' if threshold=='ignore' else
              f"{LABELS[level]}低于当前拦截起点{LABELS[threshold]}，可人工批准正文" if allowed else
              f"当前设置从{LABELS[threshold]}起拦截，本章为{LABELS[level]}")
    return {'risk_level':level, 'risk_label':LABELS[level], 'risk_reasons':list(row['blockers']),
            'approval_threshold_enabled':gate_enabled, 'approval_threshold':threshold, 'approval_threshold_label':LABELS[threshold],
            'approval_reason':reason, 'approval_blockers':[] if allowed else [reason],
            'eligible':allowed, 'accepted_risk':allowed and bool(row['blockers']),
            'content_clear':not row['blockers']}


def record(section, assessment):
    content = {k:section.get(k) for k in ('id','title','content','evidence_ids','requirement_ids')}
    return {'policy_version':VERSION, 'threshold':assessment['approval_threshold'],
            'review_rules_revision':assessment.get('review_rules_revision'), 'threshold_enabled':assessment.get('approval_threshold_enabled', True),
            'risk_level':assessment['risk_level'], 'accepted_risk':assessment['accepted_risk'],
            'reasons':assessment['risk_reasons'], 'approved_at':db.now(),
            'content_sha256':hashlib.sha256(json.dumps(content,sort_keys=True,ensure_ascii=False).encode()).hexdigest()}
