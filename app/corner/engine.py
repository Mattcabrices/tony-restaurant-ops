"""Pure standard-library numerical routines. No I/O, credentials, tools or model calls."""
import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from statistics import median

VERSION = 'weekday-median-v2-horizon'


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


def analyze(payload):
    """Fixed analysis request; external text and executable code are not inputs."""
    target = payload['target']
    as_of = payload.get('as_of_day', target)
    horizon = (date.fromisoformat(target)-date.fromisoformat(as_of)).days
    if not 0 <= horizon <= 14:
        raise ValueError('Research horizon must be 0–14 days.')
    rows = [r for r in payload['rows'] if r['day'] < as_of]
    base = backtest(rows, horizon_days=horizon)
    seasonal = backtest(rows, seasonal=True, horizon_days=horizon)
    common = {f['day']: f for f in base['folds']}
    paired = [f for f in seasonal['folds'] if f['day'] in common]
    comparison = {'baseline': errors([common[f['day']] for f in paired]), 'seasonal': errors(paired)}
    return {'forecast': forecast(rows, target, horizon), 'backtest': base, 'seasonal_comparison': comparison, 'horizon_days': horizon,
            'feature_promotion': 'None. Seasonal and external-feature candidates require separate validation.',
            'input_sha256': hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            'model_version': VERSION}


CONTEXT_TEMPLATES={'bills_context':'sports.bills','uga_context':'sports.uga',
    'school_context':'school.local_no_inperson','rain_context':'weather.evening_rain_risk'}


def context_experiment(payload):
    """Historical forecasts require demonstrated feature availability at issue time."""
    if payload['template'] not in CONTEXT_TEMPLATES:
        raise ValueError('Unsupported external-feature template.')
    date.fromisoformat(payload['as_of_day'])
    rows=sorted((r for r in payload['rows'] if r['day']<payload['as_of_day'] and
        r.get('quality','complete') in {'complete','reconstructed'}),key=lambda r:r['day'])
    vintages={}
    for feature in payload['features']:
        if set(feature)!={'day','value','available_at','kind','issue_at'} or type(feature['value']) not in (int,float) or feature['value'] not in (0,1):
            raise ValueError('Invalid numeric feature vintage.')
        date.fromisoformat(feature['day'])
        stamp=datetime.fromisoformat(feature['available_at'])
        issue=datetime.fromisoformat(feature['issue_at'])
        if stamp.tzinfo is None or issue.tzinfo is None or feature['kind'] not in {'forecast','published_schedule','observed'}:
            raise ValueError('Feature timestamps and semantics must be explicit.')
        utc_issue=issue.astimezone(timezone.utc)
        if (utc_issue.date()!=date.fromisoformat(feature['day'])-timedelta(days=1) or utc_issue.hour not in (8,9)
                or utc_issue.minute!=30 or utc_issue.second or utc_issue.microsecond):
            raise ValueError('Feature issue time must be a broker-resolved 04:30 Eastern cutoff.')
        # Realized weather and retrospective observations are never forecast inputs.
        if feature['kind']!='observed':
            vintages.setdefault(feature['day'],[]).append((stamp,feature['value'],issue))
    def exposure(day):
        eligible=[(stamp,value) for stamp,value,issue in vintages.get(day,[]) if stamp<=issue]
        return max(eligible,key=lambda p:p[0])[1] if eligible else None
    # Residual for each date is itself produced without that date's actual value.
    residuals=[]
    for row in rows:
        value=exposure(row['day'])
        base=baseline(rows,row['day'],horizon_days=1)
        if value is not None and base['available'] and base['value']>0:
            residuals.append({'day':row['day'],'exposure':value,'residual':(row['value']-base['value'])/base['value']})
    folds=[]; missing=0
    for actual in rows[-90:]:
        flag=exposure(actual['day'])
        if flag is None:
            missing+=1
            continue
        issue_day=(date.fromisoformat(actual['day'])-timedelta(days=1)).isoformat()
        prior=[p for p in residuals if p['day']<issue_day and (date.fromisoformat(actual['day'])-date.fromisoformat(p['day'])).days<=365]
        groups={v:[p['residual'] for p in prior if p['exposure']==v] for v in (0,1)}
        base=baseline(rows,actual['day'],horizon_days=1)
        if not base['available'] or min(len(groups[0]),len(groups[1]))<8:
            continue
        adjustment=median(groups[flag])-median([p['residual'] for p in prior])
        candidate=max(0,base['value']*(1+adjustment))
        folds.append({'day':actual['day'],'issue_day':issue_day,'actual':actual['value'],
            'baseline':base['value'],'candidate':round(candidate,2),'exposure':flag,
            'training_end':max(base['training_dates']+[p['day'] for p in prior])})
    return {'available':len(folds)>=28,'paired_days':len(folds),'missing_feature_vintages':missing,
        'baseline':errors([{'predicted':p['baseline'],'actual':p['actual']} for p in folds]),
        'candidate':errors([{'predicted':p['candidate'],'actual':p['actual']} for p in folds]),
        'folds':folds,'template':payload['template'],'feature':CONTEXT_TEMPLATES[payload['template']],
        'model_version':'context-vintages-v1','input_sha256':hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest(),
        'limitations':['Feature must have been known by 04:30 Eastern on the prior issue day.',
            'Unknown exposure is excluded, never treated as zero. Realized weather is excluded.',
            'Eight prior residuals per exposure group and 28 paired target dates required.',
            'Revised historical sales; observational association only. No automatic forecast promotion.']}


def experiment(payload):
    """Two fixed numeric hypotheses. Never evaluates model-supplied code or expressions."""
    template = payload['template']
    if template in CONTEXT_TEMPLATES:
        return context_experiment(payload)
    if template not in {'recent_weekday', 'annual_blend'}:
        raise ValueError('Unapproved research template.')
    date.fromisoformat(payload['as_of_day'])
    rows = sorted((r for r in payload['rows'] if r['day'] < payload['as_of_day']
                   and r.get('quality', 'complete') in {'complete', 'reconstructed'}),key=lambda r:r['day'])
    pairs = []
    for actual in rows[-90:]:
        base = baseline(rows, actual['day'], horizon_days=1)
        candidate = baseline(rows, actual['day'], seasonal=template=='annual_blend',
                             horizon_days=1, recent_count=4 if template=='recent_weekday' else 8)
        if base['available'] and candidate['available']:
            pairs.append({'day':actual['day'],'actual':actual['value'],'baseline':base['value'],
                          'candidate':candidate['value'],
                          'issue_day':(date.fromisoformat(actual['day'])-timedelta(days=1)).isoformat(),
                          'training_end':max(base['training_dates']+candidate['training_dates'])})
    base_score = errors([{'predicted':p['baseline'],'actual':p['actual']} for p in pairs])
    candidate_score = errors([{'predicted':p['candidate'],'actual':p['actual']} for p in pairs])
    return {'available':len(pairs)>=28,'paired_days':len(pairs),'baseline':base_score,'candidate':candidate_score,
            'folds':pairs,'template':template,'model_version':'fixed-templates-v1',
            'input_sha256':hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest(),
            'limitations':['One-day-ahead rolling cutoffs; paired target dates only.',
                          'Revised historical actuals, not original as-known vintages.',
                          'No external-feature uplift, causal inference or automatic forecast promotion.',
                          'At least 28 paired dates required; promising findings require forward validation.']}
