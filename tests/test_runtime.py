"""Self-hosted runtime tests use invented data and disposable local state only."""
import copy
from datetime import datetime
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest

pytest.importorskip('msal',reason='Windows operational dependencies are installed separately from the portable demo.')
from app.storage import DB
from app.main import create_app
from app.operations import Operations,TOOLS
from app.booked_orders import booked_order_report
from app.booking_index import BookingIndex
from app.calculations import ET
from app.business_lookup import business_lookup
from app.corner.service import DEFAULT_CONFIG
from app.providers import ProviderError,R365Provider
from app.outlook import SCOPES
from app.restaurant_profile import profile
from app.code_proposals import allowed_path
from fastapi.testclient import TestClient


class MemoryVault:
    def __init__(self):self.data={}
    def read(self):return copy.deepcopy(self.data)
    def update(self,**kw):self.data.update(copy.deepcopy(kw))


@pytest.fixture
def client(tmp_path):
    vault=MemoryVault()
    app=create_app(tmp_path,vault,background=False)
    with TestClient(app) as c:
        c.headers.update({'origin':'http://127.0.0.1:8790','x-tony-request':'1'})
        yield c,vault


def signin(c,v):
    result=c.post('/api/login',json={'password':'synthetic-example-password','setup_code':v.read()['setup_code']})
    assert result.status_code==200
    c.headers['x-csrf-token']=result.json()['csrf']


def test_fresh_runtime_keeps_auth_csrf_host_and_no_vendor_access(client):
    c,v=client
    assert c.get('/api/overview').status_code==401
    assert c.get('/',headers={'host':'evil.example'}).status_code==400
    signin(c,v)
    assert c.get('/api/overview').status_code==200
    assert c.post('/api/backup',headers={'origin':'https://evil.example'}).status_code==403
    assert c.post('/api/backup',headers={'x-csrf-token':'wrong'}).status_code==403
    result=c.get('/api/status');assert result.status_code==200
    assert result.json()['runtime']['workers']=={}
    assert 'access_token' not in result.text and 'password_hash' not in result.text
    assert result.json()['r365'].startswith('Not connected')
    assert c.post('/api/manage/stop',headers={'x-csrf-token':'wrong'}).status_code==403


def test_live_mode_requires_each_restaurants_own_credentials(client):
    c,v=client;signin(c,v)
    assert c.put('/api/settings',json={'demo':False}).status_code==200
    result=c.get('/api/overview')
    assert result.status_code==503 and 'not configured' in result.text


def test_memory_and_briefings_are_local_and_repeat_safe(client):
    c,v=client;signin(c,v)
    assert c.post('/api/memory',json={'text':'Invented supplier follow-up','kind':'task'}).status_code==200
    assert len(c.get('/api/memory').json())==1
    first=c.post('/api/briefings').json();second=c.post('/api/briefings').json()
    assert first['id']==second['id'] and first['mode']=='demo' and first['ai']==0


def test_profile_has_no_restaurant_or_serving_assumptions_and_rejects_unsafe_input(tmp_path,monkeypatch):
    import app.restaurant_profile as module
    monkeypatch.setattr(module,'ROOT',tmp_path)
    assert profile()['pieces_per_portion']=={} and profile()['public_hosts']==[]
    (tmp_path/'data').mkdir()
    (tmp_path/'data/restaurant.json').write_text(json.dumps({'public_hosts':['https://not-a-host.example/']}))
    with pytest.raises(ValueError):profile()


@pytest.mark.asyncio
async def test_historical_lookup_keeps_gross_and_missing_days_explicit(tmp_path):
    db=DB(tmp_path)
    db.execute('INSERT INTO clover_days VALUES(?,?,?,?,?,?)',('2025-01-01',2,12500,0,0,'2026-01-01T00:00:00+00:00'))
    result=await business_lookup(db,None,None,'sales','2025-01-01','2025-01-02',now=datetime(2026,11,2,12,tzinfo=ET))
    assert result['sources'][0]['covered_total']==125
    assert result['sources'][0]['full_period_total'] is None
    assert result['sources'][1]['covered_total'] is None
    assert 'gross' in result['sources'][0]['unit']


def test_booking_reference_restart_move_cancel_and_no_customer_payload(tmp_path):
    db=DB(tmp_path);index=BookingIndex(db);now=datetime(2026,11,2,12,tzinfo=ET)
    row={'guid':'11111111-1111-1111-1111-111111111111','promisedDate':'2026-12-01T18:00:00-05:00',
         'approvalStatus':'FUTURE','customer':{'name':'DO_NOT_STORE'}}
    index.observe([row],4,now)
    assert len(BookingIndex(DB(tmp_path)).ids('2026-12-01'))==1
    assert 'DO_NOT_STORE' not in json.dumps(db.rows('SELECT * FROM booking_references_v1'))
    row['promisedDate']='2026-12-02T18:00:00-05:00';index.observe([row],4,now)
    assert not index.ids('2026-12-01') and len(index.ids('2026-12-02'))==1
    row['voided']=True;index.observe([row],4,now);assert not index.ids('2026-12-02')


def test_unconnected_modules_and_approval_boundaries_are_truthfully_present():
    assert SCOPES==['https://graph.microsoft.com/Mail.Read']
    assert hasattr(R365Provider,'invoices') and hasattr(R365Provider,'accounting_summaries')
    assert not DEFAULT_CONFIG['enabled'] and DEFAULT_CONFIG['model_calls_per_day']==0
    assert not allowed_path('app/restaurant_profile.py')
    names={t['name'] for t in TOOLS}
    assert {'get_business_lookup','get_email','get_monday_items','get_business_calendar'}<=names
    assert not {'send_email','execute_shell','approve_task'} & names


def test_all_runtime_python_sources_compile_and_no_business_catalog_is_bundled():
    import ast
    root=Path(__file__).resolve().parents[1]
    for p in (root/'app').rglob('*.py'):ast.parse(p.read_text(encoding='utf-8'))
    assert not (root/'app/toast_pizza_mapping.json').exists()
    assert not (root/'app/football_calendar.json').exists()


@pytest.mark.asyncio
async def test_private_business_lookup_has_no_model_action_authority(tmp_path):
    db=DB(tmp_path);ops=Operations(db,None,None)
    assert await ops.tool('send_email',{},'web')=={'error':'Tool not allowed'}
    assert await ops.tool('execute_shell',{},'web')=={'error':'Tool not allowed'}


@pytest.mark.asyncio
async def test_real_synthetic_order_arithmetic_uses_no_api_or_credentials():
    from app.providers import demo_overview
    result=demo_overview()
    assert result['mode']=='demo' and result['today']['orders']>0
