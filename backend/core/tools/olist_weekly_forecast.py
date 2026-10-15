"""Olist weekly point forecasts from explicitly supplied historical SQL rows.
No file/database access, LLM, future covariates, or production/BOM models.
The origin is the first unobserved week's Monday, not the last observed week.
"""
from __future__ import annotations
from datetime import date, timedelta
from statistics import fmean
import math

MODELS = ('naive_last', 'moving_average_4', 'moving_average_8', 'seasonal_naive_52')


def monday(value):
    if not isinstance(value, str):
        raise ValueError('Week must be an ISO date string')
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('Week must be YYYY-MM-DD') from exc
    if parsed.isoformat() != value or parsed.weekday() != 0:
        raise ValueError('Week must be a Monday in YYYY-MM-DD format')
    return parsed


def forecast_olist_weekly(history_rows, category_key, origin_week, model, horizon=2):
    """Structured adapter: input rows category_key/week_start/observed_item_count.
    Rejects future, mixed-category, duplicated, missing, or out-of-order rows.
    Predictions are expected record counts (may be fractional), not probabilities.
    """
    if model not in MODELS:
        raise ValueError('Unsupported forecast model')
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon not in (1, 2):
        raise ValueError('horizon must be 1 or 2 weeks')
    origin = monday(origin_week)
    if not isinstance(category_key, str) or not category_key or category_key == '__UNKNOWN_CATEGORY__':
        raise ValueError('A known nonblank category is required')
    if not isinstance(history_rows, (list, tuple)) or not history_rows:
        raise ValueError('Historical rows are required')
    values, previous = [], None
    for row in history_rows:
        if row.get('category_key') != category_key:
            raise ValueError('Mixed category in history')
        week = monday(row.get('week_start'))
        if week >= origin:
            raise ValueError('History contains origin or future target week')
        if previous is not None and week != previous + timedelta(days=7):
            raise ValueError('History must be ordered, unique and contiguous weekly rows')
        value = row.get('observed_item_count')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or int(value) != value:
            raise ValueError('Observed count must be a finite nonnegative integer')
        values.append(float(value))
        previous = week
    if previous != origin - timedelta(days=7):
        raise ValueError('History must end immediately before the origin')
    minimum = {'naive_last':1, 'moving_average_4':4, 'moving_average_8':8, 'seasonal_naive_52':52}[model]
    if len(values) < minimum:
        raise ValueError('Insufficient historical weeks for selected model')
    if model == 'naive_last':
        predictions = [values[-1]] * horizon
    elif model.startswith('moving_average_'):
        # Fixed-origin direct horizon: mean of the last k actual observations.
        predictions = [fmean(values[-minimum:])] * horizon
    else:
        predictions = [values[-52+h] for h in range(horizon)]
    return dict(tool='olist_weekly_forecast_v1', category_key=category_key,
        origin_week=origin_week, model=model, horizon_weeks=horizon,
        history_start_week=history_rows[0]['week_start'], history_last_week=previous.isoformat(),
        history_rows=len(values), future_rows_used=False,
        demand_definition='Observed order-item record count by purchase week/category',
        forecasts=[{'horizon':h+1, 'week_start':(origin+timedelta(weeks=h)).isoformat(),
                    'predicted_item_count':predictions[h]} for h in range(horizon)],
        note='Point expected counts may be fractional; no intervals or inventory decision implied')
