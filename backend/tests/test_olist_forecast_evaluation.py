import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from core.tools.olist_weekly_sql import sha
from tests.system_eval.eval_olist_forecast_v1 import (BOUNDARIES,select_models,evaluate_test,load_split)


class OlistForecastEvaluationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.weekly = self.root/'weekly'
        self.weekly.mkdir()
        self.selection = self.root/'selection'
        self.output = self.root/'test'
        weeks = list(pd.date_range('2017-01-02',periods=86,freq='7D').strftime('%Y-%m-%d'))
        selected = []
        for split,low,high in [('train',0,60),('validation',60,72),('test',72,86)]:
            rows = []
            for i,cat in enumerate('abcde',1):
                rows.extend(dict(category_key=cat,week_start=weeks[j],observed_item_count=i,split=split) for j in range(low,high))
            pd.DataFrame(rows).to_csv(self.weekly/f'weekly_{split}.csv',index=False)
        selected = [dict(category_key=c,train_item_count=i*60) for i,c in enumerate('abcde',1)]
        pd.DataFrame(selected).to_csv(self.weekly/'selected_categories.csv',index=False)
        self.summary = dict(evaluation_id='olist_weekly_sql_v1',status='success',
            independent_sql_reference_match=True,category_selection_uses_training_only=True,
            time_boundaries=BOUNDARIES,selected_categories=selected,
            output_sha256={p.name:sha(p) for p in self.weekly.iterdir()})
        self.save_summary()

    def tearDown(self):
        self.temp.cleanup()

    def save_summary(self):
        (self.weekly/'weekly_summary.json').write_text(json.dumps(self.summary))

    def select(self):
        return select_models(self.weekly,self.selection)

    def test_selection_succeeds_with_test_file_absent_and_ties_use_candidate_order(self):
        (self.weekly/'weekly_test.csv').unlink()
        result = self.select()
        self.assertEqual(set(result['chosen_models'].values()),{'naive_last'})
        self.assertFalse(result['test_file_opened_by_selection'])
        self.assertEqual(len(pd.read_csv(self.selection/'validation_predictions.csv')),440)

    def test_full_frozen_test_exact_coverage_and_constant_metrics(self):
        self.select()
        before = sha(self.selection/'selection.json')
        result = evaluate_test(self.weekly,self.selection,self.output)
        self.assertEqual(result['frozen_selection_predictions'],130)
        self.assertEqual(result['metrics']['frozen_selection']['MAE'],0)
        self.assertEqual(result['metrics']['frozen_selection']['WMAPE_percent'],0)
        self.assertEqual(result['metrics']['frozen_selection']['WMAPE_denominator'],390)
        self.assertEqual(sha(self.selection/'selection.json'),before)
        self.assertEqual(len(pd.read_csv(self.output/'test_predictions_all_baselines.csv')),520)

    def test_test_hash_change_rejected(self):
        self.select()
        with (self.weekly/'weekly_test.csv').open('a') as file:
            file.write('\n')
        with self.assertRaisesRegex(ValueError,'test split hash'):
            evaluate_test(self.weekly,self.selection,self.output)
        self.assertFalse(self.output.exists())

    def test_changed_training_inputs_block_test_before_test_read(self):
        self.select()
        with (self.weekly/'weekly_train.csv').open('a') as file:
            file.write('\n')
        (self.weekly/'weekly_test.csv').unlink()
        with self.assertRaisesRegex(ValueError,'Selection inputs changed'):
            evaluate_test(self.weekly,self.selection,self.output)

    def test_changed_code_blocks_test(self):
        self.select()
        with patch('tests.system_eval.eval_olist_forecast_v1.code_hashes',return_value={'changed':'hash'}):
            with self.assertRaisesRegex(ValueError,'protocol or code'):
                evaluate_test(self.weekly,self.selection,self.output)

    def test_modified_model_choice_rejected_against_validation_scores(self):
        self.select()
        path = self.selection/'selection.json'
        frozen = json.loads(path.read_text())
        frozen['chosen_models']['a'] = 'moving_average_4'
        path.write_text(json.dumps(frozen))
        with self.assertRaisesRegex(ValueError,'validation scores'):
            evaluate_test(self.weekly,self.selection,self.output)

    def test_duplicate_missing_and_out_of_range_rows_blocked_even_with_updated_hash(self):
        path = self.weekly/'weekly_train.csv'
        original = pd.read_csv(path)
        for variant in [pd.concat([original.iloc[:-1],original.iloc[[0]]],ignore_index=True),original.iloc[:-1]]:
            variant.to_csv(path,index=False)
            self.summary['output_sha256'][path.name] = sha(path)
            self.save_summary()
            with self.assertRaisesRegex(ValueError,'weekly rows'):
                select_models(self.weekly,self.selection)
        self.assertFalse(self.selection.exists())

    def test_existing_outputs_not_overwritten(self):
        self.selection.mkdir()
        sentinel = self.selection/'sentinel'
        sentinel.write_text('keep')
        with self.assertRaisesRegex(ValueError,'new output'):
            self.select()
        self.assertEqual(sentinel.read_text(),'keep')


if __name__ == '__main__':
    unittest.main()
