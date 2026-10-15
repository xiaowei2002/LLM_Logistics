import json
import unittest
from copy import deepcopy
from core.graph.olist_structured_report import (DEFAULT_PLAN,parse_report_plan,render_olist_report,generate_structured_report,validate_evidence)
from core.graph.olist_report_contract import review_olist_report
from core.graph.olist_workflow import build_olist_workflow,report_table_matches
from tests import test_olist_workflow as graph_fixture
from langgraph.checkpoint.memory import InMemorySaver


def evidence():
    return dict(cleaning=dict(execution_type='deterministic_olist_validation_and_zero_deletion',source_unchanged=True,
        item_rows_before=112650,item_rows_after=112650,deleted_rows=0,gate_passed=True,real_cleaning_agent_used=False,
        warnings=[dict(type='optional_order_timestamp',column='order_approved_at',missing=160,invalid_nonblank=0,before_purchase=0,
                       action='retain_original; not used to filter forecast records'),
                  dict(type='unknown_category',products=610,item_records=1603,action='retain raw category; add separate derived marker')]),
        sql=dict(sql='SELECT category_key, week_start, SUM(item_count) FROM order_item_facts',parameters=['cama_mesa_banho','2017-01-02','2018-05-21'],
                 history_count=72,history_last_week='2018-05-14',independent_reference_match=True,facts_sha256='a'*64,db_sha256='b'*64),
        forecast=dict(tool='olist_weekly_forecast_v1',category_key='cama_mesa_banho',origin_week='2018-05-21',model='moving_average_8',
            horizon_weeks=2,history_start_week='2017-01-02',history_last_week='2018-05-14',history_rows=72,future_rows_used=False,
            demand_definition='Observed order-item record count by purchase week/category',
            forecasts=[dict(horizon=1,week_start='2018-05-21',predicted_item_count=179.5),dict(horizon=2,week_start='2018-05-28',predicted_item_count=179.5)],note='Point expected counts'),
        provenance=dict(facts_sha256='a'*64,selection_sha256='c'*64),scope='确定性检查、固定SQL、预测基线；未调用旧版Agent。')


class OlistStructuredReportTest(unittest.TestCase):
    def test_whitelisted_plan_accepts_permutation_and_known_title(self):
        plan=dict(title='Olist 周度预测报告',section_order=list(reversed(DEFAULT_PLAN['section_order'])))
        self.assertEqual(parse_report_plan(json.dumps(plan)),plan)

    def test_free_text_extra_fields_and_missing_sections_rejected(self):
        invalid=['源文件保持不变：否',json.dumps({**DEFAULT_PLAN,'summary':'invented facts'}),
                 json.dumps({**DEFAULT_PLAN,'title':'源文件已改变'}),
                 json.dumps({**DEFAULT_PLAN,'section_order':['cleaning']*5}),
                 json.dumps({**DEFAULT_PLAN,'section_order':DEFAULT_PLAN['section_order'][:-1]}),
                 '```json\n'+json.dumps(DEFAULT_PLAN)+'\n```']
        for raw in invalid:
            with self.subTest(raw=raw),self.assertRaises(ValueError):parse_report_plan(raw)

    def test_duplicate_json_keys_rejected(self):
        raw='{"title":"物流分析报告","title":"物流分析报告","section_order":["cleaning","sql","forecast","limitations","provenance"]}'
        with self.assertRaisesRegex(ValueError,'Duplicate'):parse_report_plan(raw)

    def test_known_facts_and_forecast_table_are_rendered(self):
        e=evidence();before=deepcopy(e);text=render_olist_report(e,DEFAULT_PLAN)
        self.assertTrue(report_table_matches(text,e['forecast']))
        self.assertIn('| source_unchanged | true |',text)
        self.assertIn('源文件保持不变。',text)
        self.assertIn('| future_rows_used | false |',text)
        self.assertIn('SQL 独立参考匹配：通过。',text)
        self.assertIn('179.5000',text)
        self.assertIn('1603',text)
        self.assertEqual(e,before)
        self.assertTrue(review_olist_report(text,e)['heuristic_checks_passed'])

    def test_boolean_values_are_not_hardcoded(self):
        e=evidence();e['cleaning']['source_unchanged']=False;e['cleaning']['gate_passed']=False
        text=render_olist_report(e,DEFAULT_PLAN)
        self.assertIn('| source_unchanged | false |',text)
        self.assertIn('源文件已发生改变。质量门禁未通过。',text)

    def test_bool_strings_and_contradictory_counts_rejected(self):
        for key,value in [('source_unchanged','true'),('item_rows_after',1)]:
            e=evidence();e['cleaning'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate_evidence(e)

    def test_wrong_target_week_history_and_values_rejected(self):
        for mutation in ['target','history','nan','missing_warning']:
            e=evidence()
            if mutation=='target':e['forecast']['forecasts'][0]['week_start']='2018-05-28'
            if mutation=='history':e['sql']['history_count']=73
            if mutation=='nan':e['forecast']['forecasts'][0]['predicted_item_count']=float('nan')
            if mutation=='missing_warning':del e['cleaning']['warnings']
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_evidence(e)

    def test_empty_warning_list_is_not_missing_evidence(self):
        e=evidence();e['cleaning']['warnings']=[]
        self.assertIn('输入 warnings 列表为空。',render_olist_report(e,DEFAULT_PLAN))

    def test_planner_cannot_replace_fact_values(self):
        with self.assertRaises(ValueError):
            generate_structured_report(evidence(),lambda instruction:json.dumps({**DEFAULT_PLAN,'source_unchanged':False,'t+1':999}))

    def test_layout_change_preserves_same_forecast_and_booleans(self):
        e=evidence()
        one=render_olist_report(e,DEFAULT_PLAN)
        two=render_olist_report(e,dict(title='Olist 周度预测报告',section_order=list(reversed(DEFAULT_PLAN['section_order']))))
        for row in ['| source_unchanged | true |','| item_rows_after | 112650 |','| future_rows_used | false |']:
            self.assertIn(row,one);self.assertIn(row,two)
        self.assertTrue(report_table_matches(two,e['forecast']))

    def test_real_graph_optional_mode_renders_facts(self):
        fixture=graph_fixture.OlistWorkflowTest('test_actual_graph_success_preserves_structured_evidence_and_source')
        fixture.setUp()
        try:
            graph=build_olist_workflow(lambda instruction:json.dumps(DEFAULT_PLAN,ensure_ascii=False),InMemorySaver(),report_mode='structured_plan')
            state=graph.invoke(fixture.state,fixture.cfg)
            self.assertEqual(state['status'],'completed')
            self.assertEqual(state['report_generation_mode'],'structured_plan')
            self.assertTrue(report_table_matches(state['report_result'],state['forecast_result']))
        finally:fixture.tearDown()

    def test_real_graph_invalid_plan_is_not_published_as_completed(self):
        fixture=graph_fixture.OlistWorkflowTest('test_actual_graph_success_preserves_structured_evidence_and_source')
        fixture.setUp()
        try:
            graph=build_olist_workflow(lambda instruction:'源文件保持不变：否',InMemorySaver(),report_mode='structured_plan')
            state=graph.invoke(fixture.state,fixture.cfg)
            self.assertEqual(state['status'],'needs_review')
            self.assertEqual(state['report_result'],'')
            self.assertEqual(state['report_model_output'],'源文件保持不变：否')
        finally:fixture.tearDown()


if __name__=='__main__':unittest.main()
