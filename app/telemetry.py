"""Bounded OTLP/HTTP JSON telemetry for the existing DeepSeek transport.

Full experimental prompts/responses, no credentials/headers. No model retries,
network calls, or disk spooling on business threads. Reuses existing httpx.
"""
from __future__ import annotations
import base64
import contextvars
import datetime
import functools
import hashlib
import json
import os
from pathlib import Path
import queue
import threading
import time
import uuid
from urllib.parse import urlsplit
import httpx

ROOT = Path(__file__).resolve().parents[1]
CURRENT = contextvars.ContextVar('bidding_telemetry', default=None)
_instance = None
_init_lock = threading.Lock()

def config():
    cfg = {}
    path = ROOT / '.langfuse.env'
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1); cfg[k.strip()] = v.strip().strip('"').strip("'")
    cfg.update({k: v for k, v in os.environ.items() if k.startswith('LANGFUSE_')})
    if os.environ.get('MX_TESTING') == '1' and os.environ.get('MX_LANGFUSE_TEST_EXPORT') != '1':
        cfg['LANGFUSE_ENABLED'] = 'false'
    return cfg

def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str)

def value(v):
    if isinstance(v, bool): return {'boolValue': v}
    if isinstance(v, int): return {'intValue': str(v)}
    if isinstance(v, float): return {'doubleValue': v}
    if isinstance(v, (list, tuple)): return {'arrayValue': {'values': [value(x) for x in v]}}
    return {'stringValue': str(v)}

class Exporter:
    MAX_BYTES = 32 * 1024 * 1024
    MAX_SPAN = 4 * 1024 * 1024
    def __init__(self, cfg):
        self.enabled = cfg.get('LANGFUSE_ENABLED', '').lower() in ('1', 'true', 'yes')
        self.base = cfg.get('LANGFUSE_BASE_URL', 'http://127.0.0.1:13000').rstrip('/')
        self.environment = cfg.get('LANGFUSE_ENVIRONMENT', 'bidding-experiment')
        self.public_key = cfg.get('LANGFUSE_PUBLIC_KEY', '')
        self.secret_key = cfg.get('LANGFUSE_SECRET_KEY', '')
        self.queue = queue.Queue(maxsize=128)
        self.lock = threading.Lock(); self.bytes = 0; self.closed = False
        self.stats = dict(enqueued=0, accepted=0, failed=0, dropped=0, last_http_status=None, last_error=None)
        p = urlsplit(self.base)
        if self.enabled and (p.scheme != 'http' or p.hostname != '127.0.0.1' or p.path or p.username or p.password or p.query or p.fragment or not self.public_key or not self.secret_key):
            self.enabled = False; self.stats['last_error'] = 'invalid_config_requires_loopback_and_project_keys'
        self.thread = None
        if self.enabled:
            self.thread = threading.Thread(target=self._work, name='bidding-langfuse', daemon=True)
            self.thread.start()

    def enqueue(self, row):
        if not self.enabled or self.closed: return
        try:
            data = compact({'resourceSpans': [{'resource': {'attributes': [{'key': 'service.name', 'value': value('bidding-agent')}]}, 'scopeSpans': [{'scope': {'name': 'bidding-langfuse', 'version': '1.0.0'}, 'spans': [row]}]}]}).encode()
            with self.lock:
                if self.closed or len(data) > self.MAX_SPAN or self.bytes + len(data) > self.MAX_BYTES:
                    self.stats['dropped'] += 1; return
                try: self.queue.put_nowait(data)
                except queue.Full: self.stats['dropped'] += 1; return
                self.bytes += len(data); self.stats['enqueued'] += 1
        except Exception:
            with self.lock: self.stats['dropped'] += 1

    def _work(self):
        auth = 'Basic ' + base64.b64encode((self.public_key + ':' + self.secret_key).encode()).decode()
        while not self.closed or not self.queue.empty():
            try: data = self.queue.get(timeout=.1)
            except queue.Empty: continue
            try:
                with httpx.Client(timeout=httpx.Timeout(3, connect=1, write=2, pool=1), trust_env=False, follow_redirects=False) as client:
                    response = client.post(self.base + '/api/public/otel/v1/traces', content=data, headers={'Authorization': auth, 'Content-Type': 'application/json', 'x-langfuse-ingestion-version': '4'})
                    ok = 200 <= response.status_code < 300
                    if ok:
                        partial = response.json().get('partialSuccess', {})
                        ok = int(partial.get('rejectedSpans', 0)) == 0 and not partial.get('errorMessage')
                    with self.lock:
                        self.stats['accepted' if ok else 'failed'] += 1
                        self.stats['last_http_status'] = response.status_code
                        self.stats['last_error'] = None if ok else 'ingestion_rejected'
            except Exception as exc:
                with self.lock:
                    self.stats['failed'] += 1; self.stats['last_error'] = type(exc).__name__
            finally:
                with self.lock: self.bytes -= len(data)
                self.queue.task_done()

    def flush(self, timeout=4):
        end = time.monotonic() + timeout
        while self.queue.unfinished_tasks and time.monotonic() < end: time.sleep(.02)
        return self.queue.unfinished_tasks == 0

    def shutdown(self):
        self.closed = True
        if self.thread: self.thread.join(timeout=4)
        # No persistent replay queue. Daemon exit never holds the application open.

    def status(self):
        with self.lock:
            return {'enabled': self.enabled, 'base_url': self.base, 'environment': self.environment,
                    'capture': 'full_prompts_and_responses', 'pending': self.queue.unfinished_tasks,
                    'pending_bytes': self.bytes, 'closed': self.closed, **self.stats}

def get():
    global _instance
    with _init_lock:
        if _instance is None:
            try: _instance = Exporter(config())
            except Exception: _instance = Exporter({'LANGFUSE_ENABLED': 'false'})
        return _instance

class Span:
    def __init__(self, name, typ='span', input=None, metadata=None, trace_id=None):
        self.exporter = get(); self.name = name; self.typ = typ; self.input = input
        self.metadata = dict(metadata or {}); self.explicit_trace = trace_id
        self.attributes = {}; self.result = None; self.failure = None; self.active = False
    def __enter__(self):
        self.parent = None if self.explicit_trace else CURRENT.get()
        self.trace_id = self.explicit_trace or (self.parent.trace_id if self.parent else uuid.uuid4().hex)
        self.span_id = uuid.uuid4().hex[:16]; self.start = time.time_ns()
        inherited = dict(self.parent.metadata) if self.parent else {}
        inherited.update(self.metadata); self.metadata = inherited
        self.trace_name = self.parent.trace_name if self.parent else self.name
        self.token = CURRENT.set(self); self.active = True
        return self
    def output(self, result): self.result = result
    def attr(self, key, val): self.attributes[key] = val
    def error(self, message): self.failure = str(message)
    def usage(self, raw):
        if not isinstance(raw, dict): return
        mapped = {target: raw[source] for source, target in [('prompt_tokens', 'input'), ('completion_tokens', 'output'), ('total_tokens', 'total')] if isinstance(raw.get(source), int)}
        if mapped: self.attr('langfuse.observation.usage_details', compact(mapped))
        if raw: self.attr('langfuse.observation.metadata.provider_usage', compact(raw))
    def first_token(self):
        key = 'langfuse.observation.completion_start_time'
        if key not in self.attributes:
            self.attr(key, datetime.datetime.now(datetime.timezone.utc).isoformat())
    def __exit__(self, exc_type, exc, tb):
        CURRENT.reset(self.token); self.active = False
        if exc: self.failure = str(exc) if exc_type.__module__.endswith('provider') else exc_type.__name__
        if not self.exporter.enabled: return False
        try:
            attrs = {'langfuse.trace.name': self.trace_name, 'langfuse.environment': self.exporter.environment,
                     'langfuse.trace.tags': ['bidding-agent'], 'langfuse.observation.type': self.typ,
                     'langfuse.session.id': str(self.metadata.get('project_id') or self.metadata.get('job_id') or self.trace_id),
                     'langfuse.user.id': 'local-developer',
                     'langfuse.observation.input': compact(self.input), 'langfuse.observation.output': compact(self.result), **self.attributes}
            for key, val in self.metadata.items(): attrs['langfuse.observation.metadata.' + key] = compact(val)
            if self.failure:
                attrs['langfuse.observation.level'] = 'ERROR'; attrs['langfuse.observation.status_message'] = self.failure
            row = {'traceId': self.trace_id, 'spanId': self.span_id, 'name': self.name, 'kind': 1,
                   'startTimeUnixNano': str(self.start), 'endTimeUnixNano': str(time.time_ns()),
                   'attributes': [{'key': k, 'value': value(v)} for k, v in attrs.items()],
                   'status': {'code': 2 if self.failure else 1}}
            if self.parent: row['parentSpanId'] = self.parent.span_id
            self.exporter.enqueue(row)
        except Exception:
            pass  # Never replace a business return value or exception.
        return False

def span(*args, **kwargs): return Span(*args, **kwargs)

def submit(pool, fn, *args, **kwargs):
    return pool.submit(contextvars.copy_context().run, functools.partial(fn, *args, **kwargs))

def job_trace_id(job_id): return hashlib.sha256(('bidding-job:' + str(job_id)).encode()).hexdigest()[:32]

def job_run(fn):
    @functools.wraps(fn)
    def wrapped(job_id):
        from . import db
        job = db.one('SELECT * FROM jobs WHERE id=?', (job_id,)) or {}
        meta = {'job_id': job_id, 'project_id': job.get('project_id'), 'mode': job.get('mode')}
        with span('bidding-' + str(job.get('mode', 'job')), 'agent', meta, meta, trace_id=job_trace_id(job_id)) as root:
            try: return fn(job_id)
            finally:
                final = db.one('SELECT status,error,result FROM jobs WHERE id=?', (job_id,)) or {}
                root.output(final)
                if final.get('status') in ('failed', 'cancelled'): root.error(final.get('error') or final['status'])
    return wrapped

def model_call(fn):
    @functools.wraps(fn)
    def wrapped(job_id, mode, unit_key, system, prompt, inputs, cancel=None):
        with span('model-' + mode, input={'system': system, 'user': prompt}, metadata={'job_id': job_id, 'mode': mode, 'unit_key': unit_key}) as observation:
            result = fn(job_id, mode, unit_key, system, prompt, inputs, cancel)
            observation.output(result)
            observation.attr('langfuse.observation.metadata.cache_hit', bool(result.get('_cached_response')))
            return result
    return wrapped

def validation(job_id, mode, result, reason):
    with span('validation-failed', 'guardrail', metadata={'job_id': job_id, 'mode': mode, 'request_sha256': result.get('_request_sha256') if isinstance(result, dict) else None}) as s:
        s.error(reason); s.output({'valid': False, 'reason': reason})

def shutdown():
    if _instance: _instance.shutdown()
