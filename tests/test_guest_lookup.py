import pytest
pytest.importorskip('msal',reason='Operational Windows dependencies required.')
import json
import base64
from datetime import date, datetime, timedelta, timezone

import pytest

from app.guest_lookup import GuestLookup, HELP, is_guest_lookup, parse, render
from app.operations import Operations
from app.providers import ProviderError
from app.telegram import Telegram
from app.code_proposals import allowed_path
from app.calculations import business_day


NOW = datetime(2026, 10, 24, 16, tzinfo=timezone.utc)


def order(oid='o1', gid='guest1', first='Jane', email='Jane@example.com', phone='+1 (404) 555-0123'):
    return {'guid': oid, 'checks': [{'guid': 'c'+oid, 'customer': {
        'guid': gid, 'firstName': first, 'lastName': 'Smith', 'email': email, 'phone': phone,
        'address': 'NEVER DISPLAY', 'creditCard': 'NEVER DISPLAY'}}]}


class Toast:
    def __init__(self, rows=None, fail=False):
        self.rows = [order()] if rows is None else rows
        self.calls = []
        self.fail = fail

    async def get(self, path, params, ttl):
        assert path == '/orders/v2/ordersBulk' and ttl == 120
        assert set(params) == {'businessDate', 'page', 'pageSize'}  # No PII in URLs.
        self.calls.append(params)
        if self.fail:
            raise ProviderError('Unsafe upstream detail must not escape')
        return self.rows


@pytest.mark.parametrize('text,kind,query', [
    ('/guest name Jane Smith','name','jane smith'),
    ('Could you look up a guest by email Jane@Example.com?', 'email','jane@example.com'),
    ('Find customer by phone +1 (404) 555-0123', 'phone','4045550123'),
    ('/guest Jane Smith', 'name','jane smith'),
    ('/guest jane@example.com', 'email','jane@example.com'),
    ('/guest 404.555.0123', 'phone','4045550123')])
def test_parse(text,kind,query):
    assert is_guest_lookup(text)
    assert parse(text,NOW.date()) == (kind,query,date(2026,10,18),date(2026,10,24))


@pytest.mark.parametrize('text', ['/guest', '/guest phone 0123', '/guest name Al',
    '/guest email broken', '/guest Jane | 32', '/guest Jane | 2026-10-24 | 2026-10-25',
    '/guest Jane | 2026-08-01 | 2026-10-01', '/guest Jane\nSmith', '/guest Jane | 0'])
def test_bad_queries_fail_closed(text):
    with pytest.raises(ValueError):
        parse(text,NOW.date())


def test_commands_and_ordinary_questions_not_intercepted():
    assert not is_guest_lookup('/code add a tool to find guests')
    assert not is_guest_lookup('What is our average guest count?')
    assert not is_guest_lookup('Check my inbox')
    assert not allowed_path('app/guest_lookup.py')


@pytest.mark.parametrize('text,kind,query,start,end', [
    ('find all orders from Casey yesterday','name','casey','2026-10-23','2026-10-23'),
    ('Can you show me orders for Jane Smith today?','name','jane smith','2026-10-24','2026-10-24'),
    ('Find orders placed by jane@example.com last week','email','jane@example.com','2026-10-14','2026-10-20'),
    ('list orders from 4045550123 this week','phone','4045550123','2026-10-21','2026-10-24'),
    ('Show all orders under the name Casey on 2026-10-01','name','casey','2026-10-01','2026-10-01'),
    ('Get orders for Casey from 2026-10-01 through 2026-10-03','name','casey','2026-10-01','2026-10-03'),
    ('Find orders from Casey last 3 days please','name','casey','2026-10-22','2026-10-24'),
    ('Find guest named Casey yesterday','name','casey','2026-10-23','2026-10-23'),
])
def test_natural_guest_order_parsing(text,kind,query,start,end):
    assert is_guest_lookup(text)
    assert parse(text,NOW.date()) == (kind,query,date.fromisoformat(start),date.fromisoformat(end))


@pytest.mark.parametrize('text',[
    'Please find any orders for Sample Guest on October 25, 2026.',
    'Please find any orders for Sample Guest on Sunday, 10/25.',
    'Find matching orders from Sample Guest on Oct. 25th, 2026.',
    'Find some orders placed by Sample Guest on 10/25/26.',
    "That is odd, there is no guest with the name Sample Guest? They ordered on Sunday, 10/25.",
    'Lookup a customer named Sample Guest on October 25, 2026.',
    'Why did you not do that? I want to fix the code. Please find any orders for Sample Guest on October 25, 2026.',
])
def test_recent_conversation_wording_and_human_dates(text):
    today=date(2026,10,26)
    assert is_guest_lookup(text)
    assert parse(text,today)==('name','sample guest',date(2026,10,25),date(2026,10,25))


@pytest.mark.parametrize('text',[
    'Find any orders for Sample Guest on Sunday, 10/26/2026.',
    'Find orders for Sample Guest on October 32, 2026.',
    'Find orders for Sample Guest on November 25.',
    'Find orders for Sample Guest on 2/29/2026.',
])
def test_human_date_errors_ask_instead_of_searching_guest_name_as_date(text):
    assert is_guest_lookup(text)
    with pytest.raises(ValueError):parse(text,date(2026,10,26))


@pytest.mark.asyncio
async def test_any_orders_human_date_never_reaches_model_mail_or_chat_history(db):
    db.set('settings',{'demo':False,'web_search_enabled':True})
    toast=Toast([order(first='Sample')])
    ops=Operations(db,toast,None,outlook=object())
    result=await ops.chat('Please find any orders for Sample on October 25, 2026.')
    assert result['sensitive'] and not result['ai'] and 'Order #' in result['text']
    assert all(c['businessDate']=='20261025' for c in toast.calls)
    assert db.history('web')==[] and db.get('web_search_last_usage') is None


@pytest.mark.parametrize('text', ['find all orders from Casey tomorrow',
    'find orders from Casey next Friday', 'find orders from Casey last 32 days',
    'find orders from Casey on 2026-02-30', 'find orders from Casey yesterday | 7'])
def test_unsupported_natural_dates_never_silently_search_default_week(text):
    assert is_guest_lookup(text)
    with pytest.raises(ValueError):parse(text,NOW.date())


@pytest.mark.parametrize('text',['Show all orders for yesterday','Find orders from DoorDash yesterday',
    'show orders from Clover last week','/code find orders from Casey yesterday',
    'Show any orders for October 25, 2026',
    'Were there any orders for yesterday?',
    'Were there any orders from DoorDash on Friday and what were they?'])
def test_non_guest_order_questions_keep_existing_router(text):
    assert not is_guest_lookup(text)


@pytest.mark.parametrize('text',[
    'What did Sample Guest order last Friday?',
    'Did Sample Guest place any orders on Friday?',
    "Show me Sample Guest's orders from Friday.",
    'Pull up any orders for Sample Guest on Friday.',
    'Fetch orders under Sample Guest on Friday.',
    'Hey Tony, were there any orders for Sample Guest on Friday and what were they?',
    'Are there any orders for Sample Guest on Friday?',
    'Have there been any orders from Sample Guest on Friday and show me the items?',
])
def test_more_conversational_guest_forms(text):
    assert is_guest_lookup(text)
    assert parse(text,date(2026,10,26))==('name','sample guest',date(2026,10,23),date(2026,10,23))


@pytest.mark.parametrize('phrase',['last Friday on the 23rd','Friday the 23rd','Friday, 23rd','on Friday the 23rd'])
def test_weekday_with_redundant_ordinal(phrase):
    text='Find orders for Sample Guest '+phrase
    assert parse(text,date(2026,10,27))==('name','sample guest',date(2026,10,23),date(2026,10,23))


@pytest.mark.parametrize('phrase',['last Friday on the 24th','Friday the 32nd','next Friday on the 30th'])
def test_redundant_ordinal_must_agree_and_not_guess_next_week(phrase):
    with pytest.raises(ValueError):parse('Find orders for Sample Guest '+phrase,date(2026,10,27))


@pytest.mark.asyncio
async def test_existential_order_question_uses_private_route(db):
    db.set('settings',{'demo':False,'web_search_enabled':True})
    ops=Operations(db,Toast(),None,outlook=object())
    result=await ops.chat('Hey Tony, were there any orders for Jane Smith on Friday and what were they?')
    assert result['sensitive'] and result['ai'] is False
    assert db.history('web')==[]
    assert ops.dialogue.get('web')['orders'] is True


@pytest.mark.asyncio
async def test_natural_order_output_is_matching_checks_only_and_private(db):
    casey = order('o1','casey1','Casey')
    casey.update(displayNumber='42', openedDate='2026-10-23T19:00:00Z')
    casey['checks'][0].update(totalAmount=25.50, selections=[{'displayName':'Large Pizza','modifiers':[{'displayName':'HIDDEN MODIFIER'}]}])
    other = order('o2','other','Other')['checks'][0]
    other.update(totalAmount=100, selections=[{'displayName':'OTHER GUEST ITEM'}])
    casey['checks'].extend([casey['checks'][0],other])  # Repeated check must not double the amount.
    second = order('o3','casey2','Casey')
    toast=Toast([casey,casey,second])
    db.set('settings',{'demo':False,'web_search_enabled':True})
    ops=Operations(db,toast,None,outlook=object())
    reply=await ops.chat('find all orders from Casey yesterday')
    text=reply['text']
    assert reply['sensitive'] and not reply['ai'] and 'Order #42' in text
    assert 'Matching orders shown: 2' in text and 'Large Pizza' in text and '$25.5' in text
    assert 'OTHER GUEST' not in text and 'HIDDEN MODIFIER' not in text
    # A timestamp may contain the area-code digits; test actual contact values
    # and contact labels instead of mistaking arbitrary numbers for disclosure.
    assert 'jane@example.com' not in text.lower() and '+1 (404) 555-0123' not in text
    assert 'Email:' not in text and 'Phone:' not in text
    assert 'not proof of identity' in text and db.history('web')==[]
    yesterday = business_day(datetime.now(timezone.utc),4)-timedelta(days=1)
    assert all(c['businessDate']==yesterday.strftime('%Y%m%d') for c in toast.calls)


@pytest.mark.asyncio
async def test_order_list_cap_and_missing_totals_are_explicit():
    toast=Toast([order(str(i),'same','Casey') for i in range(51)])
    result=await GuestLookup(toast).search('find all orders from Casey yesterday',now=NOW)
    assert len(result['orders'])==50 and result['orders_truncated']
    assert result['orders'][0]['matching_check_total'] is None
    assert 'not every matching order' in render(result) and 'Check 1 total: unavailable' in render(result)


@pytest.mark.asyncio
async def test_natural_yesterday_before_closeout_and_permission_guard():
    toast=Toast()
    result=await GuestLookup(toast).search('find all orders from Casey yesterday',
        now=datetime(2026,10,25,6,tzinfo=timezone.utc))
    assert result['start']==result['end']=='2026-10-23'
    assert toast.calls[0]['businessDate']=='20261023'
    toast=Toast()
    token=base64.urlsafe_b64encode(json.dumps({'scope':'orders:read'}).encode()).decode().rstrip('=')
    toast.token='header.'+token+'.signature'
    result=await GuestLookup(toast).search('find all orders from Casey yesterday',now=NOW)
    assert result['blocked'] and not result['orders']
    assert 'does not include guest.pi:read' in render(result) and 'Matching orders shown' not in render(result)


@pytest.mark.asyncio
@pytest.mark.parametrize('query',['name jane smith','email jane@example.com','phone 4045550123'])
async def test_matches_dedup_and_projection(query):
    toast = Toast([order(),order(),order('o2'),order('o3','guest2'),order('o4','guest3','Janet')])
    # Email and phone can be shared; never merge separate guest IDs.
    result = await GuestLookup(toast).search('/guest '+query+' | 1',now=NOW)
    assert len(result['matches']) == (2 if query.startswith('name') else 3)
    assert result['matches'][0]['orders'] == 2
    assert result['complete_dates'] == ['2026-10-24'] and not result['partial']
    assert 'NEVER DISPLAY' not in json.dumps(result)
    assert 'Jane Smith' in render(result)


@pytest.mark.asyncio
async def test_scope_missing_null_empty_and_failure_are_distinct():
    no_scope = await GuestLookup(Toast([{'guid':'o','checks':[{}]}])).search('/guest Jane | 1',now=NOW)
    assert 'guest.pi:read' in render(no_scope)
    absent = await GuestLookup(Toast([{'guid':'o','checks':[{'customer':None}]}])).search('/guest Jane | 1',now=NOW)
    assert 'guest.pi:read' not in render(absent) and 'availability/permissions' in render(absent)
    failed = await GuestLookup(Toast(fail=True)).search('/guest Jane | 1',now=NOW)
    assert failed['partial'] and 'Unsafe upstream' not in render(failed)
    assert 'does not mean the guest is absent' in render(failed)


@pytest.mark.asyncio
async def test_explicit_missing_guest_scope_stops_without_claiming_not_found():
    toast = Toast()
    payload = base64.urlsafe_b64encode(json.dumps({'scope':'orders:read menus:read'}).encode()).decode().rstrip('=')
    toast.token = 'header.'+payload+'.signature'
    result = await GuestLookup(toast).search('/guest Jane | 31',now=NOW)
    assert result['blocked'] and len(toast.calls)==1
    assert 'guest.pi:read' in render(result) and 'No matching guest' not in render(result)
    assert not result['matches']


@pytest.mark.asyncio
async def test_guest_reference_without_any_contact_values_is_not_searchable():
    result = await GuestLookup(Toast([{'guid':'o','checks':[{'customer':{'guid':'g'}}]}])).search('/guest Jane | 1',now=NOW)
    assert result['populated_customers']==1 and result['searchable_customers']==0
    assert 'availability/permissions' in render(result)


@pytest.mark.asyncio
async def test_request_cap_and_result_cap_explicit():
    toast = Toast([order(str(i),str(i)) for i in range(100)])
    result = await GuestLookup(toast).search('/guest Jane | 31',now=NOW)
    assert len(toast.calls) == 60 and result['partial'] and result['truncated']
    assert len(result['matches']) == 10 and not result['complete_dates']


@pytest.mark.asyncio
async def test_cooldown_and_closeout():
    lookup = GuestLookup(Toast())
    result = await lookup.search('/guest Jane | 1',now=datetime(2026,10,24,6,tzinfo=timezone.utc))
    assert result['end'] == '2026-10-23'  # 2 AM Eastern belongs to previous business day.
    with pytest.raises(ValueError,match='retry'):
        await lookup.search('/guest Jane',now=NOW)


@pytest.mark.asyncio
async def test_chat_guest_query_never_calls_model_mail_web_or_persists(db):
    db.set('settings', {'demo':False,'web_search_enabled':True})
    ops = Operations(db,Toast(),None,outlook=object())
    result = await ops.chat('Find guest by email jane@example.com')
    assert result['sensitive'] and result['ai'] is False and 'Jane Smith' in result['text']
    assert db.history('web') == [] and db.get('ai_last_tool') is None
    assert db.get('web_search_last_usage') is None
    db.set('settings',{'demo':True})
    assert 'Live Toast mode' in (await ops.chat('/guest Jane'))['text']


@pytest.mark.asyncio
async def test_telegram_private_only_and_no_guest_reply_cache(db):
    ops = Operations(db,Toast(),None)
    db.set('settings', {'demo':False})
    bot = object.__new__(Telegram)
    bot.db,bot.ops = db,ops
    bot.config = lambda: {'user_id':'1','chat_id':'2'}
    sent=[]
    async def send(text,key=None): sent.append(text)
    bot.send=send
    msg={'update_id':41,'message':{'text':'/guest Jane | 1','from':{'id':1},'chat':{'id':2,'type':'private'}}}
    await bot.handle(msg)
    await bot.handle(msg)
    assert len(sent)==1 and 'Jane Smith' in sent[0]
    assert db.get('telegram_reply:41') == {'sensitive_guest_lookup':True}
    assert db.history('telegram') == []
    msg['update_id']=42;msg['message']['from']['id']=3
    await bot.handle(msg)
    assert len(sent)==1
