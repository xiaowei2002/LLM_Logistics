import csv
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from core.graph.olist_preparation import prepare_olist_snapshot
from core.graph.olist_workflow import build_olist_workflow,make_nodes,report_table_matches
from core.tools.olist_weekly_sql import build_weekly_dataset,sha
from tests.system_eval.eval_olist_forecast_v1 import select_models
from tests.system_eval.eval_olist_workflow_v1 import preflight_report,verify_success,TRACE


class OlistWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.source=self.root/'source';self.source.mkdir()
        orders,items=[],[]
        products=[[c,c] for c in 'abcde']
        for week in range(86):
            w=(date(2017,1,2)+timedelta(weeks=week)).isoformat()
            orders.append([f'o{week}','canceled',w+' 12:00:00'])
            pos=1
            for amount,c in enumerate('abcde',1):
                for _ in range(amount):
                    items.append([f'o{week}',str(pos),c,'1','0']);pos+=1
        for name,cols,rows in [
            ('orders',['order_id','order_status','order_purchase_timestamp'],orders),
            ('order_items',['order_id','order_item_id','product_id','price','freight_value'],items),
            ('products',['product_id','product_category_name'],products)]:
            with (self.source/f'olist_{name}_dataset.csv').open('w',newline='') as file:
                writer=csv.writer(file);writer.writerow(cols);writer.writerows(rows)
        prepare_olist_snapshot(self.source,self.root/'original_preparation')
        self.weekly=self.root/'weekly';self.selection=self.root/'selection'
        build_weekly_dataset(self.root/'original_preparation',self.weekly)
        select_models(self.weekly,self.selection)
        self.calls=[]
        def reporter(instruction):
            self.calls.append(instruction)
            return preflight_report(instruction)
        self.reporter=reporter
        self.graph=build_olist_workflow(reporter,InMemorySaver())
        self.state=dict(source_dir=str(self.source),weekly_dir=str(self.weekly),selection_dir=str(self.selection),
            run_dir=str(self.root/'run'),category_key='a',origin_week='2018-05-21',status='running',execution_trace=[])
        self.cfg={'configurable':{'thread_id':'test'}}

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self):
        return self.graph.invoke(self.state,self.cfg)

    def until(self,name):
        nodes=make_nodes(self.reporter)
        state=deepcopy(self.state)
        for step in ['request','cleaning','sql','forecast']:
            update=nodes[step](state)
            state.update(update)
            if step==name:return state,nodes

    def test_actual_graph_success_preserves_structured_evidence_and_source(self):
        before={p.name:sha(p) for p in self.source.iterdir()}
        result=self.invoke()
        self.assertTrue(all(verify_success(result,self.weekly).values()))
        self.assertEqual(result['execution_trace'],TRACE)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(before,{p.name:sha(p) for p in self.source.iterdir()})
        self.assertEqual(result['forecast_result']['forecasts'][0]['predicted_item_count'],1)

    def test_native_interrupt_before_disk_changes_and_resume_once(self):
        del self.state['origin_week']
        paused=self.invoke()
        self.assertTrue(paused.get('__interrupt__'))
        self.assertFalse(Path(self.state['run_dir']).exists())
        self.assertEqual(len(self.calls),0)
        result=self.graph.invoke(Command(resume={'origin_week':'2018-05-21'}),self.cfg)
        self.assertEqual(result['execution_trace'],TRACE)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(result['status'],'completed')

    def test_invalid_resume_stops_before_cleaning(self):
        del self.state['origin_week'];self.invoke()
        result=self.graph.invoke(Command(resume={'origin_week':'2018-05-22'}),self.cfg)
        self.assertEqual(result['execution_trace'],['request'])
        self.assertEqual(result['status'],'failed')
        self.assertEqual(len(self.calls),0)

    def test_unselected_category_and_existing_output_are_rejected(self):
        self.state['category_key']='other'
        result=self.invoke()
        self.assertEqual(result['execution_trace'],['request'])
        self.assertEqual(len(self.calls),0)
        self.state['category_key']='a'
        Path(self.state['run_dir']).mkdir()
        result=self.graph.invoke(self.state,{'configurable':{'thread_id':'existing'}})
        self.assertEqual(result['status'],'failed')

    def test_duplicate_source_key_stops_before_sql_and_report(self):
        path=self.source/'olist_order_items_dataset.csv'
        lines=path.read_text().splitlines();path.write_text('\n'.join(lines+[lines[1]])+'\n')
        result=self.invoke()
        self.assertEqual(result['execution_trace'],['request','cleaning'])
        self.assertEqual(result['status'],'failed')
        self.assertEqual(len(self.calls),0)
        self.assertFalse((Path(self.state['run_dir'])/'sql_snapshot').exists())

    def test_valid_but_different_source_version_stops(self):
        path=self.source/'olist_order_items_dataset.csv'
        path.write_text(path.read_text().replace(',1,0',',2,0',1))
        result=self.invoke()
        self.assertEqual(result['execution_trace'],['request','cleaning'])
        self.assertIn('frozen forecast data version',result['error'])
        self.assertEqual(len(self.calls),0)

    def test_facts_tamper_before_sql_blocks(self):
        state,nodes=self.until('cleaning')
        path=Path(state['run_dir'])/'preparation'/'order_item_facts.csv'
        with path.open('a') as file:file.write('\n')
        result=nodes['sql'](state)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(len(self.calls),0)

    def test_sql_database_tamper_before_forecast_blocks(self):
        state,nodes=self.until('sql')
        path=Path(state['run_dir'])/'sql_snapshot'/'olist_snapshot.sqlite3'
        with path.open('ab') as file:file.write(b'x')
        result=nodes['forecast'](state)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(len(self.calls),0)

    def test_future_row_rejected_before_report(self):
        state,nodes=self.until('sql')
        state['sql_result']['history_rows'].append(dict(category_key='a',week_start=state['origin_week'],observed_item_count=999))
        result=nodes['forecast'](state)
        self.assertEqual(result['status'],'failed')
        self.assertIn('future',result['error'])
        self.assertEqual(len(self.calls),0)

    def test_modified_forecast_value_rejected_before_report(self):
        state,nodes=self.until('forecast')
        state['forecast_result']['forecasts'][0]['predicted_item_count']=999
        result=nodes['report'](state)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(len(self.calls),0)

    def test_report_error_propagates(self):
        def fail(instruction):raise RuntimeError('controlled report failure')
        graph=build_olist_workflow(fail,InMemorySaver())
        result=graph.invoke(self.state,self.cfg)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['execution_trace'],TRACE)
        self.assertIn('controlled report failure',result['error'])

    def test_changed_choice_fails_before_source_copy(self):
        path=self.selection/'selection.json'
        frozen=json.loads(path.read_text());frozen['chosen_models']['a']='moving_average_4'
        path.write_text(json.dumps(frozen))
        result=self.invoke()
        self.assertEqual(result['execution_trace'],['request'])
        self.assertIn('validation evidence',result['error'])

    def test_report_table_check_detects_wrong_number(self):
        state,nodes=self.until('forecast')
        update=nodes['report'](state)
        self.assertTrue(report_table_matches(update['report_result'],state['forecast_result']))
        self.assertFalse(report_table_matches(update['report_result'].replace('1.0000','999.0000'),state['forecast_result']))


if __name__=='__main__':
    unittest.main()
