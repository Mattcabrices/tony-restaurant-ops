"""Transparent weekday baselines with rolling time cutoffs; no model or network calls."""
from datetime import date, timedelta
from statistics import median
VERSION="weekday-median-v2-horizon"

def baseline(rows, target, seasonal=False, horizon_days=0, recent_count=8):
    if recent_count not in (4, 8):
        raise ValueError('Unsupported baseline window.')
    target = date.fromisoformat(target) if isinstance(target, str) else target
    cutoff = target-timedelta(days=max(0, horizon_days))
    history = [r for r in rows if date.fromisoformat(r['day']) < cutoff
               and r.get('quality', 'complete') in {'complete', 'reconstructed'}]
    recent = [r for r in history if date.fromisoformat(r['day']).weekday() == target.weekday()
                and (target-date.fromisoformat(r['day'])).days <= 84][-recent_count:]
    if len(recent) < 4:
        return {'available': False, 'reason': 'Fewer than four usable same-weekday observations in the preceding 12 weeks.'}
    value = float(median(r['value'] for r in recent))
    used = list(recent)
    if seasonal:
        annual = [r for r in history if date.fromisoformat(r['day']).weekday() == target.weekday()
                  and 350 <= (target-date.fromisoformat(r['day'])).days <= 378]
        if len(annual) < 3:
            return {'available': False, 'reason': 'Insufficient prior-year observations for the seasonal candidate.'}
        value = 0.5 * value + 0.5 * float(median(r['value'] for r in annual))
        used += annual
    return {'available': True, 'value': round(value, 2), 'training_dates': [r['day'] for r in used],
            'model_version': VERSION + ('-annual-blend' if seasonal else ''), 'n': len(used)}

def errors(pairs):
    if not pairs:
        return {'n': 0, 'mae': None, 'wape': None, 'bias': None, 'coverage': None}
    absolute = sum(abs(p['predicted']-p['actual']) for p in pairs)
    denominator = sum(abs(p['actual']) for p in pairs)
    covered = [p['low'] <= p['actual'] <= p['high'] for p in pairs if 'low' in p and 'high' in p]
    return {'n': len(pairs), 'mae': round(absolute/len(pairs), 3),
            'wape': round(100*absolute/denominator, 3) if denominator else None,
            'bias': round(sum(p['predicted']-p['actual'] for p in pairs)/len(pairs), 3),
            'coverage': sum(covered)/len(covered) if covered else None}

def backtest(rows, seasonal=False, limit=90, horizon_days=0):
    rows = sorted(rows, key=lambda r: r['day'])
    results = []
    for actual in rows[-limit:]:
        if actual.get('quality', 'complete') not in {'complete', 'reconstructed'}:
            continue
        forecast = baseline(rows, actual['day'], seasonal, horizon_days)
        if forecast['available']:
            results.append({'day': actual['day'], 'predicted': forecast['value'], 'actual': actual['value'],
                            'training_end': max(forecast['training_dates']),
                            'issue_day': (date.fromisoformat(actual['day'])-timedelta(days=horizon_days)).isoformat()})
    return {'metrics': errors(results), 'folds': results,
            'limitations': ['Rolling date cutoffs prevent future target values entering training.',
                            'Retrospective evaluation uses currently available revised actuals; original point-in-time vintages are not available.']}

def forecast(rows, target, horizon_days=0):
    result = baseline(sorted(rows, key=lambda r: r['day']), target, horizon_days=horizon_days)
    if not result['available']:
        return result
    # Calibration is drawn strictly from dates before this forecast's target.
    cutoff = (date.fromisoformat(target)-timedelta(days=horizon_days)).isoformat()
    prior = [r for r in rows if r['day'] < cutoff]
    evaluation = backtest(prior, horizon_days=horizon_days)
    residuals = sorted(abs(f['predicted']-f['actual']) for f in evaluation['folds'])
    if len(residuals) < 14:
        return {'available': False, 'reason': 'Fewer than 14 rolling residuals; a defensible uncertainty range is pending.'}
    radius = residuals[min(len(residuals)-1, int(0.9*(len(residuals)+1)))]
    result.update(low=round(result['value']-radius, 2), high=round(result['value']+radius, 2),
                  calibration_n=len(residuals), interval_label='Nominal 90% empirical absolute-error interval; coverage is tracked, not guaranteed.',
                  backtest=evaluation['metrics'])
    return result
