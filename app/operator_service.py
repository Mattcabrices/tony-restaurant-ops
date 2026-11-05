"""One bounded application service behind operator UI and conversational reads."""
import asyncio
from datetime import datetime, timedelta, timezone
import time
import uuid

from .calculations import business_day
from .operator_contracts import QUERY_ADAPTER, period_dates
from .operator_forecast import OperatorForecaster
from .order_search import search_orders
from .receipt_lookup import receipt_lookup
from .sales_breakdown import sales_breakdown
from .providers import ProviderError
from .storage import utcnow

SCHEMA_VERSION='tony.operator.v1'


class OperatorLedger:
    """Metadata only. No query text, receipts, amounts, names or model messages."""
    def __init__(self, db):
        self.db=db
        with db.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS operator_schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
            version=c.execute('SELECT COALESCE(MAX(version),0) FROM operator_schema_migrations').fetchone()[0]
            if version>1: raise RuntimeError('Operator schema is newer than this application.')
            c.execute('''CREATE TABLE IF NOT EXISTS operator_queries_v1(
                request_id TEXT PRIMARY KEY, kind TEXT NOT NULL, started_at TEXT NOT NULL,
                finished_at TEXT, status TEXT NOT NULL, elapsed_ms INTEGER, error_code TEXT)''')
            c.execute('CREATE INDEX IF NOT EXISTS operator_queries_time_v1 ON operator_queries_v1(started_at)')
            c.execute('INSERT OR IGNORE INTO operator_schema_migrations VALUES(1,?)',(utcnow(),))
            # In-flight work cannot survive a process restart; retain that fact.
            c.execute("UPDATE operator_queries_v1 SET status='interrupted',finished_at=?,error_code='process_restarted' WHERE status='running'",(utcnow(),))

    def start(self, identity, kind):
        self.db.execute('INSERT INTO operator_queries_v1(request_id,kind,started_at,status) VALUES(?,?,?,?)',
                        (identity,kind,utcnow(),'running'))

    def finish(self, identity, status, elapsed, code=None):
        with self.db.connect() as c:
            c.execute('UPDATE operator_queries_v1 SET finished_at=?,status=?,elapsed_ms=?,error_code=? WHERE request_id=?',
                      (utcnow(),status,elapsed,code,identity))
            c.execute('''DELETE FROM operator_queries_v1 WHERE request_id NOT IN
                         (SELECT request_id FROM operator_queries_v1 ORDER BY started_at DESC LIMIT 5000)
                         AND status!='running' ''')


def comparison(primary, previous):
    output=[]
    for current in primary['sources']:
        prior=next((s for s in previous['sources'] if s['source']==current['source'] and s['unit']==current['unit']),None)
        a=current['full_period_total']; b=prior['full_period_total'] if prior else None
        output.append({'source':current['source'],'unit':current['unit'], 'current':a,'previous':b,
                       'change':round(a-b,2) if a is not None and b is not None else None,
                       'percent_change':round(100*(a-b)/abs(b),2) if a is not None and b not in (None,0) else None,
                       'complete':a is not None and b is not None})
    return output


class OperatorService:
    def __init__(self, ops):
        self.ops=ops; self.ledger=OperatorLedger(ops.db); self.active=0
        self.forecaster=OperatorForecaster(ops.corner)

    def capabilities(self):
        cfg=self.ops.settings(); today=business_day(datetime.now(timezone.utc),cfg['closeout_hour'])
        try: configured=bool(self.ops.toast and self.ops.toast.config())
        except (ProviderError,AttributeError): configured=False
        return {'schema_version':SCHEMA_VERSION,'business_date':today.isoformat(),'timezone':'America/New_York',
                'workweek_start':cfg['workweek_start'] if cfg['workweek_start'] is not None else 2,
                'mode':'demo' if cfg['demo'] else 'live','toast_configured':configured,
                'queries':[
                    {'kind':'sales','description':'Source-separated sales, quantities and recorded labor with coverage and comparisons.'},
                    {'kind':'orders','description':'Find receipts by order/check/net-sales amount, with split-check totals.'},
                    {'kind':'receipt','description':'Find a receipt by order/check number plus date, or order UUID plus date.'},
                    {'kind':'channels','description':'Dine-in, regular/online takeout and delivery in a local-hour window.'},
                    {'kind':'forecast','description':'Restricted numerical weekday baseline with rolling backtests and empirical uncertainty.'}],
                'limits':{'parallel_requests':3,'request_seconds':120,'sales_dates':93,'receipt_dates':31,
                          'receipt_pages':60,'forecast_horizon_days':14,'paid_model_calls':0},
                'pending':['R365 accounting/inventory requires a real adapter and authorization.',
                           'Outlook requires the configured Microsoft delegated authorization.',
                           'Profit/COGS/complete labor cannot be inferred from sales alone.']}

    async def execute(self, payload):
        query=QUERY_ADAPTER.validate_python(payload)
        identity=uuid.uuid4().hex; started=time.monotonic()
        envelope={'schema_version':SCHEMA_VERSION,'request_id':identity,'kind':query.kind,'generated_at':utcnow(),
                  'status':'complete','error_code':None,'elapsed_ms':0,'data':{},'warnings':[]}
        self.ledger.start(identity,query.kind)
        if self.active>=3:
            envelope.update(status='busy',error_code='operator_busy',warnings=['Three operator queries are running. Retry shortly.'])
            self.ledger.finish(identity,'busy',0,'operator_busy');return envelope
        self.active+=1
        try:
            async with asyncio.timeout(120):
                cfg=self.ops.settings();today=business_day(datetime.now(timezone.utc),cfg['closeout_hour'])
                if cfg['demo']:
                    envelope.update(status='unavailable',error_code='live_mode_required',warnings=['Choose Live mode for actual restaurant queries. Existing demo screens remain available.'])
                else:
                    data=await self._query(query,today,cfg)
                    envelope['data']=data
                    if query.kind=='sales':
                        if not any(s['covered_total'] is not None or s.get('observed_total') is not None for s in data['primary']['sources']): envelope['status']='unavailable'
                        elif not any(s['complete'] for s in data['primary']['sources']): envelope['status']='partial'
                        if data.get('comparison') and not any(r['complete'] for r in data['comparison']):
                            envelope['warnings'].append('Comparison requires complete periods with the same source and metric definition.')
                    elif query.kind=='forecast' and not data['available']: envelope['status']='unavailable'
                    elif data.get('partial') or data.get('complete') is False: envelope['status']='partial'
        except asyncio.CancelledError:
            self.ledger.finish(identity,'interrupted',int((time.monotonic()-started)*1000),'request_cancelled');raise
        except (TimeoutError,ProviderError,ValueError,TypeError,KeyError,OSError) as error:
            code='request_timeout' if isinstance(error,TimeoutError) else 'invalid_query' if isinstance(error,ValueError) else 'source_unavailable'
            envelope.update(status='unavailable',error_code=code,warnings=[
                'Query timed out; no unverified result was returned.' if code=='request_timeout' else
                'Dates or filters are outside supported limits.' if code=='invalid_query' else
                'The requested source could not be read. Check integration status and retry; no data was invented.'])
        except Exception:
            envelope.update(status='unavailable',error_code='internal_error',warnings=[
                'The operator query could not complete. Use its reference to investigate; no unverified values were returned.'])
        finally:
            self.active-=1
        envelope['elapsed_ms']=int((time.monotonic()-started)*1000)
        self.ledger.finish(identity,envelope['status'],envelope['elapsed_ms'],envelope['error_code'])
        return envelope

    async def _query(self, query, today, cfg):
        if query.kind in ('sales','orders'):
            first,last=period_dates(query,today,cfg['workweek_start'] if cfg['workweek_start'] is not None else 2)
            if query.kind=='orders':
                return await search_orders(self.ops.toast,first.isoformat(),last.isoformat(),query.minimum,query.maximum,query.basis,cfg['closeout_hour'])
            primary=await self.ops.lookup(query.metric,first.isoformat(),last.isoformat(),query.dimension)
            result={'primary':primary,'comparison':None,'comparison_period':None}
            if query.compare!='none':
                offset=7 if query.compare=='previous_week' else (last-first).days+1
                previous=await self.ops.lookup(query.metric,(first-timedelta(days=offset)).isoformat(),(last-timedelta(days=offset)).isoformat(),query.dimension)
                result.update(comparison=comparison(primary,previous),comparison_period={'start':previous['start'],'end':previous['end']},previous=previous)
            return result
        if query.kind=='receipt': return await receipt_lookup(self.ops.toast,query.day,query.reference,cfg['closeout_hour'])
        if query.kind=='channels': return await sales_breakdown(self.ops.toast,query.day.isoformat(),query.start_hour,query.end_hour,cfg['closeout_hour'])
        return await self.forecaster.estimate(query,today,cfg['closeout_hour'])
