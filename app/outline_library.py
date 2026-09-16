"""User maintained H1 modules. Titles and scores are suggestions, never evidence."""
from __future__ import annotations
import copy
import hashlib
import json
import re
from . import db

VERSION = 'outline-library-2'
KEY = 'outline_library'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def title(value):
    if not isinstance(value, str):
        raise ValueError('目录标题应为文字')
    value = value.strip()
    if not value or len(value) > 100 or '\n' in value or '\r' in value:
        raise ValueError('目录标题须为 1 至 100 字的一行文字')
    if re.match(r'^(?:#{1,6}\s|[（(]?(?:[一二三四五六七八九十百]+|\d+)[）).、．]\s*)', value):
        raise ValueError('目录标题不填写数字标号，系统会根据顺序自动编号')
    return value


def defaults():
    names = [
        ('understanding', '需求理解与解决方案', ['需求理解', '建设目标', '项目背景']),
        ('architecture', '整体设计方案', ['架构', '部署', '安全', '权限']),
        ('integration', '系统集成方案', ['接口', '集成', '对接']),
        ('features', '功能模块设计及系统功能演示', ['功能', '模块', '演示']),
        ('implementation', '项目进度安排', ['实施', '进度', '工期', '上线']),
        ('quality', '质量保证与工期保障', ['质量', '测试', '验收']),
        ('service', '售后运维服务方案', ['售后', '运维', '维保']),
        ('training', '知识转移与培训', ['培训', '知识转移', '教材']),
    ]
    return {'version': VERSION, 'modules': [
        {'id': 'module-' + key, 'title': name, 'description': '',
         'domains': ['archive', 'expense'], 'keywords': words, 'children': []} for key, name, words in names],
        'presets': [{'id': 'preset-' + str(i), 'name': '预设' + '一二三'[i-1], 'module_ids': []} for i in range(1, 4)]}


def _value(conn=None):
    row = conn.execute('SELECT value FROM settings WHERE key=?', (KEY,)).fetchone() if conn is not None else db.one('SELECT value FROM settings WHERE key=?', (KEY,))
    return json.loads(row['value']) if row else defaults()


def get_library():
    value = _value()
    # Old stored libraries retain their exact revision until an explicit save.
    # Display defaults must not silently mutate a frozen project or the DB.
    display = copy.deepcopy(value)
    for module in display['modules']:
        module.setdefault('children', [])
    return {**display, 'revision': digest(value)}


def _validate(modules, presets):
    if not isinstance(modules, list) or len(modules) > 200:
        raise ValueError('通用模块须为列表，最多 200 项')
    result = []
    ids, names = set(), set()
    for item in modules:
        if not isinstance(item, dict): raise ValueError('通用模块格式无效')
        identity = item.get('id')
        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', identity) or identity in ids:
            raise ValueError('通用模块 ID 无效或重复')
        name = title(item.get('title'))
        norm = re.sub(r'\s+', '', name).casefold()
        if norm in names: raise ValueError('通用模块标题重复')
        description = item.get('description', '')
        if not isinstance(description, str) or len(description) > 4000: raise ValueError('模块说明最多 4000 字')
        domains = item.get('domains', [])
        keywords = item.get('keywords', [])
        if not isinstance(domains, list) or any(not isinstance(d, str) for d in domains) or not set(domains) <= {'archive', 'expense', 'general'}: raise ValueError('产品范围无效')
        if not isinstance(keywords, list) or len(keywords) > 50 or any(not isinstance(k, str) or not k.strip() or len(k) > 80 for k in keywords):
            raise ValueError('关键词须为最多 50 项的短文本列表')
        children = item.get('children', [])
        if not isinstance(children, list) or len(children) > 100:
            raise ValueError('每个通用模块最多设置100个二级目录')
        leaves, leaf_ids, leaf_titles = [], set(), set()
        for child in children:
            if not isinstance(child, dict): raise ValueError('二级目录格式无效')
            child_id = child.get('id')
            if not isinstance(child_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', child_id) or child_id in leaf_ids:
                raise ValueError('二级目录ID无效或重复')
            child_title = title(child.get('title'))
            if re.sub(r'\s+', '', child_title).casefold() in leaf_titles:
                raise ValueError('同一通用模块下的二级目录标题重复')
            leaf_ids.add(child_id); leaf_titles.add(re.sub(r'\s+', '', child_title).casefold())
            leaves.append({'id': child_id, 'title': child_title})
        result.append({'id': identity, 'title': name, 'description': description.strip(),
                       'domains': list(dict.fromkeys(domains)), 'keywords': list(dict.fromkeys(k.strip() for k in keywords)),
                       'children': leaves})
        ids.add(identity); names.add(norm)
    if not isinstance(presets, list) or len(presets) != 3: raise ValueError('须保留三项预设')
    configured = []
    for i, item in enumerate(presets, 1):
        if not isinstance(item, dict) or item.get('id') != 'preset-' + str(i): raise ValueError('预设 ID 或顺序无效')
        selected = item.get('module_ids', [])
        if not isinstance(selected, list) or any(not isinstance(x, str) for x in selected) or len(set(selected)) != len(selected) or not set(selected) <= ids:
            raise ValueError('预设包含失效或重复的通用模块 ID')
        configured.append({'id': item['id'], 'name': title(item.get('name')), 'module_ids': selected})
    return {'version': VERSION, 'modules': result, 'presets': configured}


def save_library(revision, modules, presets):
    value = _validate(modules, presets)
    from . import workflow
    with workflow.JOB_LOCK, db.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        current = _value(conn)
        if revision != digest(current): raise ValueError('通用目录库已变化，请刷新后再保存')
        if current != value:
            conn.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                         (KEY, json.dumps(value, ensure_ascii=False)))
    return {**copy.deepcopy(value), 'revision': digest(value)}
