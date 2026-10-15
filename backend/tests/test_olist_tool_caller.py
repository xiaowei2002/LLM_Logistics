import json
import unittest
from copy import deepcopy
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from core.agents.olist_tool_caller import OlistToolCaller,ControlledToolModel,OlistToolCallRejected
from core.graph.olist_workflow import build_olist_workflow
from core.graph.olist_structured_report import DEFAULT_PLAN
from tests import test_olist_workflow as fixture_module


class MutatingModel(ControlledToolModel):
    def __init__(self,mutation):self.mutation=mutation
    def invoke(self,messages):
        message=super().invoke(messages)
        calls=deepcopy(message.tool_calls)
        calls=self.mutation(calls)
        return AIMessage(content='model text is never a tool result',tool_calls=calls)


class OlistToolCallerTest(unittest.TestCase):
    def setUp(self):
        self.args=dict(category_key='a',origin_week='2018-05-21',model='naive_last',horizon=2)
        self.executed=[]
    def executor(self,args):
        self.executed.append(args);return dict(prediction=3.5)
    def test_actual_dispatch_returns_raw_executor_payload_and_audit(self):
        caller=OlistToolCaller(ControlledToolModel(),controlled=True)
        result,audit=caller.execute('forecast',self.args,self.executor)
        self.assertEqual(result,{'prediction':3.5})
        self.assertEqual(self.executed,[self.args])
        self.assertTrue(audit['arguments_validated'])
        self.assertEqual(audit['tool_executions'],1)
    def reject(self,mutation):
        caller=OlistToolCaller(MutatingModel(mutation),controlled=True)
        with self.assertRaises(OlistToolCallRejected) as caught:caller.execute('forecast',self.args,self.executor)
        self.assertEqual(self.executed,[])
        self.assertEqual(caught.exception.audit['tool_executions'],0)
    def test_plain_text_or_missing_call_rejected(self):self.reject(lambda calls:[])
    def test_parallel_calls_rejected_before_any_execution(self):self.reject(lambda calls:calls+deepcopy(calls))
    def test_foreign_tool_rejected(self):
        def change(calls):calls[0]['name']='execute_arbitrary_sql';return calls
        self.reject(change)
    def test_changed_date_rejected(self):
        def change(calls):calls[0]['args']['origin_week']='2030-01-01';return calls
        self.reject(change)
    def test_added_path_or_sql_argument_rejected(self):
        def change(calls):calls[0]['args']['sql']='DROP TABLE x';return calls
        self.reject(change)
    def test_changed_frozen_model_rejected(self):
        def change(calls):calls[0]['args']['model']='moving_average_8';return calls
        self.reject(change)
    def test_bool_is_not_accepted_as_integer_horizon(self):
        def change(calls):calls[0]['args']['horizon']=True;return calls
        self.reject(change)
    def test_executor_error_is_not_retried_or_replaced_with_prose(self):
        caller=OlistToolCaller(ControlledToolModel(),controlled=True)
        def fail(args):self.executed.append(args);raise RuntimeError('controlled DB failure')
        with self.assertRaises(OlistToolCallRejected):caller.execute('forecast',self.args,fail)
        self.assertEqual(len(self.executed),1)
        self.assertEqual(caller.events[0]['execution_status'],'rejected_or_failed')
    def test_provider_error_text_is_not_recorded(self):
        class Broken(ControlledToolModel):
            def invoke(self,messages):raise RuntimeError('SECRET_EXAMPLE')
        caller=OlistToolCaller(Broken(),controlled=True)
        with self.assertRaises(OlistToolCallRejected) as caught:caller.execute('forecast',self.args,self.executor)
        self.assertNotIn('SECRET_EXAMPLE',json.dumps(caught.exception.audit))
        self.assertEqual(self.executed,[])
    def test_actual_graph_runs_two_tools_and_forwards_raw_values(self):
        case=fixture_module.OlistWorkflowTest('test_actual_graph_success_preserves_structured_evidence_and_source');case.setUp()
        try:
            caller=OlistToolCaller(ControlledToolModel(),controlled=True)
            graph=build_olist_workflow(lambda instruction:json.dumps(DEFAULT_PLAN),InMemorySaver(),report_mode='structured_plan',tool_caller=caller)
            state=graph.invoke(case.state,case.cfg)
            self.assertEqual(state['status'],'completed')
            self.assertEqual(len(caller.events),2)
            self.assertTrue(state['sql_tool_call']['arguments_validated'])
            self.assertTrue(state['forecast_tool_call']['arguments_validated'])
            self.assertEqual(state['forecast_result'],state['report_evidence']['forecast'])
        finally:case.tearDown()
    def test_actual_graph_rejects_bad_tool_before_forecast_and_report(self):
        case=fixture_module.OlistWorkflowTest('test_actual_graph_success_preserves_structured_evidence_and_source');case.setUp()
        try:
            def wrong(calls):calls[0]['args']['origin_week']='2030-01-01';return calls
            caller=OlistToolCaller(MutatingModel(wrong),controlled=True)
            reports=[]
            graph=build_olist_workflow(lambda instruction:reports.append(instruction),InMemorySaver(),report_mode='structured_plan',tool_caller=caller)
            state=graph.invoke(case.state,case.cfg)
            self.assertEqual(state['status'],'failed')
            self.assertEqual(state['execution_trace'],['request','cleaning','sql'])
            self.assertEqual(state['tool_failure']['tool_executions'],0)
            self.assertEqual(reports,[])
        finally:case.tearDown()


if __name__=='__main__':unittest.main()
