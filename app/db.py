"""Local, durable SQLite storage. Every operation uses a short transaction."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("MX_DATA_DIR", ROOT / "data")).resolve()
JSON_FIELDS = {"warnings", "metadata", "evidence_ids", "requirement_ids", "payload", "checkpoint", "result", "details", "checks", "findings", "quotation"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def uid():
    return uuid.uuid4().hex


def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA / "bidding.sqlite3", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init():
    for folder in ("documents", "exports", "temp"):
        (DATA / folder).mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, domain TEXT NOT NULL DEFAULT 'archive',
            company_name TEXT NOT NULL DEFAULT '', status TEXT DEFAULT 'draft',
            analysis_status TEXT DEFAULT 'pending', analysis_fingerprint TEXT DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), name TEXT NOT NULL,
            path TEXT NOT NULL, source_path TEXT DEFAULT '', format TEXT DEFAULT '',
            sha256 TEXT NOT NULL, size INTEGER DEFAULT 0, source_type TEXT DEFAULT 'tender',
            status TEXT DEFAULT 'pending', scope TEXT DEFAULT 'general', valid_until TEXT DEFAULT '',
            parse_status TEXT DEFAULT 'pending', page_count INTEGER DEFAULT 0,
            text_chars INTEGER DEFAULT 0, warnings TEXT DEFAULT '[]', metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS documents_project ON documents(project_id);
        CREATE INDEX IF NOT EXISTS documents_sha ON documents(sha256);
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL, text TEXT NOT NULL, locator TEXT NOT NULL, kind TEXT DEFAULT 'paragraph',
            page INTEGER, metadata TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id, ordinal);
        CREATE TABLE IF NOT EXISTS requirements (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), document_id TEXT REFERENCES documents(id),
            chunk_id TEXT REFERENCES chunks(id), number TEXT DEFAULT '', category TEXT DEFAULT 'technical',
            title TEXT NOT NULL, text TEXT NOT NULL, quote TEXT DEFAULT '', locator TEXT DEFAULT '',
            mandatory INTEGER DEFAULT 0, score TEXT DEFAULT '', status TEXT DEFAULT 'pending',
            response TEXT DEFAULT '', evidence_ids TEXT DEFAULT '[]', origin TEXT DEFAULT 'ai',
            verified INTEGER DEFAULT 0, updated_at TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS requirements_project ON requirements(project_id);
        CREATE TABLE IF NOT EXISTS sections (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), ordinal INTEGER NOT NULL,
            title TEXT NOT NULL, content TEXT DEFAULT '', status TEXT DEFAULT 'draft',
            requirement_ids TEXT DEFAULT '[]', evidence_ids TEXT DEFAULT '[]', user_edited INTEGER DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS sections_project ON sections(project_id, ordinal);
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), mode TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued', progress REAL DEFAULT 0, message TEXT DEFAULT '',
            payload TEXT DEFAULT '{}', checkpoint TEXT DEFAULT '{}', result TEXT DEFAULT '{}', error TEXT DEFAULT '',
            cancel_requested INTEGER DEFAULT 0, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT REFERENCES jobs(id), level TEXT DEFAULT 'info',
            message TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS checks (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), code TEXT NOT NULL,
            severity TEXT NOT NULL, message TEXT NOT NULL, details TEXT DEFAULT '{}', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS exports (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), name TEXT NOT NULL,
            path TEXT NOT NULL, format TEXT NOT NULL, final INTEGER DEFAULT 0,
            warnings TEXT DEFAULT '[]', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reviews (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
            fingerprint TEXT NOT NULL, status TEXT NOT NULL, findings TEXT DEFAULT '[]',
            model TEXT DEFAULT '', targets INTEGER DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS reviews_project ON reviews(project_id,created_at);
        CREATE TABLE IF NOT EXISTS project_snapshots (
            id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), document_id TEXT REFERENCES documents(id),
            label TEXT NOT NULL, payload TEXT DEFAULT '{}', created_at TEXT NOT NULL
        );
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
        for column in ("project_number", "buyer", "deadline", "metadata", "quotation"):
            if column not in columns:
                default = "{}" if column in ("metadata", "quotation") else ""
                conn.execute(f"ALTER TABLE projects ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'")
        section_columns = {row[1] for row in conn.execute("PRAGMA table_info(sections)")}
        for column in ('outline_group_id','outline_group_title','legacy_title'):
            if column not in section_columns:
                conn.execute(f"ALTER TABLE sections ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        from . import product_modules
        product_modules.initialize(conn)
        conn.execute("UPDATE jobs SET status='interrupted',message='服务重启中断了任务；可重试并复用已完成检查点',finished_at=? WHERE status IN ('queued','running')", (now(),))
        conn.execute("""UPDATE projects SET analysis_status='partial',analysis_fingerprint='',updated_at=?
            WHERE analysis_status='analyzing' AND EXISTS
            (SELECT 1 FROM jobs WHERE jobs.project_id=projects.id AND mode='analyze'
             AND status IN ('failed','cancelled','interrupted'))
            AND NOT EXISTS (SELECT 1 FROM jobs WHERE jobs.project_id=projects.id AND mode='analyze'
                            AND status IN ('queued','running'))""", (now(),))


def decode(row):
    if row is None:
        return None
    item = dict(row)
    for key in JSON_FIELDS & item.keys():
        try:
            item[key] = json.loads(item[key])
        except (TypeError, ValueError):
            item[key] = {} if key in {"metadata", "payload", "checkpoint", "result", "details"} else []
    return item


def all(sql, params=()):
    with connect() as conn:
        return [decode(row) for row in conn.execute(sql, params).fetchall()]


def one(sql, params=()):
    with connect() as conn:
        return decode(conn.execute(sql, params).fetchone())


def execute(sql, params=()):
    with connect() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid


def insert(table, values):
    cols = list(values)
    encoded = [json.dumps(values[k], ensure_ascii=False) if k in JSON_FIELDS else values[k] for k in cols]
    execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", encoded)
    return values.get("id")


def update(table, id, values):
    cols = list(values)
    encoded = [json.dumps(values[k], ensure_ascii=False) if k in JSON_FIELDS else values[k] for k in cols]
    execute(f"UPDATE {table} SET {','.join(k+'=?' for k in cols)} WHERE id=?", encoded + [id])


def get_settings():
    defaults = {
        "base_url": "https://api.deepseek.com", "model": "deepseek-v4-pro", "company_name": "",
        "knowledge_path": "", "tender_path": "",
        "temperature": 0.2, "max_tokens": 8192, "batch_chars": 16000, "ocr": False, "section_approval_threshold": "medium",
    }
    for row in all("SELECT key,value FROM settings"):
        if row["key"] != "api_key_encrypted":
            try:
                defaults[row["key"]] = json.loads(row["value"])
            except ValueError:
                defaults[row["key"]] = row["value"]
    return defaults


def set_setting(key, value):
    execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False)))


def event(job_id, message, level="info"):
    execute("INSERT INTO events(job_id,level,message,created_at) VALUES(?,?,?,?)", (job_id, level, message, now()))


def touch(project_id, **fields):
    if project_id:
        update("projects", project_id, {"updated_at": now(), **fields})
