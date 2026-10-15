import unittest
from unittest.mock import patch
from tests.test_olist_report_contract import evidence
from core.graph.olist_report_contract import review_olist_report
from core.graph.olist_workflow import make_nodes


class OlistReportV12Test(unittest.TestCase):
    def test_observed_timestamp_specific_prose_is_not_generalized_warning(self):
        texts=[
            '三条时间戳警告的 action 均明确包含 "not used to filter forecast records"，即 order_approved_at、order_delivered_carrier_date、order_delivered_customer_date 未用于筛选预测记录。unknown_category 警告保留原始类目并添加派生标记。',
            '- 共保留 5 条警告：3 个可选时间戳字段均未用于筛选预测记录；未知类目保留原始类目并添加派生标记；无明细订单保留且不虚构明细需求。']
        for text in texts:
            with self.subTest(text=text):self.assertTrue(review_olist_report(text,evidence())['heuristic_checks_passed'])

    def test_original_overbroad_warning_still_flagged(self):
        self.assertFalse(review_olist_report('以下警告均已保留，对应字段均未用于过滤预测记录：',evidence())['heuristic_checks_passed'])
        self.assertFalse(review_olist_report('所有警告字段均未用于筛选预测记录。',evidence())['heuristic_checks_passed'])

    def test_observed_prose_source_polarity_error_detected(self):
        text='源文件保持不变：否（`source_unchanged = true`，即源文件未改变）。'
        flags=review_olist_report(text,evidence())['boolean_and_scope_review_flags']
        self.assertEqual(flags[0]['type'],'prose_boolean_polarity_contradiction')

    def test_positive_and_negative_prose_questions_correct(self):
        for text in ['源文件保持不变：是（source_unchanged=true）。','源文件是否变更：否（source_unchanged=true）。']:
            with self.subTest(text=text):self.assertTrue(review_olist_report(text,evidence())['heuristic_checks_passed'])
        self.assertFalse(review_olist_report('源文件是否变更：是。',evidence())['heuristic_checks_passed'])

    def test_false_source_unchanged_supported_in_prose(self):
        self.assertTrue(review_olist_report('源文件保持不变：否。',evidence(False))['heuristic_checks_passed'])
        self.assertTrue(review_olist_report('源文件是否变更：是。',evidence(False))['heuristic_checks_passed'])

    def test_independent_verification_absence_requires_scope(self):
        data=evidence();data['sql']={'independent_reference_match':True}
        self.assertFalse(review_olist_report('未提供独立验证记录。',data)['heuristic_checks_passed'])
        for text in ['未提供预测模型的独立验证记录。','未提供独立验证的详细记录。']:
            with self.subTest(text=text):self.assertTrue(review_olist_report(text,data)['heuristic_checks_passed'])

    def test_generation_with_prose_contradiction_is_retained_but_not_completed(self):
        data=evidence();f=data['forecast']
        f.update(history_last_week='2018-05-14',history_rows=72)
        p=dict(execution_type='deterministic',source_unchanged=True,cleaning_gate=dict(tables={'order_items':{'rows':1}},passed=True,warnings=[]),facts_rows=1,deleted_rows=0,facts_sha256='facts')
        sql=dict(facts_sha256='facts',db_sha256='db',history_last_week='2018-05-14',history_rows=[])
        state=dict(run_dir='/unused',preparation=p,sql_result=sql,forecast_result=f,
                   origin_week=f['origin_week'],category_key='a',model='naive_last',selection_sha256='frozen')
        with patch('core.graph.olist_workflow.sha',side_effect=['facts','db']),patch('core.graph.olist_workflow.forecast_olist_weekly',return_value=f):
            update=make_nodes(lambda instruction:'源文件保持不变：否（source_unchanged=true）。')['report'](state)
        self.assertEqual(update['status'],'needs_review')
        self.assertIn('report_result',update)
        self.assertFalse(update['report_review']['heuristic_checks_passed'])


if __name__=='__main__':unittest.main()
