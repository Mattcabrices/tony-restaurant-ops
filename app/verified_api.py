"""Versioned local read-only contract for another authorized assistant."""
from datetime import datetime
from .calculations import ET
from .storage import utcnow
from .source_health import inspect


def summary(db, ledger, corner=None):
    # Intentionally no messages, owner notes, employee details or raw source text.
    clover = db.rows('SELECT MIN(day) first_day,MAX(day) last_day,COUNT(*) days FROM clover_days')[0]
    metrics = db.rows('SELECT day,paid_orders,gross_line_cents,custom_lines FROM clover_days ORDER BY day DESC LIMIT 7')
    result = {'schema_version':'tony.verified.v1','generated_at':utcnow(),'business_timezone':'America/New_York',
        'workweek':'Wednesday-Tuesday','access':'Authenticated local read-only snapshot; no refresh or execution',
        'sources':inspect(db)['sources'],
        'clover':{'coverage':clover,'metric_definition':'Gross line-item sales before report adjustments; not Toast net sales',
            'recent_days':[{'day':r['day'],'paid_orders':r['paid_orders'],'gross_line_sales':round(r['gross_line_cents']/100,2),
                'unclassified_custom_lines':r['custom_lines']} for r in metrics]},
        'exceptions':{'counts':ledger.listing()['counts']},
        'gaps':['R365 accounting/inventory access pending.','Toast labor may omit Homebase-only clock-ins.',
            'Clover static item aggregates do not retain channel; direct order lookup is a separate read.',
            'No public API, external assistant identity, or automatic transfer is enabled.'],
        'actions':[]}
    saved = db.get('verified_toast_snapshot',{})
    if saved:
        today = saved.get('today') or {}
        result['toast'] = {'retrieved_at':saved.get('fresh_at'),'business_date':saved.get('business_date'),
            'metric_definition':'Toast net sales; never add directly to Clover gross line sales',
            'net_sales':today.get('net_sales'),'orders':today.get('orders'),
            'limitations':saved.get('limitations',[])[:12]}
    if corner:
        try:
            target = datetime.now(ET).date().isoformat()
            rows = corner.store.rows('''SELECT created_at,target_day,metric,dimension,value,low,high,model_version
                FROM predictions WHERE target_day>=? ORDER BY target_day,id DESC LIMIT 30''',(target,))
            result['predictions'] = rows
            result['prediction_limitations'] = 'Issued research forecasts only; ranges are empirical estimates, not guarantees.'
        except Exception:
            result['predictions'] = []
            result['gaps'].append('Research predictions unavailable; no values inferred.')
    return result
