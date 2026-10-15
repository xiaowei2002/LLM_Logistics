from datetime import date,timedelta
import unittest
from core.tools.olist_weekly_forecast import forecast_olist_weekly, MODELS
from tests.system_eval.eval_olist_forecast_v1 import score, rolling_rows
import pandas as pd


class OlistWeeklyForecastTest(unittest.TestCase):
    def setUp(self):
        start = date(2017,1,2)
        self.rows = [dict(category_key='a', week_start=(start+timedelta(weeks=i)).isoformat(), observed_item_count=i+1) for i in range(60)]
        self.origin = (start+timedelta(weeks=60)).isoformat()

    def run_model(self, model='naive_last',rows=None,**kwargs):
        return forecast_olist_weekly(self.rows if rows is None else rows,'a',self.origin,model,**kwargs)

    def test_four_models_have_known_two_week_values(self):
        expected = {'naive_last':[60,60], 'moving_average_4':[58.5,58.5],
                    'moving_average_8':[56.5,56.5], 'seasonal_naive_52':[9,10]}
        for model in MODELS:
            with self.subTest(model=model):
                result = self.run_model(model)
                self.assertEqual([r['predicted_item_count'] for r in result['forecasts']],expected[model])
                self.assertEqual(result['forecasts'][0]['week_start'], self.origin)
                self.assertEqual(result['forecasts'][1]['week_start'],(date.fromisoformat(self.origin)+timedelta(weeks=1)).isoformat())
                self.assertLess(result['history_last_week'], self.origin)

    def test_future_target_row_rejected(self):
        with self.assertRaisesRegex(ValueError,'future'):
            self.run_model(rows=self.rows+[dict(category_key='a',week_start=self.origin,observed_item_count=9999)])

    def test_missing_duplicate_or_unsorted_history_rejected(self):
        for rows in [self.rows[:5]+self.rows[6:], self.rows[:5]+[self.rows[4]]+self.rows[5:],list(reversed(self.rows))]:
            with self.subTest(rows=rows[:1]),self.assertRaises(ValueError):
                self.run_model(rows=rows)

    def test_mixed_category_and_stale_history_rejected(self):
        bad = [dict(r) for r in self.rows]
        bad[0]['category_key'] = 'b'
        with self.assertRaisesRegex(ValueError,'category'):
            self.run_model(rows=bad)
        with self.assertRaisesRegex(ValueError,'immediately'):
            self.run_model(rows=self.rows[:-1])

    def test_invalid_counts_rejected(self):
        for value in [-1,1.2,float('nan'),float('inf'),True,'2']:
            bad = [dict(r) for r in self.rows]
            bad[0]['observed_item_count'] = value
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.run_model(rows=bad)

    def test_insufficient_seasonal_history_rejected(self):
        with self.assertRaisesRegex(ValueError,'Insufficient'):
            self.run_model('seasonal_naive_52', rows=self.rows[-51:])

    def test_invalid_models_horizons_dates_rejected(self):
        with self.assertRaises(ValueError):
            self.run_model('invented')
        for horizon in [0,3,True,1.0]:
            with self.subTest(horizon=horizon),self.assertRaises(ValueError):
                self.run_model(horizon=horizon)
        with self.assertRaises(ValueError):
            forecast_olist_weekly(self.rows,'a','2018-02-27','naive_last')

    def test_all_zero_counts_are_valid_but_zero_wmape_denominator_is_null(self):
        rows = [{**r,'observed_item_count':0} for r in self.rows]
        self.assertEqual(self.run_model(rows=rows)['forecasts'][0]['predicted_item_count'],0)
        metrics = score([dict(prediction=2,actual=0),dict(prediction=0,actual=0)])
        self.assertEqual(metrics['MAE'],1)
        self.assertAlmostEqual(metrics['RMSE'],2**0.5)
        self.assertIsNone(metrics['WMAPE_percent'])

    def test_metrics_known_errors_and_weighting(self):
        result = score([dict(prediction=2,actual=1),dict(prediction=7,actual=3)])
        self.assertEqual(result['MAE'],2.5)
        self.assertEqual(result['RMSE'],(8.5)**0.5)
        self.assertEqual(result['WMAPE_percent'],125)

    def test_future_label_perturbation_cannot_change_earlier_origin_predictions(self):
        start = date(2017,1,2)
        rows = [dict(category_key='a',week_start=(start+timedelta(weeks=i)).isoformat(), observed_item_count=i+1) for i in range(86)]
        changed = [dict(r) for r in rows]
        changed[80]['observed_item_count'] = 99999
        first = rolling_rows(pd.DataFrame(rows),'2018-05-21','2018-08-27',MODELS)
        second = rolling_rows(pd.DataFrame(changed),'2018-05-21','2018-08-27',MODELS)
        cutoff = rows[80]['week_start']
        a = [(r['origin_week'],r['model'],r['horizon'],r['prediction']) for r in first if r['origin_week']<=cutoff]
        b = [(r['origin_week'],r['model'],r['horizon'],r['prediction']) for r in second if r['origin_week']<=cutoff]
        self.assertEqual(a,b)


if __name__ == '__main__':
    unittest.main()
