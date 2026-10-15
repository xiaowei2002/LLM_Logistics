"""New Olist four-stage LangGraph branch, independent of legacy workflows.
Cleaning/SQL/forecast are deterministic; the injected report generator may be LLM.
Native missing-origin interruption resumes with Command(resume={origin_week:...}).
"""
from __future__ import annotations
from datetime import timedelta
import json
import operator
from pathlib import Path
from typing import Annotated, TypedDict
import pandas as pd
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from core.graph.olist_preparation import prepare_olist_snapshot
from core.graph.olist_report_contract import strengthen_olist_instruction, review_olist_report
from core.tools.olist_weekly_sql import build_weekly_dataset, open_readonly, sha, START as WEEK_START, VAL_END, END as WEEK_END
from core.tools.olist_weekly_forecast import MODELS, forecast_olist_weekly, monday

HISTORY_SQL = '''SELECT category_key, week_start, SUM(item_count) AS observed_item_count
FROM order_item_facts
WHERE category_key = ? AND week_start >= ? AND week_start < ?
GROUP BY category_key, week_start
ORDER BY week_start ASC'''
INPUTS = {'weekly_summary.json','selected_categories.csv','weekly_train.csv','weekly_validation.csv'}
CODE_NAMES = {'core/tools/olist_weekly_sql.py','core/tools/olist_weekly_forecast.py','tests/system_eval/eval_olist_forecast_v1.py'}


class OlistState(TypedDict, total=False):
    source_dir: str
    weekly_dir: str
    selection_dir: str
    run_dir: str
    category_key: str
    origin_week: str
    model: str
    status: str
    error: str
    selection_sha256: str
    expected_facts_sha256: str
    expected_weekly_sha256: dict
    preparation: dict
    weekly: dict
    sql_result: dict
    forecast_result: dict
    report_evidence: dict
    report_instruction: str
    report_result: str
    report_review: dict
    report_generation_mode: str
    report_model_output: str
    report_plan: dict
    sql_tool_call: dict
    forecast_tool_call: dict
    tool_failure: dict
    execution_trace: Annotated[list[str], operator.add]


def frozen_request(state):
    weekly_dir, selection_dir = Path(state['weekly_dir']), Path(state['selection_dir'])
    frozen = json.loads((selection_dir/'selection.json').read_text(encoding='utf-8-sig'))
    if frozen.get('evaluation_id') != 'olist_forecast_v1_selection' or frozen.get('selection_uses_test_labels') is not False:
        raise ValueError('Invalid frozen selection evidence')
    if set(frozen['input_sha256']) != INPUTS or set(frozen['code_sha256']) != CODE_NAMES:
        raise ValueError('Unexpected frozen evidence paths')
    backend = Path(__file__).resolve().parents[2]
    if any(sha(backend/n) != h for n,h in frozen['code_sha256'].items()):
        raise ValueError('Frozen forecast code changed')
    if any(sha(weekly_dir/n) != h for n,h in frozen['input_sha256'].items()):
        raise ValueError('Frozen selection inputs changed')
    if set(frozen['selection_artifact_sha256']) != {'validation_metrics.json','validation_predictions.csv'}:
        raise ValueError('Unexpected validation artifacts')
    if any(sha(selection_dir/n) != h for n,h in frozen['selection_artifact_sha256'].items()):
        raise ValueError('Validation selection artifacts changed')
    scores = json.loads((selection_dir/'validation_metrics.json').read_text(encoding='utf-8-sig'))['by_category']
    for category,model in frozen['chosen_models'].items():
        values = {r['model']:r['MAE'] for r in scores if r['category_key']==category}
        if set(values) != set(MODELS) or model != min(MODELS,key=lambda m:values[m]):
            raise ValueError('Model choice differs from validation evidence')
    category = state['category_key']
    if category not in frozen['chosen_models']:
        raise ValueError('Category not selected from training data')
    origin = monday(state['origin_week'])
    if not VAL_END <= origin.isoformat() or origin+timedelta(weeks=2) > monday(WEEK_END):
        raise ValueError('Origin outside the existing two-week test protocol')
    meta = json.loads((weekly_dir/'weekly_summary.json').read_text(encoding='utf-8-sig'))
    return dict(model=frozen['chosen_models'][category],selection_sha256=sha(selection_dir/'selection.json'),
                expected_facts_sha256=meta['facts_sha256'],
                expected_weekly_sha256={n:meta['output_sha256'][n] for n in ['selected_categories.csv','weekly_train.csv','weekly_validation.csv','weekly_test.csv']})


def step_failure(name, exc):
    result = dict(status='failed',error=f'{name}: {type(exc).__name__}: {exc}',execution_trace=[name])
    if hasattr(exc,'audit'):
        result['tool_failure'] = exc.audit
    return result


def make_nodes(report_generator,report_mode="free_text",tool_caller=None):
    if report_mode not in ("free_text","structured_plan"):
        raise ValueError("Unknown report mode")
    def request(state):
        # Do not catch GraphInterrupt. No disk mutation happens before resume.
        origin = state.get('origin_week')
        if not origin:
            answer = interrupt(dict(reason='missing_origin_week',question='请提供预测起点周一日期（YYYY-MM-DD）。'))
            origin = answer.get('origin_week') if isinstance(answer,dict) else None
        try:
            value = {**state,'origin_week':origin}
            checked = frozen_request(value)
            run = Path(state['run_dir']).resolve()
            if run.exists() or any(run==Path(state[n]).resolve() or Path(state[n]).resolve() in run.parents for n in ['source_dir','weekly_dir','selection_dir']):
                raise ValueError('Use a new run directory outside input directories')
            run.mkdir(parents=True)
            return dict(**checked,origin_week=origin,status='running',error='',execution_trace=['request'])
        except Exception as exc:
            return step_failure('request',exc)

    def cleaning(state):
        try:
            result = prepare_olist_snapshot(state['source_dir'],Path(state['run_dir'])/'preparation')
            if result['facts_sha256'] != state['expected_facts_sha256']:
                raise ValueError('Prepared facts differ from the frozen forecast data version')
            return dict(preparation=result,execution_trace=['cleaning'])
        except Exception as exc:
            return step_failure('cleaning',exc)

    def sql(state):
        try:
            root = Path(state['run_dir'])
            preparation = state['preparation']
            if sha(root/'preparation'/'order_item_facts.csv') != preparation['facts_sha256']:
                raise ValueError('Prepared facts changed before SQL')
            result = build_weekly_dataset(root/'preparation',root/'sql_snapshot')
            if any(result['output_sha256'][n] != h for n,h in state['expected_weekly_sha256'].items()):
                raise ValueError('New SQL snapshot does not reproduce frozen weekly datasets')
            db = root/'sql_snapshot'/'olist_snapshot.sqlite3'
            def execute_history(arguments):
                con = open_readonly(db)
                try:
                    cursor = con.execute(HISTORY_SQL,(arguments['category_key'],arguments['start_week'],arguments['origin_week']))
                    columns = [c[0] for c in cursor.description]
                    return [dict(zip(columns,row)) for row in cursor.fetchall()]
                finally:
                    con.close()
            arguments = dict(category_key=state['category_key'],start_week=WEEK_START,origin_week=state['origin_week'])
            if tool_caller is None:
                observed = execute_history(arguments)
                call_audit = {}
            else:
                observed,call_audit = tool_caller.execute('sql_history',arguments,execute_history)
            lookup = {r['week_start']:int(r['observed_item_count']) for r in observed}
            weeks = list(pd.date_range(WEEK_START,state['origin_week'],freq='7D',inclusive='left').strftime('%Y-%m-%d'))
            history = [dict(category_key=state['category_key'],week_start=w,observed_item_count=lookup.get(w,0)) for w in weeks]
            # Independent reference reads the verified facts and filters by origin.
            facts = pd.read_csv(root/'preparation'/'order_item_facts.csv',dtype=str,keep_default_na=False)
            part = facts[facts.category_key.eq(state['category_key']) & facts.week_start.ge(WEEK_START) & facts.week_start.lt(state['origin_week'])]
            reference = part.groupby('week_start').size().to_dict()
            if lookup != reference or any(r['week_start']>=state['origin_week'] for r in history):
                raise ValueError('SQL history reference or temporal cutoff mismatch')
            evidence = dict(sql=HISTORY_SQL,parameters=[state['category_key'],WEEK_START,state['origin_week']],
                history_rows=history,history_count=len(history),history_last_week=history[-1]['week_start'],
                independent_reference_match=True,facts_sha256=result['facts_sha256'],db_sha256=sha(db))
            return dict(weekly=result,sql_result=evidence,sql_tool_call=call_audit,execution_trace=['sql'])
        except Exception as exc:
            return step_failure('sql',exc)

    def forecast(state):
        try:
            root = Path(state['run_dir'])
            if sha(root/'preparation'/'order_item_facts.csv') != state['sql_result']['facts_sha256'] or sha(root/'sql_snapshot'/'olist_snapshot.sqlite3') != state['sql_result']['db_sha256']:
                raise ValueError('SQL evidence changed before forecast')
            def execute_forecast(arguments):
                return forecast_olist_weekly(state['sql_result']['history_rows'],arguments['category_key'],arguments['origin_week'],arguments['model'],arguments['horizon'])
            arguments = dict(category_key=state['category_key'],origin_week=state['origin_week'],model=state['model'],horizon=2)
            if tool_caller is None:
                result = execute_forecast(arguments)
                call_audit = {}
            else:
                result,call_audit = tool_caller.execute('forecast',arguments,execute_forecast)
            return dict(forecast_result=result,forecast_tool_call=call_audit,execution_trace=['forecast'])
        except Exception as exc:
            return step_failure('forecast',exc)

    def report(state):
        try:
            p,sql,f = state['preparation'],state['sql_result'],state['forecast_result']
            root = Path(state['run_dir'])
            if sha(root/'preparation'/'order_item_facts.csv') != p['facts_sha256'] or sha(root/'sql_snapshot'/'olist_snapshot.sqlite3') != sql['db_sha256']:
                raise ValueError('Evidence changed before report')
            checked = forecast_olist_weekly(sql['history_rows'],state['category_key'],state['origin_week'],state['model'],2)
            if checked != f:
                raise ValueError('Forecast payload differs from deterministic tool result')
            if (sql['facts_sha256']!=p['facts_sha256'] or f['origin_week']!=state['origin_week'] or f['category_key']!=state['category_key'] or f['model']!=state['model'] or f['history_last_week']!=sql['history_last_week']):
                raise ValueError('Report input evidence inconsistent')
            evidence = dict(cleaning=dict(execution_type=p['execution_type'],source_unchanged=p['source_unchanged'],
                item_rows_before=p['cleaning_gate']['tables']['order_items']['rows'],item_rows_after=p['facts_rows'],
                deleted_rows=p['deleted_rows'],gate_passed=p['cleaning_gate']['passed'],warnings=p['cleaning_gate']['warnings'],
                real_cleaning_agent_used=False),
                sql={k:v for k,v in sql.items() if k!='history_rows'},forecast=f,
                provenance=dict(selection_sha256=state['selection_sha256'],facts_sha256=p['facts_sha256']),
                scope='确定性 Olist 数据检查、固定 SQL、预测基线；未调用旧 CleaningAgent、SQLAgent、ForecastAgent，报告独立生成。')
            if tool_caller is not None:
                evidence['scope'] += ' SQL 与 Forecast 的受限工具参数由工具调用适配器提交并核验，数值由实际工具执行产生。'
            instruction = ('请只依据下面结构化证据生成中文报告。保留警告和来源边界，不新增业务事实、不推断库存/补货结论、不编造人工审批。'
                '必须含一张仅一行数据的 Markdown 表，列顺序严格为 category_key、origin_week、model、t+1_week、t+1、t+2_week、t+2；'
                '两个预测值按工具原值格式化至四位小数，其他列使用原始值。t+1 就是 origin_week 所在周。'
                '说明这是观测明细数的点预测，不是实际已发生数量、概率区间或模型准确率；'
                '说明清洗明细前后行数、删除数与保留警告，SQL 历史截止周；没有输入的事实不要补全。\nEVIDENCE_JSON\n'+json.dumps(evidence,ensure_ascii=False))
            structured = {}
            if report_mode == 'structured_plan':
                from core.graph.olist_structured_report import planner_instruction,parse_report_plan,render_olist_report
                instruction = planner_instruction(evidence)
                raw = report_generator(instruction)
                try:
                    plan = parse_report_plan(raw)
                except (ValueError,TypeError) as exc:
                    return dict(report_evidence=evidence,report_instruction=instruction,
                        report_model_output=raw if isinstance(raw,str) else repr(raw),report_result='',
                        report_generation_mode=report_mode,status='needs_review',error=f'Invalid layout plan: {exc}',
                        execution_trace=['report'])
                result = render_olist_report(evidence,plan)
                structured = dict(report_generation_mode=report_mode,report_model_output=raw,report_plan=plan)
            else:
                instruction = strengthen_olist_instruction(instruction)
                result = report_generator(instruction)
                structured = dict(report_generation_mode=report_mode)
            if not isinstance(result,str) or not result.strip():
                raise ValueError('Report generator returned empty text')
            review = review_olist_report(result,evidence)
            return dict(report_evidence=evidence,report_instruction=instruction,report_result=result,
                        report_review=review, status='completed' if review['heuristic_checks_passed'] else 'needs_review',
                        execution_trace=['report'],**structured)
        except Exception as exc:
            return step_failure('report',exc)
    return dict(request=request,cleaning=cleaning,sql=sql,forecast=forecast,report=report)


def build_olist_workflow(report_generator,checkpointer=None,*,report_mode="free_text",tool_caller=None):
    graph = StateGraph(OlistState)
    for name,node in make_nodes(report_generator,report_mode,tool_caller).items():
        graph.add_node(name,node)
    graph.add_edge(START,'request')
    for name,next_name in [('request','cleaning'),('cleaning','sql'),('sql','forecast'),('forecast','report')]:
        graph.add_conditional_edges(name,lambda s:'stop' if s.get('status')=='failed' else 'next',{'stop':END,'next':next_name})
    graph.add_edge('report',END)
    return graph.compile(checkpointer=checkpointer)


def expected_report_row(forecast):
    a,b = forecast['forecasts']
    return [forecast['category_key'],forecast['origin_week'],forecast['model'],
            a['week_start'],f"{a['predicted_item_count']:.4f}",b['week_start'],f"{b['predicted_item_count']:.4f}"]


def report_table_matches(text,forecast):
    expected = expected_report_row(forecast)
    rows = [[c.strip().strip('`').strip() for c in line.strip().strip('|').split('|')]
            for line in text.splitlines() if line.strip().startswith('|')]
    return sum(row==expected for row in rows)==1
