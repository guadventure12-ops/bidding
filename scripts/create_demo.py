"""Create an opt-in, synthetic workspace in a NEW directory. No model calls."""
from pathlib import Path
import argparse
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True, help='New directory; an existing directory is never overwritten')
    args = parser.parse_args()
    target = Path(args.data_dir).expanduser().resolve()
    if target.exists():
        parser.error('The target already exists. Choose a new directory; no data was changed.')
    root = Path(__file__).resolve().parents[1]
    target.mkdir(parents=True, exist_ok=False)
    os.environ['MX_DATA_DIR'] = str(target)
    os.environ['LANGFUSE_ENABLED'] = 'false'
    os.environ.pop('DEEPSEEK_API_KEY', None)
    sys.path.insert(0, str(root))
    from app import db, workflow, knowledge_review, product_modules
    db.init()
    db.set_setting('company_name', '演示工作空间')
    company = workflow.ingest(root/'examples/demo-company.md', name='演示产品资料（合成样例）.md')
    knowledge_review.review_single(company['id'], {'status': 'approved', 'scope': 'archive'})
    now = db.now()
    pid = db.uid()
    db.insert('projects', {'id': pid, 'name': '演示 · 电子档案建设项目', 'domain': 'archive',
        'company_name': '演示投标主体', 'project_number': 'DEMO-ARCHIVE-001', 'buyer': '示例采购方',
        'metadata': {'synthetic_demo': True, 'demo_note': '人工编写的合成样例；未运行模型分析或AI审核。'},
        'created_at': now, 'updated_at': now})
    tender = workflow.ingest(root/'examples/demo-tender.md', project_id=pid, name='电子档案建设需求（合成样例）.md')
    chunks = db.all('SELECT * FROM chunks WHERE document_id=? ORDER BY ordinal', (tender['id'],))
    topics = [
        ('需求理解与解决方案', '业务目标与建设范围', '说明档案归集、元数据校验和异常处理的具体流程。',
         '### 建设目标\n\n围绕档案归集、授权检索和过程留痕建立可核对的处理流程。先明确组织范围、档案类型与数据来源，再约定本项目的实施边界。\n\n### 范围核对\n\n|核对项|编制要点|\n|---|---|\n|档案范围|按本次采购确认的类型整理|\n|数据来源|列明业务系统、接口与责任人|\n|例外处理|记录缺失字段并由授权人员确认|'),
        ('需求理解与解决方案', '档案处理流程', '说明档案归集、元数据校验和异常处理的具体流程。',
         '### 归集与校验\n\n数据进入归档流程后执行字段完整性和格式校验。校验异常进入待处理清单，核对完成后重新提交；操作过程保留处理记录。'),
        ('功能模块设计', '授权检索与结果核对', '说明按组织与角色控制查询范围的方式。',
         '### 权限与查询\n\n按照组织和角色确定数据范围，支持按日期、编号与关键字组合查询。用户在授权范围内查看结果，并核对档案元数据。\n\n### 操作留痕\n\n对查询、下载及维护操作记录操作者、时间和处理结果，便于后续核对。'),
        ('功能模块设计', '异常处置与日志', '说明档案检索条件、结果核对及访问日志的使用。',
         '### 异常处置\n\n发现缺失字段、重复记录或访问异常时，形成问题清单。根据问题类型安排核对，并记录处理结果与再次验证情况。'),
        ('实施与交付方案', '实施阶段与交付物', '给出实施阶段、交付物和培训安排。',
         '### 实施安排\n\n|阶段|主要工作|交付物|\n|---|---|---|\n|准备|核对范围与资料|实施准备清单|\n|配置|配置与联调验证|验证记录|\n|验收|逐项核对采购要求|验收核对清单|\n\n具体人员与日期由项目负责人确认。'),
        ('实施与交付方案', '培训与运维交接', '给出实施阶段、交付物和培训安排。',
         '### 培训安排\n\n按管理员与日常使用人员分别准备操作演示、练习清单和常见问题说明。培训后核对操作结果，收集尚需澄清的问题。\n\n### 交接资料\n\n整理配置说明、问题记录和日常检查清单，具体交接对象以实际项目安排为准。'),
    ]
    groups = {}
    for ordinal, (group, title, quote, body) in enumerate(topics):
        gid = groups.setdefault(group, 'demo-group-'+str(len(groups)+1))
        chunk = next((c for c in chunks if quote in c['text']), chunks[0])
        rid = db.uid()
        db.insert('requirements', {'id': rid, 'project_id': pid, 'document_id': tender['id'], 'chunk_id': chunk['id'],
            'number': str(ordinal+1), 'category': 'technical', 'title': title, 'text': quote, 'quote': quote,
            'locator': chunk['locator'], 'origin': 'manual', 'verified': 1, 'status': 'pending',
            'created_at': now, 'updated_at': now})
        db.insert('sections', {'id': db.uid(), 'project_id': pid, 'ordinal': ordinal, 'title': title,
            'outline_group_id': gid, 'outline_group_title': group, 'content': body,
            'requirement_ids': [rid], 'status': 'draft', 'user_edited': 1, 'created_at': now, 'updated_at': now})
    for title, content, scope in [
        ('档案归集与元数据校验', topics[1][3], 'archive'),
        ('授权检索与访问留痕', topics[2][3], 'archive'),
        ('实施与培训服务素材', topics[5][3], 'general'),
        ('费用申请与审批流程', '### 费用流程\n\n演示流程包含申请、节点审批与报销进度查询。费用类别和审批规则由管理员配置；上线前需按真实制度核对。', 'expense'),
    ]:
        product_modules.create({'title': title, 'content': content, 'scope': scope})
    result = {'synthetic_demo': True, 'project_id': pid, 'sections': len(topics), 'model_calls': 0}
    (target/'demo-info.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Synthetic workspace created. No model calls or changes to other workspaces.')
    print('Data directory: '+str(target))
    print('Set MX_DATA_DIR to this directory and run the application. All examples are drafts.')


if __name__ == '__main__':
    main()
