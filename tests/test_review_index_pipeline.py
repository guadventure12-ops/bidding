"""Index-only regeneration through the real API/worker; provider is a mock."""
import copy
import json
import uuid
from io import BytesIO
from docx import Document

from app import db, provider, workflow
from test_proposal_pipeline import pipeline,create_and_generate,execute_job


def test_index_default_and_custom_regeneration_persist_and_preserve_other_sections(pipeline,monkeypatch):
    client,model,_=pipeline
    pid,detail,_,_=create_and_generate(client)
    section=next(s for s in detail['sections'] if s['outline_group_title']=='评审索引表');sid=section['id']
    baseline={s['id']:copy.deepcopy(s) for s in detail['sections']}
    before_requirements=db.all('SELECT * FROM requirements WHERE project_id=? ORDER BY id',(pid,))
    old_export=client.post(f'/api/projects/{pid}/export',json={'format':'docx','final':False}).json()
    old_bytes=client.get(old_export['url']).content
    count=len(model.calls)
    preview=client.get(f'/api/sections/{sid}/regeneration').json()
    assert '不填补充要求时不调用模型' in preview['notice'] and '调用DeepSeek一次' in preview['notice']
    job=execute_job(client,client.post(f'/api/sections/{sid}/regenerate',json={'revision':preview['revision'],'outline_revision':preview['outline_revision'],
        'request_id':uuid.uuid4().hex,'instruction':'','confirmed':True}))
    assert len(model.calls)==count and job['result']['sections']==1
    refreshed=client.get(f'/api/projects/{pid}').json()
    current=next(s for s in refreshed['sections'] if s['id']==sid)
    assert current['content'].startswith('| 序号 | 评分因素 | 评审标准 | 分值 | 页数 |')
    assert current['status']=='draft'
    for row in refreshed['sections']:
        if row['id']!=sid:assert row==baseline[row['id']]
    calls=[]
    def layout_model(system,prompt,cancel=None):
        calls.append(prompt)
        catalog=json.loads(prompt.split('实际评分数据及允许的列键：\n',1)[1])
        return {'tables':[{'source_table_id':t['table_id'], 'columns':[{'key':'c0','label':'条目编号'},{'key':'c1','label':'评审项目'},
            {'key':'c2','label':'评分标准'},{'key':'c3','label':'分值'},{'key':'chapter','label':'响应章节'},{'key':'page','label':'页数'}],
            'row_ids':[r['id'] for r in t['rows']]} for t in catalog]}
    monkeypatch.setattr(provider,'chat_json',layout_model)
    preview=client.get(f'/api/sections/{sid}/regeneration').json()
    job=execute_job(client,client.post(f'/api/sections/{sid}/regenerate',json={'revision':preview['revision'],'outline_revision':preview['outline_revision'],
        'request_id':uuid.uuid4().hex,'instruction':'序号改成条目编号，增加响应章节列。','confirmed':True}))
    assert len(calls)==1 and '序号改成条目编号' in calls[0]
    fresh=client.get(f'/api/projects/{pid}').json()
    changed=next(s for s in fresh['sections'] if s['id']==sid)
    assert changed['content'].startswith('| 条目编号 | 评审项目 | 评分标准 | 分值 | 响应章节 | 页数 |')
    for row in fresh['sections']:
        if row['id']!=sid:assert row==baseline[row['id']]
    assert db.all('SELECT * FROM requirements WHERE project_id=? ORDER BY id',(pid,))==before_requirements
    assert client.get(old_export['url']).content==old_bytes
    exported=client.post(f'/api/projects/{pid}/export',json={'format':'docx','final':False})
    assert exported.status_code==200,exported.text
    doc=Document(BytesIO(client.get(exported.json()['url']).content))
    assert [c.text for c in doc.tables[0].rows[0].cells]==['条目编号','评审项目','评分标准','分值','响应章节','页数']
    assert len(calls)==1  # Export and refresh never call the model again.
    history=client.get(f'/api/sections/{sid}/generation-history').json()['snapshots']
    assert len(history)>=2
    # Duplicate worker dispatch is idempotent, including the custom layout call.
    workflow._run(job['id'])
    assert len(calls)==1
