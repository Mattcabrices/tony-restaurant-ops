import pytest
pytest.importorskip('msal',reason='Operational Windows dependencies required.')
from datetime import date, datetime, timezone
from decimal import Decimal
import pytest
from app.calculations import ET, bounds, business_day, change, check_metrics, labor_summary, percent, sales_summary
from app.operations import differences
from app.providers import demo_overview


def test_net_sales_exclusions_no_double_count():
    check = {'amount': 100, 'selections': [{'price': 20, 'deferred': True}, {'price': 10, 'selectionType': 'HOUSE_ACCOUNT_PAY_BALANCE'}, {'price': 30, 'voided': True}],
             'appliedServiceCharges': [{'chargeAmount': 5, 'serviceChargeCategory': 'FUNDRAISING_CAMPAIGN'}],
             'appliedDiscounts': [{'discountAmount': 7}], 'payments': [{'refund': {'refundAmount': 12, 'tipRefundAmount': 3}}]}
    assert check_metrics(check) == (Decimal('53'), Decimal('7'), Decimal('12'))
    assert check_metrics({'amount': 100, 'voided': True})[0] == 0


def test_zero_denominators():
    assert percent(1, 0) is None
    assert change(100, 0) is None
    assert percent(12.345, 100) == 12.35
    assert change(120, 100) == 20


def test_business_date_cutoff():
    assert business_day(datetime(2026, 10, 13, 3, 59, tzinfo=ET)) == date(2026, 10, 12)
    assert business_day(datetime(2026, 10, 13, 4, 0, tzinfo=ET)) == date(2026, 10, 13)


@pytest.mark.parametrize('day,hours', [(date(2026,3,7),23), (date(2026,10,31),25)])
def test_dst_business_day(day, hours):
    a,b = bounds(day)
    assert (b.astimezone(timezone.utc)-a.astimezone(timezone.utc)).total_seconds()/3600 == hours


def test_order_dedupe_future_voided_and_midnight():
    now = datetime(2026,10,13,18,tzinfo=ET)
    orders = [{'guid':'1','openedDate':'2026-10-13T00:30:00-04:00','checks':[{'amount':10}]},
              {'guid':'1','checks':[{'amount':10}]}, {'guid':'2','voided':True,'checks':[{'amount':99}]},
              {'guid':'3','promisedDate':'2026-10-14T12:00:00-04:00','checks':[{'amount':300}]}]
    r = sales_summary(orders, date(2026,10,12), now)
    assert r['net_sales'] == 10 and r['orders'] == 1 and r['voids'] == 1
    assert r['hourly'][20] == 10
    assert len(r['upcoming']) == 1


def test_labor_breaks_unknown_wages_and_overtime():
    now = datetime(2026,10,13,18,tzinfo=ET)
    entries = [{'employeeReference':{'guid':'x'},'inDate':'2026-10-13T14:00:00-04:00','hourlyWage':20,
                'breaks':[{'paid':False,'inDate':'2026-10-13T15:00:00-04:00','outDate':'2026-10-13T15:30:00-04:00'}]}]
    r = labor_summary(entries, [], now, 1000)
    assert r['hours'] == 3.5 and r['cost'] == 70 and r['percentage'] == 7 and r['estimated']
    entries[0]['hourlyWage'] = None
    assert labor_summary(entries, [], now, 1000)['cost'] is None
    entries = [{'outDate':'2026-10-13T18:00:00-04:00','regularHours':7,'overtimeHours':1,'hourlyWage':20}]
    assert labor_summary(entries, [], now, 1000)['cost'] is None
    assert labor_summary(entries, [], now, 1000, 1.5)['cost'] == 170


def test_open_labor_dst_elapsed_real_hours():
    r = labor_summary([{'inDate':'2026-11-01T00:00:00-04:00','hourlyWage':20}], [], datetime(2026,11,1,3,tzinfo=ET), 1000)
    assert r['hours'] == 4


def test_change_detection():
    a,b = demo_overview(),demo_overview()
    assert differences(a,b) == []
    b['today']['net_sales'] += 100
    assert '100.00' in differences(a,b)[0]
    b['business_date'] = '2099-01-01'
    assert 'Business day changed' in differences(a,b)[0]
    assert not any('Net sales changed' in s for s in differences(a,b))


def test_missing_amount_never_becomes_zero():
    with pytest.raises(ValueError,match='no amount'):
        check_metrics({'payments':[]})


def test_capability_loss_is_meaningful():
    a,b=demo_overview(),demo_overview()
    b['stock']=None
    assert any('Stock became unavailable' in x for x in differences(a,b))
