"""Evidence-boundary tests; no LLM, MCP, MySQL or file reads.
Run from backend: python -m tests.test_report_node_v3
"""
import unittest
from core.graph.nodes.report_node_v3 import build_report_node


class CapturingAgent:
    def __init__(self):
        self.instructions = []

    def generate(self, instruction):
        self.instructions.append(instruction)
        return '测试报告'


class ReportNodeV3Test(unittest.TestCase):
    def invoke(self, state):
        agent = CapturingAgent()
        result = build_report_node(agent)(state)
        return result, agent.instructions

    def test_request_and_paths_alone_do_not_call_agent(self):
        result, calls = self.invoke(dict(user_query='生成报告',
            data_file_path='input.csv', cleaned_file_path='cleaned.csv'))
        self.assertEqual(result['report_status'], 'error')
        self.assertEqual(calls, [])

    def test_true_approval_does_not_invent_execution_history(self):
        result, calls = self.invoke(dict(cleaning_large_deletion_approved=True))
        self.assertEqual(result['report_status'], 'success')
        self.assertIn('"cleaning_large_deletion_approved": true', calls[0])
        self.assertNotIn('超过 10%', calls[0])
        self.assertNotIn('人工确认后未重新执行 CleaningAgent', calls[0])
        self.assertNotIn('重新通过 Cleaning Quality Gate 后', calls[0])

    def test_false_and_string_false_alone_do_not_establish_approval(self):
        for flag in (False, 'false', 'true', None):
            with self.subTest(flag=flag):
                result, calls = self.invoke(dict(cleaning_large_deletion_approved=flag))
                self.assertEqual(result['report_status'], 'error')
                self.assertEqual(calls, [])

    def test_missing_and_false_approval_are_distinguished(self):
        _, missing = self.invoke(dict(sql_result='quantity: 42'))
        _, explicit = self.invoke(dict(sql_result='quantity: 42',
                                      cleaning_large_deletion_approved=False))
        self.assertNotIn('"cleaning_large_deletion_approved": false', missing[0])
        self.assertIn('"cleaning_large_deletion_approved": false', explicit[0])

    def test_quality_records_preserved_without_new_file_verification(self):
        quality = dict(source_row_count=8, cleaned_row_count=5,
                       removed_row_count=3, passed=False,
                       errors=['台账不一致'], warnings=['保留警告'])
        result, calls = self.invoke(dict(cleaning_quality=quality))
        self.assertEqual(result['report_status'], 'success')
        self.assertIn('台账不一致', calls[0])
        self.assertIn('保留警告', calls[0])
        self.assertIn('"removed_row_count": 3', calls[0])
        self.assertNotIn('37.5', calls[0])
        self.assertNotIn('以下内容由 Python 对实际落盘文件', calls[0])

    def test_sql_error_does_not_assert_other_modules_skipped(self):
        result, calls = self.invoke(dict(sql_status='error',sql_error='数据库连接失败'))
        self.assertEqual(result['report_status'], 'success')
        self.assertIn('数据库连接失败', calls[0])
        self.assertIn('sql_status = "error"', calls[0])
        self.assertNotIn('所有下游模块均未启动', calls[0])

    def test_forecast_answer_is_not_promoted_to_tool_evidence(self):
        _, calls = self.invoke(dict(forecast_status='success',
                                  forecast_result=dict(answer='M001 t+1=12.5')))
        self.assertIn('预测智能体自然语言回答（来源声明）', calls[0])
        self.assertNotIn('【需求预测工具结构化结果】', calls[0])

    def test_failed_forecast_cannot_supply_verified_tool_payload(self):
        result, calls = self.invoke(dict(forecast_status='error',
            forecast_result=dict(tool_result=dict(status='success',forecast={'M001':12.5}))))
        self.assertEqual(result['report_status'], 'error')
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
