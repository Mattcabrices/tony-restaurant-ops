import asyncio
from datetime import date, datetime, timedelta, timezone
import json
from types import SimpleNamespace
import pytest
pytest.importorskip('msal',reason='Operational Windows dependencies required.')
from pydantic import ValidationError
from fastapi.testclient import TestClient

from app.operator_contracts import QUERY_ADAPTER, period_dates
from app.operator_service import OperatorService, OperatorLedger, comparison
from app.operator_forecast import OperatorForecaster
from app.corner.engine import analyze
from app.corner.store import CornerStore
from app.main import create_app
from app.operations import Operations
from app.storage import utcnow
from app.code_proposals import allowed_path


def period(value): return QUERY_ADAPTER.validate_python({'kind':'sales','period':value})


def test_dates_use_configured_business_week_and_reject_ignored_filters():
    today=date(2026,11,4)
    assert period_dates(period('last_week'),today)==(date(2026,10,28),date(2026,11,3))
    assert period_dates(period('this_week'),today)==(today,today)
    assert period_dates(period('last_14_days'),today)==(date(2026,10,21),date(2026,11,3))
    assert period_dates(period('last_week'),today,0)==(date(2026,10,26),date(2026,11,1))
    for payload in [dict(kind='sales',period='yesterday',start='2026-11-01'),
                    dict(kind='sales',period='custom',start='2026-11-03',end='2026-11-01'),
                    dict(kind='orders',minimum=900,maximum=500),dict(kind='orders',minimum=float('nan')),
                    dict(kind='receipt',day='2026-11-03',reference='../secrets'),
                    dict(kind='forecast',target='2026-11-05',employee='PRIVATE'),
                    dict(kind='channels',day='2026-11-03',start_hour=21,end_hour=16)]:
        with pytest.raises(ValidationError):QUERY_ADAPTER.validate_python(payload)
    for path in ('app/operator_contracts.py','app/operator_service.py','app/operator_forecast.py','app/operator_api.py','app/receipt_lookup.py'):
        assert not allowed_path(path)


def source(total, unit='USD net sales', name='Toast'):
    return {'source':name,'unit':unit,'full_period_total':total,'covered_total':total,'complete':total is not None}


def test_comparison_never_mixes_sources_metrics_partial_periods_or_zero_denominator():
    a={'sources':[source(200),source(300,'USD approximate gross line sales','Clover imported history')]}
    b={'sources':[source(100),source(100,'USD net sales','Clover imported history')]}
    rows=comparison(a,b)
    assert rows[0]['change']==100 and rows[0]['percent_change']==100
    assert rows[1]['change'] is None and not rows[1]['complete']
    assert comparison({'sources':[source(None)]},b)[0]['change'] is None
    assert comparison({'sources':[source(100)]},{'sources':[source(0)]})[0]['percent_change'] is None


def test_additive_metadata_schema_restart_and_retention_do_not_store_query_content(db):
    ledger=OperatorLedger(db);ledger.start('one','sales')
    OperatorLedger(db)
    row=db.rows('SELECT * FROM operator_queries_v1')[0]
    assert row['status']=='interrupted' and row['error_code']=='process_restarted'
    ledger.start('two','receipt');ledger.finish('two','complete',10)
    assert db.rows('SELECT COUNT(*) n FROM operator_schema_migrations')[0]['n']==1
    assert db.rows("SELECT name FROM sqlite_master WHERE name='memory'")
    assert set(db.rows('SELECT * FROM operator_queries_v1')[0])=={
        'request_id','kind','started_at','finished_at','status','elapsed_ms','error_code'}


@pytest.mark.asyncio
async def test_service_shared_sales_contract_and_bounded_concurrency(db):
    db.set('settings',{'demo':False})
    ops=Operations(db,None,None);calls=[]
    async def lookup(metric,start,end,dimension):
        calls.append((metric,start,end,dimension))
        return {'start':start,'end':end,'sources':[source(200 if len(calls)==1 else 100)]}
    ops.lookup=lookup
    r=await ops.operator.execute({'kind':'sales','period':'custom','start':'2026-10-28','end':'2026-11-03','compare':'previous_week'})
    assert r['status']=='complete' and r['data']['comparison'][0]['change']==100
    assert calls[1][1:3]==('2026-10-21','2026-10-27')
    ops.operator.active=3
    assert (await ops.operator.execute({'kind':'sales'}))['status']=='busy'
    assert ops.operator.active==3


@pytest.mark.asyncio
async def test_errors_and_demo_never_disclose_secrets_or_claim_success(db):
    ops=Operations(db,None,None)
    assert (await ops.operator.execute({'kind':'sales'}))['error_code']=='live_mode_required'
    db.set('settings',{'demo':False})
    async def broken(*args):raise RuntimeError('TOKEN_SECRET_PRIVATE')
    ops.lookup=broken
    r=await ops.operator.execute({'kind':'sales'})
    assert r['error_code']=='internal_error' and 'TOKEN_SECRET_PRIVATE' not in json.dumps(r)
    assert ops.operator.active==0
    assert not db.rows("SELECT * FROM operator_queries_v1 WHERE status='running'")


@pytest.mark.asyncio
async def test_receipt_number_ambiguity_and_no_customer_fields(db):
    db.set('settings',{'demo':False})
    class Toast:
        async def get(self,path,params=None,ttl=0):
            assert path=='/orders/v2/ordersBulk'
            return [{'guid':str(i),'displayNumber':str(i+10),'checks':[{'guid':'c'+str(i),
                'displayNumber':'101','amount':500,'totalAmount':550,'tabName':'PRIVATE_GUEST',
                'customer':{'email':'private@example.test'}}]} for i in range(2)]
    ops=Operations(db,Toast(),None)
    r=await ops.operator.execute({'kind':'receipt','day':'2026-11-03','reference':'101'})
    assert r['status']=='complete' and r['data']['ambiguous'] and len(r['data']['matches'])==2
    assert 'PRIVATE_GUEST' not in json.dumps(r) and 'private@example.test' not in json.dumps(r)


@pytest.mark.asyncio
async def test_on_demand_forecast_is_horizon_safe_isolated_and_reports_error(tmp_path):
    today=date(2026,11,4);store=CornerStore(tmp_path/'research')
    rows=[{'day':(today-timedelta(days=140-i)).isoformat(),'metric':'toast.net_sales','dimension':'all',
           'value':1000+20*((today-timedelta(days=140-i)).weekday()),'quality':'complete'} for i in range(140)]
    rows.append({'day':today.isoformat(),'metric':'toast.net_sales','dimension':'all','value':999999,'quality':'complete'})
    store.ingest('Toast',rows,{'closeout_hour':4})
    class Worker:
        def __init__(self):self.calls=0
        def status(self):return {'ready':True}
        def analyze(self,payload,deadline=None):
            self.calls+=1;assert deadline and all(r['day']<today.isoformat() for r in payload['rows'])
            return analyze(payload)
    worker=Worker();service=OperatorForecaster(SimpleNamespace(store=store,worker=worker,lock=asyncio.Lock()))
    query=QUERY_ADAPTER.validate_python({'kind':'forecast','target':'2026-11-06'})
    r=await service.estimate(query,today,4)
    assert r['available'] and r['backtest']['n']>=14 and r['low']<=r['predicted']<=r['high']
    assert max(r['training_dates'])<'2026-11-04' and r['predicted']<999999
    assert r['horizon_days']==2 and r['backtest']['wape'] is not None
    await service.estimate(query,today,4);assert worker.calls==1
    worker.status=lambda:{'ready':False}
    r=await OperatorForecaster(SimpleNamespace(store=store,worker=worker,lock=asyncio.Lock())).estimate(query,today,4)
    assert not r['available'] and 'unrestricted' in r['reason']


def test_operator_http_contract_keeps_auth_csrf_host_and_validation(tmp_path,vault,monkeypatch):
    from app.code_proposals import CodeProposals
    monkeypatch.setattr('app.telegram.CodeProposals',lambda db,**kwargs:CodeProposals(db,root=tmp_path,**kwargs))
    app=create_app(tmp_path,vault,background=False)
    with TestClient(app) as c:
        c.headers.update({'origin':'http://127.0.0.1:8790','x-tony-request':'1'})
        assert c.get('/api/operator/capabilities').status_code==401
        r=c.post('/api/login',json={'password':'a-secure-test-password','setup_code':vault.read()['setup_code']})
        c.headers['x-csrf-token']=r.json()['csrf']
        assert c.get('/api/operator/capabilities').json()['schema_version']=='tony.operator.v1'
        assert 'oneOf' in c.get('/api/operator/schema').json()['request']
        assert c.post('/api/operator/query',json={'kind':'sales'},headers={'x-csrf-token':'bad'}).status_code==403
        assert c.post('/api/operator/query',json={'kind':'sales','password':'x'}).status_code==422
        assert c.post('/api/operator/query',json={'kind':'receipt','day':'2026-11-03','reference':'../x'}).status_code==422
        r=c.post('/api/operator/query',json={'kind':'sales'})
        assert r.status_code==200 and r.json()['status']=='unavailable'
        assert 'no-store' in r.headers['cache-control']
        history=c.get('/api/operator/queries').json()['queries']
        assert history and history[0]['kind']=='sales' and 'data' not in history[0]


@pytest.mark.asyncio
async def test_running_query_cancellation_is_recorded_and_slot_released(db):
    db.set('settings',{'demo':False})
    ops=Operations(db,None,None);entered=asyncio.Event()
    async def lookup(*args): entered.set();await asyncio.Event().wait()
    ops.lookup=lookup
    task=asyncio.create_task(ops.operator.execute({'kind':'sales'}))
    await entered.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert ops.operator.active==0
    assert db.rows('SELECT status,error_code FROM operator_queries_v1')[0]=={
        'status':'interrupted','error_code':'request_cancelled'}


@pytest.mark.asyncio
async def test_current_day_observation_is_visible_but_never_a_complete_total(db):
    from app.business_lookup import business_lookup
    from app.calculations import ET
    now=datetime(2026,11,4,18,tzinfo=ET)
    class Toast:
        async def get(self,*args,**kwargs):return [{'guid':'invented','checks':[{'amount':100}]}]
    r=await business_lookup(db,Toast(),None,'sales','2026-11-04','2026-11-04',now=now)
    toast=next(s for s in r['sources'] if s['source']=='Toast')
    assert toast['observed_total']==100 and toast['full_period_total'] is None and not toast['complete']


@pytest.mark.asyncio
async def test_rollback_switch_preserves_legacy_tools_and_avoids_new_schema(db,monkeypatch):
    monkeypatch.setenv('TONY_OPERATOR_DISABLED','1')
    ops=Operations(db,None,None)
    assert ops.operator is None
    assert not db.rows("SELECT name FROM sqlite_master WHERE name='operator_queries_v1'")
    assert (await ops.tool('get_operator_capabilities',{},'web'))['status']=='unavailable'
    assert await ops.tool('get_memory',{},'web')==[]


def test_followups_retain_amount_filters_but_not_failed_query_claims():
    from app.dialogue import Dialogue
    d=Dialogue()
    d.remember_business('web','get_orders_by_amount',{'minimum':500,'maximum':900.25,'basis':'order_total'},{'partial':False})
    assert d.business_hint('What about last week?','web')['arguments']['maximum']==900.25
    d.clear('web')
    d.remember_business('web','get_operator_sales',{'metric':'sales'},{'status':'unavailable'})
    assert d.business_hint('What about last week?','web') is None
