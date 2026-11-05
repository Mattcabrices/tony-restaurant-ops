"""On-demand numerical estimates through the existing restricted research worker."""
import asyncio
from datetime import date, timedelta
import hashlib
import json
import math
import time
from .corner.isolation import IsolationUnavailable
from .storage import utcnow

SERIES = {'sales':('toast.net_sales','all','USD net sales'),
          'evening_sales':('toast.evening_sales','all','USD net sales, 17:00–24:00 order-open time'),
          'orders':('toast.orders','all','orders'),
          'wing_orders':('toast.item_portions','wings','portions'), 'wing_pieces':('toast.item_pieces','wings','pieces'),
          'finger_orders':('toast.item_portions','fingers','portions'), 'finger_pieces':('toast.item_pieces','fingers','pieces'),
          'labor_hours':('toast.labor_hours','all','recorded hours; Homebase coverage incomplete')}


class OperatorForecaster:
    def __init__(self, corner): self.corner, self.cache = corner, {}

    async def estimate(self, query, today, closeout):
        horizon=(query.target-today).days
        if not 0<=horizon<=14: raise ValueError('Forecast target must be today through 14 days ahead.')
        metric, dimension, unit=SERIES[query.metric]
        base={'source':'Stored Toast research observations', 'target':query.target.isoformat(), 'metric':metric,
              'dimension':dimension, 'unit':unit, 'horizon_days':horizon, 'available':False,
              'forecast_type':'On-demand planning estimate; not a scheduled Corner-issued prediction',
              'limitations':['Rolling time cutoffs prevent target-date leakage; revised actuals are not original historical vintages.',
                             'Empirical uncertainty and past error do not guarantee future accuracy.',
                             'No causal sports/weather uplift is applied. No paid search, model call or data collection is triggered.',
                             'Missing or flagged history is excluded, never replaced with zero.']}
        if not self.corner: return {**base,'reason':'Research connection is unavailable.'}
        # SQL is parameterized, source-specific and bounded. Metadata verifies metric cutoff.
        as_of=utcnow()
        rows=self.corner.store.rows('''SELECT o.day,o.value,o.quality,d.metadata,d.retrieved_at
            FROM observations o JOIN datasets d ON d.id=o.dataset_id
            WHERE o.metric=? AND o.dimension=? AND d.source='Toast' AND o.day>=? AND o.day<?
            AND o.available_at<=? AND o.id=(SELECT MAX(x.id) FROM observations x
              WHERE x.metric=o.metric AND x.dimension=o.dimension AND x.day=o.day AND x.available_at<=?)
            ORDER BY o.day LIMIT 450''', (metric,dimension,(today-timedelta(days=449)).isoformat(),today.isoformat(),
                                       as_of,as_of))
        usable=[]
        for row in rows:
            try: cutoff=json.loads(row['metadata']).get('closeout_hour')
            except (ValueError,TypeError): continue
            if cutoff==closeout and row['quality'] in {'complete','reconstructed'} and math.isfinite(row['value']):
                usable.append({k:row[k] for k in ('day','value','quality')})
        base['history']={'usable_days':len(usable),'first':usable[0]['day'] if usable else None,
                         'last':usable[-1]['day'] if usable else None,
                         'fresh_at':max((r['retrieved_at'] for r in rows),default=None),
                         'excluded_days':len(rows)-len(usable)}
        recent_dates={r['day'] for r in usable}
        base['history']['missing_recent_dates']=[(today-timedelta(days=i)).isoformat() for i in range(1,29)
                                               if (today-timedelta(days=i)).isoformat() not in recent_dates]
        if len(usable)<28: return {**base,'reason':'At least 28 usable dates are needed; four same-weekday observations and 14 rolling residuals must also be available.'}
        if (today-date.fromisoformat(usable[-1]['day'])).days>7:
            return {**base,'reason':'Latest usable actual is more than seven days old. Refresh historical collection before forecasting.'}
        if not self.corner.worker.status().get('ready'): return {**base,'reason':'Native research isolation is not ready; unrestricted analysis is disabled.'}
        payload={'rows':usable,'target':query.target.isoformat(),'as_of_day':today.isoformat()}
        key=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        if key in self.cache and time.monotonic()-self.cache[key][0]<300: return self.cache[key][1]
        try:
            async with self.corner.lock:
                if key in self.cache and time.monotonic()-self.cache[key][0]<300: return self.cache[key][1]
                result=await asyncio.to_thread(self.corner.worker.analyze,payload,deadline=time.monotonic()+25)
        except IsolationUnavailable: return {**base,'reason':'Restricted numerical worker is unavailable; no unrestricted fallback was used.'}
        forecast=result['forecast']; evaluation=result['backtest']
        answer={**base, 'available':bool(forecast.get('available')), 'reason':forecast.get('reason'),
                'model_version':result['model_version'], 'input_sha256':result['input_sha256'],
                'backtest':evaluation['metrics'],'recent_backtest_dates':evaluation['folds'][-10:]}
        if answer['available']:
            answer.update(predicted=forecast['value'],low=forecast['low'],high=forecast['high'],
                          interval_label=forecast['interval_label'], calibration_n=forecast['calibration_n'],
                          training_dates=forecast['training_dates'])
        if len(self.cache)>=32: self.cache.clear()
        self.cache[key]=(time.monotonic(),answer)
        return answer
