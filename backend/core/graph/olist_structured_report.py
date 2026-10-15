"""Constrained report architecture: LLM proposes layout IDs; code renders facts.
This does not measure a free-text LLM's factual accuracy.
"""
from copy import deepcopy
from datetime import timedelta
import html
import json
import math
import re
from core.tools.olist_weekly_forecast import MODELS,monday

TITLES=('物流分析报告','Olist 周度预测报告')
SECTIONS=('cleaning','sql','forecast','limitations','provenance')
DEFAULT_PLAN={'title':TITLES[0],'section_order':list(SECTIONS)}
PLANNER_SYSTEM=('你是报告版式规划器。只输出一个JSON对象，字段只能是title和section_order。'
    'title只能是“物流分析报告”或“Olist 周度预测报告”。section_order必须完整包含'
    'cleaning、sql、forecast、limitations、provenance五个字符串各一次，顺序可调整。'
    '不得输出事实、数字、日期、说明文字、Markdown代码围栏或额外字段。事实由程序从证据渲染。')


def parse_report_plan(raw):
    if not isinstance(raw,str) or len(raw)>2000:
        raise ValueError('Planner must return a small JSON string')
    def no_duplicate_keys(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate JSON key')
            result[key]=value
        return result
    plan=json.loads(raw,object_pairs_hook=no_duplicate_keys)
    if not isinstance(plan,dict) or set(plan)!={'title','section_order'}:
        raise ValueError('Only title and section_order are accepted')
    if plan['title'] not in TITLES or not isinstance(plan['section_order'],list):
        raise ValueError('Unknown title or invalid section_order')
    order=plan['section_order']
    if len(order)!=len(SECTIONS) or any(not isinstance(s,str) for s in order) or set(order)!=set(SECTIONS):
        raise ValueError('Each known section must occur exactly once')
    return plan


def planner_instruction(evidence):
    validate_evidence(evidence)
    # No factual output field exists; all required sections must remain present.
    return PLANNER_SYSTEM+'\n请为本次 Olist 报告选择标题和五个章节的顺序。'


def real_report_plan(instruction):
    from core.llm import get_llm
    response=get_llm().model.invoke([{'role':'system','content':PLANNER_SYSTEM},
                                    {'role':'user','content':instruction}])
    content=getattr(response,'content',None)
    if not isinstance(content,str):
        raise ValueError('Planner response is not a text JSON object')
    return content


def _integer(value):
    return type(value) is int and value>=0


def validate_evidence(evidence):
    if not isinstance(evidence,dict) or set(evidence)!={'cleaning','sql','forecast','provenance','scope'}:
        raise ValueError('Unsupported evidence schema')
    json.dumps(evidence,ensure_ascii=False,allow_nan=False)
    c,s,f,p=(evidence[k] for k in ['cleaning','sql','forecast','provenance'])
    for field in ['source_unchanged','gate_passed','real_cleaning_agent_used']:
        if type(c.get(field)) is not bool:raise ValueError('Cleaning boolean missing or invalid')
    if type(s.get('independent_reference_match')) is not bool or type(f.get('future_rows_used')) is not bool:
        raise ValueError('SQL/forecast boolean missing or invalid')
    for field in ['item_rows_before','item_rows_after','deleted_rows']:
        if not _integer(c.get(field)):raise ValueError('Invalid cleaning count')
    if c['item_rows_before']-c['deleted_rows']!=c['item_rows_after']:
        raise ValueError('Cleaning counts conflict')
    if c.get('execution_type')!='deterministic_olist_validation_and_zero_deletion' or c['deleted_rows']!=0:
        raise ValueError('Only the existing zero-deletion evidence schema is supported')
    if not isinstance(c.get('warnings'),list):raise ValueError('Missing warnings list')
    for warning in c['warnings']:
        if not isinstance(warning,dict) or not isinstance(warning.get('type'),str) or not isinstance(warning.get('action'),str):
            raise ValueError('Invalid warning evidence')
    origin=monday(f.get('origin_week'))
    first=monday(f.get('history_start_week'));last=monday(f.get('history_last_week'))
    if last!=origin-timedelta(weeks=1) or first>last:
        raise ValueError('Invalid history boundary')
    if not _integer(f.get('history_rows')) or f['history_rows']!=(last-first).days//7+1:
        raise ValueError('History count/boundary mismatch')
    if s.get('history_last_week')!=f['history_last_week'] or s.get('history_count')!=f['history_rows']:
        raise ValueError('SQL and Forecast histories differ')
    if f.get('model') not in MODELS or f.get('horizon_weeks')!=2 or f.get('tool')!='olist_weekly_forecast_v1':
        raise ValueError('Unsupported forecast tool/model/horizon')
    if not isinstance(f.get('category_key'),str) or not re.fullmatch(r'[a-z0-9_]+',f['category_key']):
        raise ValueError('Unsupported category identifier')
    if not isinstance(s.get('sql'),str) or not s['sql'].strip().startswith('SELECT '):
        raise ValueError('Missing source SQL')
    if s.get('parameters')!=[f['category_key'],f['history_start_week'],f['origin_week']]:
        raise ValueError('SQL parameters inconsistent with forecast')
    rows=f.get('forecasts')
    if not isinstance(rows,list) or len(rows)!=2:raise ValueError('Expected two forecasts')
    for i,row in enumerate(rows,1):
        if type(row.get('horizon')) is not int or row.get('horizon')!=i or monday(row.get('week_start'))!=origin+timedelta(weeks=i-1):
            raise ValueError('Forecast horizon/week mismatch')
        value=row.get('predicted_item_count')
        if type(value) not in (int,float) or not math.isfinite(value) or value<0:
            raise ValueError('Invalid forecast value')
    for value in [s.get('facts_sha256'),s.get('db_sha256'),p.get('facts_sha256'),p.get('selection_sha256')]:
        if not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{64}',value):
            raise ValueError('Invalid provenance checksum')
    if s['facts_sha256']!=p['facts_sha256']:raise ValueError('Facts provenance conflict')
    if not isinstance(evidence['scope'],str):raise ValueError('Missing execution scope')
    return True


def cell(value):
    if type(value) is bool:return str(value).lower()
    return html.escape(str(value),quote=False).replace('|','&#124;').replace('\n',' ')


def table(headers,rows):
    return '\n'.join(['| '+' | '.join(map(cell,headers))+' |','| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(map(cell,row))+' |' for row in rows])


def render_olist_report(evidence,plan):
    validate_evidence(evidence)
    # Validate dict plans through the same JSON whitelist as model output.
    plan=parse_report_plan(json.dumps(plan,ensure_ascii=False,allow_nan=False))
    c,s,f,p=(evidence[k] for k in ['cleaning','sql','forecast','provenance'])
    cleaning=table(['字段','值'],[[k,c[k]] for k in ['execution_type','source_unchanged','item_rows_before','item_rows_after','deleted_rows','gate_passed','real_cleaning_agent_used']])
    cleaning+='\n\n源文件'+('保持不变。' if c['source_unchanged'] else '已发生改变。')+'质量门禁'+('通过。' if c['gate_passed'] else '未通过。')
    cleaning+='\n\n以下各行仅表示该条警告自身的处理记录：\n\n'
    if c['warnings']:
        cleaning+=table(['type','来源字段/统计','action'],[[w['type'],json.dumps({k:v for k,v in w.items() if k not in ('type','action')},ensure_ascii=False,sort_keys=True),w['action']] for w in c['warnings']])
    else:
        cleaning+='输入 warnings 列表为空。'
    sql=table(['字段','值'],[[k,s[k]] for k in ['history_count','history_last_week','independent_reference_match']])
    sql+='\n\nSQL 独立参考匹配：'+('通过。' if s['independent_reference_match'] else '未通过。')
    sql+='\n\n参数：'+cell(json.dumps(s['parameters'],ensure_ascii=False))+'\n\nSQL 原文：\n\n'+table(['sql'],[[s['sql']]])
    a,b=f['forecasts']
    forecast=table(['category_key','origin_week','model','t+1_week','t+1','t+2_week','t+2'],
        [[f['category_key'],f['origin_week'],f['model'],a['week_start'],f"{a['predicted_item_count']:.4f}",b['week_start'],f"{b['predicted_item_count']:.4f}"]])
    forecast+='\n\n'+table(['字段','值'],[[k,f[k]] for k in ['tool','horizon_weeks','history_start_week','history_last_week','history_rows','future_rows_used','demand_definition']])
    forecast+='\n\n'+('使用了未来记录。' if f['future_rows_used'] else '未使用未来记录。')+'t+1 对应 origin_week 所在周。'
    limitations=('预测对象为按购买周和类别统计的观测订单明细数。以上数值是点预测，可能为小数，'
        '不等于实际已发生数量，不是概率区间、模型准确率或库存补货决策。'
        'SQL 的 independent_reference_match 仅表明该历史查询的参考核对结果；本报告输入未包含预测模型的独立准确性评估。'
        '\n\n执行范围（上游原文）：'+cell(evidence['scope']))
    provenance=table(['字段','值'],[['facts_sha256',p['facts_sha256']],['db_sha256',s['db_sha256']],['selection_sha256',p['selection_sha256']]])
    sections=dict(cleaning=('数据检查与清洗',cleaning),sql=('历史 SQL 查询',sql),
        forecast=('周度点预测',forecast),limitations=('证据范围',limitations),provenance=('来源记录',provenance))
    parts=['# '+plan['title'],'> 事实字段由代码依据结构化证据渲染；大模型仅提供经校验的标题与章节顺序。']
    for name in plan['section_order']:
        title,body=sections[name];parts.append('## '+title+'\n\n'+body)
    return '\n\n'.join(parts)+'\n'


def generate_structured_report(evidence,planner=real_report_plan):
    validate_evidence(evidence)
    instruction=planner_instruction(evidence)
    raw=planner(instruction)
    plan=parse_report_plan(raw)
    return dict(report_result=render_olist_report(evidence,plan),report_plan=deepcopy(plan),
                planner_instruction=instruction,planner_raw_output=raw,
                generation_mode='validated_layout_plan_and_deterministic_facts')
