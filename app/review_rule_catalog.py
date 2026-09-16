"""Backend audit inventory. One row per emitted check or business validator."""


def entry(id, name, group, source, trigger, exception, positive, negative, *, editable=True, codes=()):
    return {'id':id, 'name':name, 'group':group, 'source':source, 'version':'1.0',
            'editable':editable, 'check_codes':list(codes),
            'description':trigger + (' 关闭只停用本地审查项，不改变原始数据或基础保存/生成校验。' if editable else ' 此项为基础校验或汇总结果，在此展示说明，不能单独关闭。'),
            'triggers':[trigger], 'exceptions':[exception],
            'correct_example':{'text':positive,'result':'应触发此项。'},
            'false_positive_example':{'text':negative,'result':'不应触发此项；其他规则仍独立判断。'}}


CHECKS = [
 ('no_tender','招标文件缺失','招标与解析','项目没有导入任何招标文件。','已有项目招标文档时不按缺失处理。','项目文档数量为0。','已导入一份有效招标文档。'),
 ('project_basics_missing','项目基本信息缺失','招标与解析','项目名称、编号、采购人或投标企业名称为空。','字段已按原文或人工确认填写。','采购人字段为空。','四项基本信息均已填写。'),
 ('project_field_conflicts','采购信息存在冲突','招标与解析','项目编号、采购人、截止时间存在多个原文值且未人工确认。','已明确采用值并保留来源。','两个截止日期并存，未确认。','已确认采用澄清后的截止日期。'),
 ('analysis_incomplete','AI招标分析未完整','招标与解析','未完成全批分析或分析指纹不匹配当前招标文件。','完整分析且文件指纹一致。','只有本地候选扫描结果。','所有批次分析完成且文件未变。'),
 ('parse_errors','招标文件解析失败','招标与解析','至少一份项目文档解析未就绪。','全部项目文件均解析ready。','文件解析状态error。','全部项目文档解析ready。'),
 ('parse_warnings','解析警告未核对','招标与解析','招标文档存在OCR、图像、表格等解析提示，未核对时阻断。','无提示，或已核对提示时仅提醒。','扫描页面未识别且未确认。','提示已核对并有记录。'),
 ('no_requirements','要求台账为空','响应与覆盖','项目没有要求条目。','至少一条有效要求存在。','要求数量为0。','已提取技术要求条目。'),
 ('responses_unconfirmed','要求响应尚未人工确认','响应与覆盖','要求状态不是confirmed或not_applicable。','响应已确认或已给出不适用记录。','响应仍为drafted。','全部响应confirmed。'),
 ('invalid_evidence','响应证据失效或不适用理由不足','资料与引用','响应引用无效/过期资料，或不适用理由不足10字。','证据有效且不适用理由充分。','已撤销资料仍被引用。','有效证据已核对；不适用有充分理由。'),
 ('confirmed_without_evidence','已确认响应缺少直接证据','资料与引用','confirmed响应缺乏有效企业证据，且不是已确认结构化报价支持的报价条款。','响应有直接资料，或报价有已确认结构化报价。','无任何证据却已确认功能满足。','报价响应由已确认报价明细支持。'),
 ('response_citation_mismatch','响应引用格式或登记不一致','资料与引用','响应中的引用未登记、格式异常或来源无效。','正文引用与已登记有效证据一致。','正文写E:abc但证据列表为空。','正文与登记引用一致且来源有效。'),
 ('no_sections','没有投标章节','响应与覆盖','项目没有任何章节。','章节已存在。','章节数量为0。','已有章节记录。'),
 ('sections_unapproved','章节尚未人工批准','正文与批准','有章节状态不是approved。','全部章节已人工批准。','仍有draft章节。','全部章节approved。'),
 ('uncovered_requirements','要求未被章节覆盖','响应与覆盖','部分要求ID未关联任何章节。','每条要求至少关联一章。','要求R1未在任何章节登记。','要求R1已关联技术方案。'),
 ('broken_citation','章节引用无效或未登记','资料与引用','章节正文引用未登记、格式异常或来源失效。','本章所有引用有效且登记一致。','正文引用已过期来源。','引用与有效资料一致。'),
 ('unverified_numbers','数字缺乏直接企业依据','资料与引用','正文或响应的能力/业务数字未在对应企业资料中找到。','数字及单位有直接资料，报价数字可由已确认报价支持。','正文100并发但所引资料20并发。','正文20并发与企业资料相同。'),
 ('model_review_missing','当前版本缺少独立AI复核','AI复核','没有与当前内容指纹一致且完整的AI复核。','最新完整AI复核指纹匹配。','修改正文后仍使用旧审核。','编辑后已完成对应版本复核。'),
 ('human_final_review','最终人工检查提醒','交付与导出','每次本地检查提醒最终人工核对。','提醒不是事实通过证明，也不是错误计数。','准备递交前需要人工核对格式与签章。','不能把此提醒当作缺失一份企业证书。'),
]
CATALOG = [entry(id,name,group,'app/workflow.py:review_project',trigger,exception,positive,negative,codes=(id,)) for id,name,group,trigger,exception,positive,negative in CHECKS]

SUBRULES = [
 ('section_citation_format','章节引用语法与登记','资料与引用','app/content_review.py:inspect_section','章节正文引用格式错误或未登记到证据列表。','所有可见引用格式和登记一致。','写了引用ID但没有登记。','有效引用同时登记在章节证据列表。'),
 ('section_evidence_validity','章节资料认可与适用性','资料与引用','app/content_review.py:inspect_section','本章证据未认可、过期、不适用或内容变更。','当前有效且适用于本项目的已认可证据。','资料已撤销但章节继续引用。','资料认可版本与当前来源一致。'),
 ('qualification_declaration','资格声明内容尚需认可','正文与批准','app/content_review.py:inspect_section','资格声明缺乏直接支持或尚需企业认可。','已按当前内容版本记录核对结果。','声明无关联关系但企业未核实。','已核查并认可该资格声明。'),
 ('other_content_gap','其他具体正文缺项','正文与批准','app/content_review.py:assessments','存在未解决的人员、日期、适用条件等非报价事实缺项。','相关内容已经补齐并核对。','法定代表人姓名未填写。','实际姓名已填写并核对。'),
 ('other_review_issue','未分类的内部审核问题','正文与批准','app/content_review.py:assessments','存在未被专门规则归类的、未解决的内部审核问题。','没有未分类问题或已有对应版本解决记录。','旧审核留下未知类型的内容问题。','对应问题已核对解决。'),
 ('ai_other_finding','AI其他审核发现','AI复核','app/workflow.py:review_project','当前AI审核存在不能明确归入其他规则的问题。','AI未发现其他问题。','模型给出未知类gap意见。','当前模型仅报告已支持事实。'),
]
CATALOG += [entry(*row) for row in SUBRULES]

# Aggregation nodes explain how their child rules contribute, rather than
# offering a misleading switch that leaves the same child errors active.
for code,name,why in [
    ('response_gaps','响应缺项汇总','由正文为空、报价缺失和其他正文缺项汇总。'),
    ('content_todo','内部待办汇总','由每个待办的实际类型和对应开关汇总。'),
    ('section_gap','章节正文问题汇总','由本章仍启用的检查和待办汇总。'),
    ('ai_evidence_review','当前AI复核发现汇总','按每条AI意见的实际类型关联具体规则。'),
    ('model_review_complete','AI复核完成信息','存在与当前指纹匹配的完整AI审核时显示。')]:
    CATALOG.append(entry(code,name,'审核汇总','app/workflow.py:review_project',why,'汇总本身不新增事实，也不将未完成问题标为完成。','子规则触发后汇总展示。','关闭子规则后不重复计入当前阻断。',editable=False,codes=(code,)))

FIXED = [
 ('knowledge_identity','资料身份与归属','企业资料认可','app/knowledge_review.py:plan','资料ID无效、已删除或属于采购文件而非企业资料。','有效企业资料。','将甲方招标文件作为企业能力批准。','实际企业产品手册。'),
 ('knowledge_parse','资料解析状态','企业资料认可','app/knowledge_review.py:plan','资料尚未解析ready。','已解析正文可读。','解析失败的PDF。','短Markdown已解析，页数0并非失败。'),
 ('knowledge_expiry','资料有效期','企业资料认可','app/knowledge_review.py:plan','资料明确过期或状态expired。','未设置有效期不等于过期。','证书已过有效期。','未设置日期的有效产品介绍。'),
 ('knowledge_scope','产品及用途范围','企业资料认可','app/knowledge_review.py:plan','资料用途为历史投标或内部参考，不能当通用产品事实。','产品、版本、项目范围匹配。','其他客户专属投标承诺。','适用产品的原始功能说明。'),
 ('knowledge_origin','原始来源与事实类型','企业资料认可','app/knowledge_review.py:original_source','生成稿、失败案例、客户专属或冲突内容不能自动当原始事实。','真实可复用产品资料。','AI生成稿未经区分重新当证据。','官方产品资料中的可复用能力说明。'),
 ('knowledge_version','资料版本与局部认可范围','企业资料认可','app/knowledge_review.py:plan','来源变化或局部认可被扩大为未预览的新正文。','认可版本与哈希一致。','只认可两段却自动认可全文。','保留原两段范围。'),
 ('knowledge_body','资料正文与附件可读取性','企业资料认可','app/knowledge_review.py:readable_text','只有附件链接、索引、文件名或无可用正文。','短正文可读即可，不按任意最小字数拒绝。','仅一个PDF下载链接。','已获取的附件实际正文。'),
 ('extraction_quote','招标原文引用定位','生成与解析校验','app/workflow.py:_extraction_problems','模型条目不是对象，quote不为连续原文或无法在批次中定位。','逐字连续原文且位置可核对。','模型改写原句作为quote。','原文连续摘录及准确位置。'),
 ('generation_schema','生成JSON结构','生成与解析校验','app/workflow.py:_validate_generation_result','正文、响应列表、引用ID数据类型不符合约定。','输出结构与字段类型合法。','responses返回字符串。','responses为完整对象列表。'),
 ('generation_coverage','生成要求覆盖','生成与解析校验','app/workflow.py:_validate_generation_result','要求ID遗漏、重复或增加。','本批每条要求恰好一次。','十二条要求只返回十一条。','十二条要求逐条覆盖。'),
 ('generation_citations','生成引用白名单','生成与解析校验','app/workflow.py:_validate_generation_result','模型使用未提供或伪造引用ID。','引用均在本次提供的证据中。','自行编造E99。','使用本次已提供E01。'),
 ('repair_support','修复引用逐字支持','生成与解析校验','app/workflow.py:_validate_repaired_citation_support','修复引用没有有效的逐字支持或借用无关资料。','引用与原始请求证据对应。','用无关证书给性能声明补ID。','引用直接支持原陈述的连续原文。'),
 ('generation_body','生成无实质正文保护','生成与解析校验','app/section_generation.py:run','模型只返回内部提示或空内容，不能覆盖原章。','生成有实质正文。','只返回TODO清单。','返回完整方案正文。'),
 ('response_confirmation','响应确认数据校验','响应保存校验','app/main.py:patch_requirement','确认响应前内容为空、引用无效或不适用理由不足。','确认所需内容及有效引用齐备。','空响应直接设confirmed。','已填写有据响应后确认。'),
 ('body_separation','正文与内部提示分离','正文保存校验','app/bid_body.py:separate','正文含编制待办、缺项包装或混写的未核实说明。','正式业务术语、实值数字和技术示例不按内部提示移出。','【待补充：报价】写入正文。','【10】%与“待确认状态查询”是已填值或业务表述。'),
 ('approval_threshold','章节风险阈值','正文与批准','app/approval_risk.py:evaluate','风险达到总设置的拦截等级。','风险低于阈值，或用户选择无视风险。','中风险阈值下批准高风险章。','无视风险模式允许高风险正文批准。'),
 ('operation_revision','预览版本一致性','操作一致性','app/content_review.py:execute','预览后内容、资料或设置变化。明确执行确认仍由对应操作按钮提交。','当前状态与已确认预览一致。','用旧预览批准新正文。','基于当前预览确认。'),
 ('operation_conflict','并发任务与保存排他','操作一致性','app/workflow.py:editing','正在生成/审核或其他保存操作仍占用相同范围。','无冲突任务。','模型生成过程中更改资料认可。','任务结束后保存设置。'),
 ('restore_conflict','撤销与后续编辑冲突','操作一致性','app/content_review.py:undo','快照之后已有正文、批准或待办修改。','目标仍与操作后快照一致。','用旧快照覆盖新编辑。','无后续修改时撤销本轮操作。'),
 ('export_basic_fields','正式导出基础字段','交付与导出','app/documents.py:compose_docx','正式Word缺项目名称、编号、采购人或企业法定名称。','基础字段齐全；草稿可保留空白。','正式文件采购人为空。','正式封面四项基本信息齐全。'),
 ('quote_decimal','报价金额格式与范围','交付与导出','app/documents.py:_confirmed_quotation','报价为负数、非法金额或超过两位小数等。','有效非负十进制金额。','-100元或12.345元。','12000.00元。'),
 ('quote_arithmetic','报价数量单价小计一致','交付与导出','app/documents.py:_confirmed_quotation','明细数量×单价不等于小计，或明细合计不等于总报价。','使用十进制精确核对且金额一致。','2×100却小计300。','2×100小计200，总额一致。'),
 ('quote_uppercase','报价人民币大写一致','交付与导出','app/documents.py:_confirmed_quotation','大写金额与含税总报价不一致。','大小写金额匹配。','数字100，大写贰佰元。','数字100，大写壹佰元整。'),
 ('package_quotation','正式分册报价确认','交付与导出','app/documents.py:compose_bid_package','正式分册缺企业确认报价，或报价正文仍待填写。','已确认有效报价及完整报价分册。','将采购预算当最终投标报价。','企业确认的实际投标报价。'),
 ('file_parse_safety','文件解析规模限制','文件与运行校验','app/documents.py:parse_document','文件大小、页数、解压规模或压缩比超过自定义限制。','文件在自定义规模限制内。关闭本规则仍需格式可读，不能修复损坏或解密文件。','超过页数上限的PDF或超出压缩比上限的DOCX。','合法可读文档。'),
]
CATALOG += [entry(*r,editable=False) for r in FIXED]

MODEL_AND_OUTPUT = [
 ('analysis_output_coverage','分析结果与强制条款漏项','生成与解析校验','app/workflow.py:_extract_validated_batch','模型没有requirements数组，或原文含强制/评分标记却未提取要求。','返回有效要求数组且覆盖已识别的强制内容。','评分批次返回零要求。','强制条款有对应提取条目。'),
 ('model_output_complete','模型输出完整性','生成与解析校验','app/workflow.py:_extract_validated_batch','模型输出截断或中断，不保存不完整批次。','模型正常完整结束且通过结构校验。','输出达到长度限制而截断。','完整有效JSON输出。'),
 ('generation_prerequisites','生成来源与分析前置条件','生成与解析校验','app/workflow.py:run_generate','没有有效分析、要求台账或适用的已批准企业证据。','当前分析和可用依据齐备。','未批准任何企业资料却开始生成。','已有当前分析及适用企业资料。'),
 ('review_result_shape','独立审核结果格式','AI复核','app/workflow.py:_validate_review_result','AI未返回逐目标结果或verdict/理由结构无效。','逐目标结果格式合法。','verdict不在支持的结果集合。','逐目标给出合法结论和理由。'),
 ('review_target_coverage','独立审核目标覆盖','AI复核','app/workflow.py:_validate_review_result','本批目标漏评、重复或使用错误目标ID。','每个当前目标恰好评估一次。','20个目标只评估19个。','20个目标均已评估。'),
 ('review_repair_immutability','审核修复不得改写有效结论','AI复核','app/workflow.py:_review_with_repairs','修复缺漏时改变已经有效评估的结论或理由。','只修补未覆盖/无效目标。','修复一条漏项却改写其余已评估项。','已有效目标原样保留。'),
 ('review_input_revision','审核期间输入版本一致','AI复核','app/workflow.py:run_review','审核期间企业资料或项目内容已改变。','完成时输入指纹与开始一致。','模型审核中修改了正文。','审核前后材料未变。'),
 ('claim_evidence_semantics','逐项事实的证据语义','AI判断约束','app/workflow.py:_review_prompt','引用存在但不能语义支持当前事实，不能当已证实。','直接证据支持对应事实。','用营业执照证明软件性能。','产品性能报告支持具体性能描述。'),
 ('claim_units','数值与单位一致','AI判断约束','app/workflow.py:_review_prompt','数值、单位、人数、并发、SLA或日期与直接依据不一致。','原文数值与单位均一致。','把20分钟响应写为20秒。','保留20分钟。'),
 ('claim_qualifiers','否定、程度与适用范围','AI判断约束','app/workflow.py:_review_prompt','改变否定、程度、版本、条件或客户案例限定。','限定条件完整保留。','把无明显缺陷写成无缺陷。','按原文保留无明显缺陷。'),
 ('procurement_not_capability','采购要求不能证明企业能力','AI判断约束','app/workflow.py:_review_prompt','仅因采购人要求某能力就承诺企业已经具有。','明确区分采购需求、企业能力及实施建议。','采购需要XBRL就写我方已实现XBRL。','依据企业资料另行证明能力。'),
 ('filled_values_not_gaps','已填数值不得当空项','AI判断约束','app/workflow.py:_review_prompt','因括号样式将已有数字错误识别为待填。','实际空字段才列缺项。','将【10】%当空税率。','识别10%为已有值。'),
 ('effective_options','项目生效选项与冲突','AI判断约束','app/workflow.py:_review_prompt','忽略项目级生效选项或擅自消除采购原文冲突。','按当前生效选项核对，冲突保留待澄清。','未启用的保证金条款被当作必须。','按明确不需要保证金的生效选项处理。'),
 ('disclaimer_not_evidence','免责声明不能证明事实','AI判断约束','app/workflow.py:_review_prompt','先写无依据肯定承诺，再以待补或草稿声明作为依据。','事实本身有直接证据，缺项单独处理。','支持XBRL后加待确认就认定有据。','缺少依据的能力不当作已实现。'),
 ('procurement_actor','采购责任主体不转换','生成约束','app/workflow.py:_prepare_generation_request','将采购人或评审机构职责改为投标人承诺。','区分采购组织职责与投标人必要配合。','把评委确定成交方写成我方负责。','仅知悉采购评审程序。'),
 ('export_content_present','导出内容与产物有效性','交付与导出','app/workflow.py:export_project','没有任何可导出章节/响应，或输出文件不存在、为空。','存在实际内容且生成有效文件。','输出路径存在但文件0字节。','成功生成非空投标文件。'),
]
CATALOG += [entry(*r,editable=False) for r in MODEL_AND_OUTPUT]
