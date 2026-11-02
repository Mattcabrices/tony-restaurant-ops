"""Persistent hypotheses executed only through fixed, isolated numeric templates."""
from datetime import datetime
import hashlib
import json
import time

from ..calculations import ET
from .store import METRICS, clean, now
from .engine import CONTEXT_TEMPLATES
from .features import numeric_vintages

TEMPLATES = {'recent_weekday', 'annual_blend'} | set(CONTEXT_TEMPLATES)


def propose(service, proposal, origin='AI'):
    if not isinstance(proposal, dict) or set(proposal) != {'template','metric','dimension','hypothesis'}:
        raise ValueError('Use the fixed research proposal schema.')
    template, metric, dimension = (proposal[k] for k in ('template','metric','dimension'))
    if not isinstance(template,str) or template not in TEMPLATES or not isinstance(metric,str) or metric not in METRICS or metric.startswith('weather.'):
        raise ValueError('Unapproved numeric research template or metric.')
    if not isinstance(dimension,str) or not service.store.rows('SELECT id FROM observations WHERE metric=? AND dimension=? LIMIT 1',(metric,dimension)):
        raise ValueError('Research series must exist in the approved numeric store.')
    title = proposal['hypothesis']
    if not isinstance(title,str) or not 1 <= len(title.strip()) <= 300:
        raise ValueError('Hypothesis text must be brief plain data.')
    key = ':'.join(('plan',template,metric,dimension))
    existing = service.store.rows('SELECT id FROM research_plans WHERE plan_key=?',(key,))
    if existing:
        return existing[0]['id']
    if service.store.rows('SELECT COUNT(*) n FROM research_plans')[0]['n'] >= 50:
        raise ValueError('Research hypothesis registry limit reached.')
    identifier = service.store.hypothesis(key,clean(title,300),[],
        f'{origin} proposal; execution restricted to fixed {template} template. Not a forecast or causal conclusion.')
    with service.store.connect() as connection:
        connection.execute("INSERT OR IGNORE INTO research_plans(plan_key,hypothesis_id,created_at,template,metric,dimension,status) VALUES(?,?,?,?,?,?,'queued')",
                           (key,identifier,now(),template,metric,dimension))
        return connection.execute('SELECT id FROM research_plans WHERE plan_key=?',(key,)).fetchone()[0]


def seed(service):
    for metric in ('toast.net_sales','clover.gross_line_sales'):
        if service.store.rows("SELECT id FROM observations WHERE metric=? AND dimension='all' LIMIT 1",(metric,)):
            propose(service,{'template':'recent_weekday','metric':metric,'dimension':'all',
                'hypothesis':f'Using four recent matching weekdays may track changes in {metric} better than using eight.'},'Fixed baseline')
    if service.store.rows("SELECT id FROM observations WHERE metric='toast.net_sales' LIMIT 1"):
        for template,feature in CONTEXT_TEMPLATES.items():
            if service.store.rows('SELECT id FROM feature_vintages WHERE feature=? LIMIT 1',(feature,)):
                propose(service,{'template':template,'metric':'toast.net_sales','dimension':'all',
                    'hypothesis':f'{feature} may improve the weekday sales baseline when its value was known before forecast issue.'},'Fixed external-feature candidate')


def run_plans(service, deadline=None):
    """At most two attempts/day; unchanged data do not trigger repeated tests."""
    deadline = deadline or time.monotonic()+300
    day, started = datetime.now(ET).date().isoformat(), now()
    seed(service)
    results=[]
    plans=service.store.rows("SELECT * FROM research_plans ORDER BY CASE status WHEN 'queued' THEN 0 WHEN 'waiting_data' THEN 1 ELSE 2 END,id LIMIT 50")
    for plan in plans:
        if service.stopping.is_set() or time.monotonic() >= deadline:
            break
        rows=service.store.series(plan['metric'],plan['dimension'],as_of=started)
        payload={'mode':'experiment','template':plan['template'],'as_of_day':day,
                 'rows':[{k:r[k] for k in ('day','value','quality')} for r in rows if r['day']<day]}
        if plan['template'] in CONTEXT_TEMPLATES:
            payload['features']=numeric_vintages(service.store,CONTEXT_TEMPLATES[plan['template']],started)[-8000:]
        sha=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        # The date alone is not new evidence. Compare the actual numeric vintage.
        data_sha=hashlib.sha256(json.dumps({'rows':payload['rows'],'features':payload.get('features',[])},sort_keys=True).encode()).hexdigest()
        latest=service.store.rows('SELECT result FROM research_plan_runs WHERE plan_id=? ORDER BY id DESC LIMIT 1',(plan['id'],))
        unchanged={data_sha}
        if 'features' not in payload:
            # Existing numeric-only plans used this fingerprint before schema v5.
            unchanged.add(hashlib.sha256(json.dumps(payload['rows'],sort_keys=True).encode()).hexdigest())
        if latest and json.loads(latest[0]['result']).get('data_sha256') in unchanged:
            continue
        with service.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if connection.execute('SELECT COUNT(*) FROM research_plan_runs WHERE day=?',(day,)).fetchone()[0]>=2:
                break
            claim=connection.execute("INSERT OR IGNORE INTO research_plan_runs(plan_id,day,started_at,status,input_sha256,result) VALUES(?,?,?,'running',?,?)",
                                     (plan['id'],day,started,sha,json.dumps({'data_sha256':data_sha})))
            if not claim.rowcount:
                continue
            connection.execute("UPDATE research_plans SET status='running' WHERE id=?",(plan['id'],))
        try:
            result=service.worker.analyze(payload,deadline=deadline)
            output=service.workspace/'outputs'
            output.mkdir(exist_ok=True)
            (output/('input-'+sha+'.json')).write_text(json.dumps(payload,sort_keys=True),encoding='utf-8')
            result_sha=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
            (output/(result_sha+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
            status='complete' if result['available'] else 'waiting_data'
            key=f'plan:{day}:{plan["id"]}'
            with service.store.connect() as connection:
                connection.execute('INSERT OR IGNORE INTO experiments(created_at,kind,config,input_sha256,result,model_version,run_key,series_key) VALUES(?,?,?,?,?,?,?,?)',
                    (started,'hypothesis_backtest',json.dumps({k:plan[k] for k in ('template','metric','dimension')}),sha,json.dumps(result),result['model_version'],key,plan['plan_key']))
                experiment_id=connection.execute('SELECT id FROM experiments WHERE run_key=?',(key,)).fetchone()[0]
                detail={'data_sha256':data_sha,'paired_days':result['paired_days'],'baseline':result['baseline'],
                        'candidate':result['candidate'],'limitations':result['limitations']}
                connection.execute('UPDATE research_plan_runs SET status=?,experiment_id=?,result=? WHERE plan_id=? AND day=?',
                                   (status,experiment_id,json.dumps(detail),plan['id'],day))
                connection.execute('UPDATE research_plans SET status=? WHERE id=?',(status,plan['id']))
            base, candidate=result['baseline']['mae'],result['candidate']['mae']
            promising=result['available'] and base is not None and base>0 and candidate<=base*.95
            state='testing' if promising else 'unsupported' if result['available'] else 'proposed'
            note=f'{result["paired_days"]} paired rolling dates; baseline MAE {base}, candidate MAE {candidate}. '
            note+='Insufficient history; waiting for changed data.' if not result['available'] else 'Provisional improvement; forward validation required.' if promising else 'The candidate did not improve MAE by the predeclared 5% threshold.'
            note+=' Forecast model remains unchanged. No causal claim.'
            hypothesis=service.store.rows('SELECT title FROM hypotheses WHERE id=?',(plan['hypothesis_id'],))[0]
            service.store.hypothesis(plan['plan_key'],hypothesis['title'],[{'experiment_id':experiment_id,'input_sha256':sha}],note,state,'low' if result['available'] else 'exploratory')
            service.store.journal(day,'hypothesis_test',f'{plan["template"]} / {plan["metric"]} / {plan["dimension"]}: {note}',[experiment_id])
            results.append({'plan_id':plan['id'],'status':status,'experiment_id':experiment_id})
        except Exception as error:
            with service.store.connect() as connection:
                connection.execute("UPDATE research_plan_runs SET status='failed',result=? WHERE plan_id=? AND day=?",
                                   (json.dumps({'error':type(error).__name__,'data_sha256':data_sha}),plan['id'],day))
                connection.execute("UPDATE research_plans SET status='failed' WHERE id=?",(plan['id'],))
            raise
    service.export_journal(day)
    return {'attempted':len(results),'results':results,'daily_attempt_cap':2}
