import json
import tempfile
import unittest
from pathlib import Path
from core.graph.olist_report_contract import strengthen_olist_instruction,review_olist_report,MARKER
from tests.system_eval.eval_olist_workflow_v1 import preflight_report
from tests.system_eval.review_olist_reports_v11 import replay_reports


def evidence(unchanged=True):
    return dict(cleaning=dict(source_unchanged=unchanged,gate_passed=True,warnings=[]),
        forecast=dict(category_key='a',origin_week='2018-05-21',model='naive_last',future_rows_used=False,
            forecasts=[dict(week_start='2018-05-21',predicted_item_count=57.0),dict(week_start='2018-05-28',predicted_item_count=57.0)]))


class OlistReportContractTest(unittest.TestCase):
    def test_observed_chinese_inversion_is_flagged_despite_correct_raw_boolean(self):
        report='| 源文件是否变更 | 是（source_unchanged = true） |'
        result=review_olist_report(report,evidence())
        self.assertFalse(result['heuristic_checks_passed'])
        self.assertEqual(result['boolean_and_scope_review_flags'][0]['type'],'chinese_boolean_polarity_contradiction')

    def test_correct_inverse_and_positive_questions_supported(self):
        for text in ['| 源文件是否变更 | 否（source_unchanged = true） |',
                     '| 源文件是否保持不变 | 是 |','| source_unchanged | true |']:
            with self.subTest(text=text):self.assertTrue(review_olist_report(text,evidence())['heuristic_checks_passed'])

    def test_false_source_unchanged_is_not_hardcoded_to_unchanged(self):
        self.assertTrue(review_olist_report('| 源文件是否变更 | 是 |',evidence(False))['heuristic_checks_passed'])
        self.assertFalse(review_olist_report('| 源文件是否保持不变 | 是 |',evidence(False))['heuristic_checks_passed'])

    def test_raw_boolean_contradictions_flagged_for_all_three_fields(self):
        for row in ['| source_unchanged | false |','| gate_passed | false |','| future_rows_used | true |']:
            with self.subTest(row=row):self.assertFalse(review_olist_report(row,evidence())['heuristic_checks_passed'])

    def test_warning_scope_overgeneralization_flagged(self):
        result=review_olist_report('以下警告均已保留，对应字段均未用于过滤预测记录。',evidence())
        self.assertFalse(result['heuristic_checks_passed'])
        self.assertEqual(result['boolean_and_scope_review_flags'][0]['type'],'warning_action_scope_needs_review')
        self.assertTrue(review_olist_report('时间字段未用于筛选预测记录；未知类别保留原值并添加标记。',evidence())['heuristic_checks_passed'])

    def test_contract_preserves_exact_payload_and_adds_meanings(self):
        original='只转述事实。\n'+MARKER+json.dumps(evidence(),ensure_ascii=False)
        new=strengthen_olist_instruction(original)
        self.assertEqual(json.loads(new.partition(MARKER)[2]),evidence())
        self.assertIn('source_unchanged=true：源文件保持不变',new)
        self.assertIn('unknown_category 只转述',new)

    def test_invalid_boolean_evidence_rejected(self):
        data=evidence();data['cleaning']['source_unchanged']='true'
        with self.assertRaises(ValueError):strengthen_olist_instruction(MARKER+json.dumps(data))
        with self.assertRaises(ValueError):strengthen_olist_instruction('no evidence')

    def test_review_never_certifies_full_text_accuracy(self):
        result=review_olist_report('无事实声明',evidence())
        self.assertTrue(result['full_text_review_required'])
        self.assertIsNone(result['verified_full_text_factual_accuracy'])

    def test_replay_does_not_modify_original_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'old';source.mkdir()
            (source/'summary.json').write_text(json.dumps(dict(evaluation_id='olist_workflow_v1',mode='real_report')))
            for case in ['OW701','OW702','OW703']:
                data=evidence();instruction='任务\n'+MARKER+json.dumps(data)
                state=dict(report_instruction=instruction,report_evidence=data,forecast_result=data['forecast'],report_result='| 源文件是否变更 | 是（source_unchanged = true） |')
                (source/f'{case}.json').write_text(json.dumps(dict(case_id=case,state=state,automatic_passed=True)))
            before={p.name:p.read_bytes() for p in source.iterdir()}
            calls=[]
            def reporter(instruction):
                calls.append(instruction)
                return preflight_report(instruction)+'\n| source_unchanged | true |'
            result=replay_reports(source,root/'new',True,reporter)
            self.assertEqual(result['automatically_passed_reports'],3)
            self.assertEqual(result['report_generate_calls'],3)
            self.assertEqual(result['original_reports_with_flags'],3)
            self.assertEqual(result['pending_full_text_reviews'],3)
            self.assertFalse(result['sql_rerun'])
            self.assertFalse(result['forecast_rerun'])
            self.assertEqual(before,{p.name:p.read_bytes() for p in source.iterdir()})
            with self.assertRaises(ValueError):replay_reports(source,root/'new')


if __name__=='__main__':
    unittest.main()
