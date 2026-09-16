import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest
from app import db, model_jobs, provider, telemetry

class Sink:
    enabled = True
    environment = 'bidding-integration-test'
    def __init__(self): self.rows = []
    def enqueue(self, row): self.rows.append(row)

@pytest.fixture
def sink(monkeypatch):
    obj = Sink(); monkeypatch.setattr(telemetry, '_instance', obj)
    token = telemetry.CURRENT.set(None)
    yield obj
    telemetry.CURRENT.reset(token)

def attrs(row): return {a['key']: next(iter(a['value'].values())) for a in row['attributes']}

def stream_response(finish='stop', usage=True, status=200, broken=False):
    class Response:
        status_code = status
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def iter_lines(self):
            yield 'data: ' + json.dumps({'choices': [{'delta': {'content': '{"answer":"完整试验回答 user@example.invalid"}'}, 'finish_reason': None}]}, ensure_ascii=False)
            if broken: raise httpx.ReadError('transport interrupted')
            event = {'choices': [{'delta': {}, 'finish_reason': finish}]}
            if usage: event['usage'] = {'prompt_tokens': 12, 'completion_tokens': 7, 'total_tokens': 19, 'prompt_cache_hit_tokens': 3}
            yield 'data: ' + json.dumps(event)
            yield 'data: [DONE]'
    return Response()

def transport(monkeypatch, responses):
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def stream(self, *args, **kwargs):
            calls.append(kwargs); return responses.pop(0)
    monkeypatch.setattr(provider.httpx, 'Client', Client)
    monkeypatch.setattr(provider, 'get_key', lambda: 'TEST-TRANSPORT-KEY-NEVER-EXPORTED')
    monkeypatch.setattr(db, 'get_settings', lambda: {'model': 'deepseek-chat', 'base_url': 'https://api.deepseek.com', 'temperature': .2, 'max_tokens': 1000})
    return calls

def test_full_stream_io_usage_and_no_header(sink, monkeypatch):
    calls = transport(monkeypatch, [stream_response()])
    result = provider.chat_json('system full', 'prompt email user@example.invalid')
    assert result['answer'].startswith('完整') and result['_usage']['total_tokens'] == 19
    row = sink.rows[0]; a = attrs(row)
    assert json.loads(a['langfuse.observation.usage_details']) == {'input': 12, 'output': 7, 'total': 19}
    assert 'user@example.invalid' in a['langfuse.observation.input']
    assert json.loads(a['langfuse.observation.output']) == '{"answer":"完整试验回答 user@example.invalid"}'
    assert a['langfuse.observation.completion_start_time']
    assert 'TEST-TRANSPORT-KEY' not in json.dumps(sink.rows)
    assert len(calls) == 1 and int(row['endTimeUnixNano']) >= int(row['startTimeUnixNano'])

def test_missing_usage_is_not_fabricated(sink, monkeypatch):
    transport(monkeypatch, [stream_response(usage=False)])
    provider.chat_json('system', 'prompt')
    assert 'langfuse.observation.usage_details' not in attrs(sink.rows[0])

def test_retry_stays_in_one_trace(sink, monkeypatch):
    calls = transport(monkeypatch, [stream_response(status=503), stream_response()])
    monkeypatch.setattr(provider.time, 'sleep', lambda _: None)
    with telemetry.span('business'):
        provider.chat_json('system', 'prompt')
    generations = [r for r in sink.rows if r['name'].startswith('deepseek')]
    assert len(calls) == 2 and len(generations) == 2
    assert generations[0]['status']['code'] == 2 and generations[1]['status']['code'] == 1
    assert len({r['traceId'] for r in sink.rows}) == 1
    assert all(r['parentSpanId'] == sink.rows[-1]['spanId'] for r in generations)

@pytest.mark.parametrize('broken,finish,kind', [(True,'stop',provider.OutputInterruptedError),(False,'length',provider.OutputTruncatedError)])
def test_partial_output_never_rebills(sink, monkeypatch, broken, finish, kind):
    calls = transport(monkeypatch, [stream_response(broken=broken, finish=finish)])
    with pytest.raises(kind): provider.chat_json('system', 'prompt')
    assert len(calls) == 1 and sink.rows[0]['status']['code'] == 2
    assert '完整试验回答' in attrs(sink.rows[0])['langfuse.observation.output']

def test_cancel_does_not_start_model_request(sink, monkeypatch):
    calls = transport(monkeypatch, [])
    with pytest.raises(provider.Cancelled): provider.chat_json('system', 'prompt', cancel=lambda: True)
    assert calls == [] and sink.rows == []

def test_threads_keep_parent_and_job_context(sink):
    barrier = threading.Barrier(2)
    def child(label):
        with telemetry.span(label) as s:
            barrier.wait(timeout=3); s.output(label)
    with ThreadPoolExecutor(max_workers=2) as pool:
        with telemetry.span('root', metadata={'job_id': 'synthetic-job', 'project_id': 'synthetic-project'}):
            a = telemetry.submit(pool, child, 'a'); b = telemetry.submit(pool, child, 'b'); a.result(); b.result()
    root = sink.rows[-1]
    assert all(r['traceId'] == root['traceId'] and r['parentSpanId'] == root['spanId'] for r in sink.rows[:-1])
    assert all(json.loads(attrs(r)['langfuse.observation.metadata.job_id']) == 'synthetic-job' for r in sink.rows)
    assert telemetry.CURRENT.get() is None

def test_parallel_jobs_do_not_share_trace(sink):
    def job(label):
        with telemetry.span(label, trace_id=telemetry.job_trace_id(label)):
            with telemetry.span('child'): pass
    with ThreadPoolExecutor(max_workers=2) as pool: list(pool.map(job, ['a','b']))
    for label in ('a','b'):
        group = [r for r in sink.rows if r['traceId'] == telemetry.job_trace_id(label)]
        assert len(group) == 2 and group[0]['parentSpanId'] == group[1]['spanId']

def test_cache_does_not_create_another_generation(sink, monkeypatch, tmp_path):
    monkeypatch.setattr(db, 'DATA', tmp_path / 'data'); monkeypatch.setenv('MX_TESTING', '1'); db.init()
    calls = transport(monkeypatch, [stream_response()])
    with telemetry.span('root'):
        first = model_jobs.chat_json('job', 'generation', 'unit', 'system', 'prompt', {})
        second = model_jobs.chat_json('job', 'generation', 'unit', 'system', 'prompt', {})
    assert not first['_cached_response'] and second['_cached_response'] and len(calls) == 1
    assert len([r for r in sink.rows if r['name'].startswith('deepseek')]) == 1
    assert any(attrs(r).get('langfuse.observation.metadata.cache_hit') is True for r in sink.rows)

def test_sink_failure_does_not_change_result(sink, monkeypatch):
    transport(monkeypatch, [stream_response()])
    sink.enqueue = lambda row: (_ for _ in ()).throw(RuntimeError('sink down'))
    assert provider.chat_json('system','prompt')['_usage']['total_tokens'] == 19

def test_export_is_nonblocking_and_bounded(monkeypatch):
    entered = threading.Event(); release = threading.Event()
    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def post(self,*args,**kwargs): entered.set(); release.wait(3); raise httpx.ConnectError('down')
    monkeypatch.setattr(telemetry.httpx, 'Client', Client)
    cfg = {'LANGFUSE_ENABLED':'true','LANGFUSE_PUBLIC_KEY':'test-public','LANGFUSE_SECRET_KEY':'test-secret'}
    exp = telemetry.Exporter(cfg)
    try:
        before=time.monotonic(); exp.enqueue({'name':'one'})
        assert time.monotonic()-before < .1 and entered.wait(1)
        exp.MAX_SPAN=10; exp.enqueue({'name':'too-long'})
        assert exp.status()['dropped'] == 1 and not exp.flush(.05)
        release.set(); assert exp.flush(2)
        assert exp.status()['failed'] == 1 and exp.status()['accepted'] == 0
        exp.shutdown(); exp.enqueue({'name':'closed'}); assert exp.status()['enqueued'] == 1
    finally: release.set(); exp.shutdown()

def test_partial_acceptance_is_not_success(monkeypatch):
    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def post(self,*args,**kwargs): return httpx.Response(200,json={'partialSuccess':{'rejectedSpans':'1'}})
    monkeypatch.setattr(telemetry.httpx,'Client',Client)
    exp=telemetry.Exporter({'LANGFUSE_ENABLED':'true','LANGFUSE_PUBLIC_KEY':'test','LANGFUSE_SECRET_KEY':'test'})
    try:
        exp.enqueue({'name':'test'}); assert exp.flush(2)
        assert exp.status()['accepted']==0 and exp.status()['failed']==1
    finally: exp.shutdown()

def test_config_cannot_send_keys_to_remote_host():
    exp=telemetry.Exporter({'LANGFUSE_ENABLED':'true','LANGFUSE_PUBLIC_KEY':'test','LANGFUSE_SECRET_KEY':'test','LANGFUSE_BASE_URL':'https://unknown.invalid'})
    assert not exp.enabled and exp.thread is None
