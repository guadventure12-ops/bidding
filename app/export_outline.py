"""Render the persisted chapter outline, with a legacy import adapter.

The section IDs, facts, Markdown tables and inner headings are preserved.  The
renderer decides how far to demote inner headings underneath the returned H2.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import re


_PART = re.compile(r"^(?P<base>.+?)\s*[（(](?P<number>[1-9]\d{0,2})[）)]\s*$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_NUMBER = re.compile(r"^(?:第[一二三四五六七八九十百千万零〇\d]+[章节部分]+\s*|[（(][一二三四五六七八九十百\d]+[）)]\s*|[一二三四五六七八九十百]+[、.．]\s*|\d+(?:\.\d+)*[、.)．]\s*)")
_LOCATOR = re.compile(r"(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|\b(?:req|requirement|doc|chunk|section)[_-][a-z0-9_-]+|(?:采购|招标|原文|来源)(?:定位|位置|条款编号)|(?:原文|来源)(?:页码|文件)|^第\s*\d+\s*页)", re.I)


def _plain_heading(line: str) -> str | None:
    """Recognize a Markdown heading (including a wholly bold heading)."""
    match = _HEADING.match(line)
    value = match.group(1).strip() if match else line.strip()
    if value.startswith("**") and value.endswith("**") and len(value) > 4:
        return value[2:-2].strip()
    return value if match else None


def _without_duplicate_heading(content: str, title: str) -> str:
    """Only remove an exact first nonblank heading; never strip later text."""
    lines = content.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if _plain_heading(line) == title.strip():
            del lines[index]
        break
    return "".join(lines)


def _topic(value: str, base: str) -> str | None:
    value = value.strip().strip("*_ ")
    value = _NUMBER.sub("", value).strip()
    if value.startswith("关于"):
        value = value[2:].strip()
    value = value.rstrip("：:。；; ")
    if not value or value == base or _PART.match(value) or _LOCATOR.search(value):
        return None
    # Use actual heading labels, not arbitrary body paragraphs or locator text.
    if len(value) > 52 or "|" in value or re.search(r"https?://|[。！？!?；;]", value):
        return None
    if value in {"招标要求", "采购要求", "原文位置", "来源", "正文", "响应", "响应内容", "投标响应"}:
        return None
    return value


def _themes(section: dict, base: str) -> list[str]:
    """Read only labels already present in the body; never infer capabilities."""
    title = str(section.get("title") or "").strip()
    themes: list[str] = []
    fenced = False
    first = True
    for line in str(section.get("content") or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced or not stripped:
            continue
        heading = _plain_heading(line)
        if first:
            first = False
            if heading:
                # A real subtitle after the section's leading heading remains
                # in content; it also supplies a meaningful exported part title.
                for prefix in (title, base):
                    if not prefix or not heading.startswith(prefix):
                        continue
                    rest = heading[len(prefix):]
                    if re.match(r"^\s*(?:——|—|--|：|:|[－-])\s*\S", rest):
                        theme = _topic(re.sub(r"^\s*(?:——|—|--|：|:|[－-])\s*", "", rest), base)
                        if theme:
                            return [theme]
        # Ordinary numbered clauses can be factual assertions, not heading
        # labels. Never promote such body sentences into a new subtitle.
        if heading:
            theme = _topic(heading, base)
            if theme and theme not in themes:
                themes.append(theme)
    return themes


def _ordinal(value: int) -> str:
    digits = "零一二三四五六七八九"
    if value < 10:
        return digits[value]
    if value < 100:
        tens, ones = divmod(value, 10)
        return (digits[tens] if tens > 1 else "") + "十" + (digits[ones] if ones else "")
    return str(value)


def _part_title(themes: list[str], used: set[str]) -> str:
    candidates = []
    if len(themes) >= 2 and len('、'.join(themes[:2])) <= 64:
        candidates.append("、".join(themes[:2]))
    candidates.extend(themes)
    for candidate in candidates:
        if candidate not in used:
            return candidate
    # This labels structure only, rather than inventing a missing business topic.
    index = 1
    while True:
        candidate = "补充说明" if index == 1 else "补充说明之" + _ordinal(index)
        if candidate not in used:
            return candidate
        index += 1


def split_families(sections: list[dict]) -> set[str]:
    matches=[_PART.match(str(section.get('title') or '').strip()) for section in sections]
    counts=Counter(match['base'].strip() for match in matches if match)
    return {base for base,count in counts.items() if count>=2}


def outline_sections(sections: list[dict], family_names=()) -> list[dict]:
    """Return ordered H1 groups and their export-only H2 parts.

    A lone parenthesized title is kept as-is.  Only families with at least two
    numbered section titles are grouped, at their first position in the input.
    Numbering is treated as a split indicator, never as a sort key.
    """
    originals = [deepcopy(section) for section in sections]
    matches = [_PART.match(str(section.get("title") or "").strip()) for section in originals]
    counts = Counter(match.group("base").strip() for match in matches if match)
    groups: list[dict] = []
    families: dict[str, dict] = {}
    used_titles: dict[str, set[str]] = {}
    for section, match in zip(originals, matches):
        title = str(section.get("title") or "").strip()
        if section.get('outline_group_id'):
            key=('persisted',section['outline_group_id'])
            if key not in families:
                families[key]={'id':section['outline_group_id'],'title':section['outline_group_title'],'grouped':True,'parts':[]}
                groups.append(families[key])
            body=str(section.get('content') or '')
            for alias in (section.get('legacy_title'),title):
                if alias:body=_without_duplicate_heading(body,alias)
            families[key]['parts'].append({'title':title,'section':section,'content':body})
            continue
        base = match.group("base").strip() if match else title
        grouped = bool(match and (counts[base] >= 2 or base in family_names))
        body = str(section.get("content") or "")
        part = {"title": None, "section": section, "content": _without_duplicate_heading(body, title)}
        if grouped:
            if base not in families:
                families[base] = {"title": base, "grouped": True, "parts": []}
                used_titles[base] = set()
                groups.append(families[base])
            part["title"] = _part_title(_themes(section, base), used_titles[base])
            used_titles[base].add(part["title"])
            families[base]["parts"].append(part)
        else:
            groups.append({"title": title, "grouped": False, "parts": [part]})
    return groups


def part_body(part: dict) -> str:
    """A source subtitle promoted to H2 need not appear as a second H3."""
    content=part['content']
    if not part.get('title'):
        return content
    original=str(part['section'].get('legacy_title') or part['section'].get('title') or '').strip()
    lines=content.splitlines(keepends=True)
    for index,line in enumerate(lines):
        if not line.strip():continue
        heading=_plain_heading(line)
        if heading and original and heading.startswith(original):
            rest=re.sub(r'^\s*(?:——|—|--|：|:|[－-])\s*','',heading[len(original):])
            if rest==part['title']:
                del lines[index]
        elif heading and _topic(heading,'')==part['title']:
            # The existing first topic itself became the H2; keep its prose
            # directly below it instead of repeating the same label as an H3.
            del lines[index]
        break
    return ''.join(lines)
