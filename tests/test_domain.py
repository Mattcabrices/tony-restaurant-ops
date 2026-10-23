from copy import deepcopy
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
import pytest
from tony_demo.domain.calculations import ET,bounds,business_day,check_metrics,percent,sales_summary
from tony_demo.domain.product_mix import product_mix
from tony_demo.domain.forecasting import baseline,forecast,backtest,errors
from tony_demo.fixtures import orders,GROUPS,DEMO_DAY,DEMO_NOW,daily_series
from tony_demo.summary import build_summary


def test_demo_is_explicitly_synthetic_and_reconciles_units():
    report=build_summary()
    assert report['provenance']['synthetic'] is True
    assert report['sales']['net_sales']==216 and report['sales']['orders']==4
    assert report['whole_pizzas']['total']==5
    wings=report['product_mix']['categories']['wings']
    assert wings['portion_orders']==4 and wings['total_pieces']==46
    assert report['product_mix']['categories']['fingers']['total_pieces']==8
    assert sum(report['whole_pizzas']['by_channel'].values())==5


def test_discount_refund_and_deferred_sales_definitions():
    check={'amount':100,'selections':[{'price':20,'deferred':True}],
           'appliedDiscounts':[{'discountAmount':7}],'payments':[{'refund':{'refundAmount':12,'tipRefundAmount':3}}]}
    assert check_metrics(check)==(Decimal('68'),Decimal('7'),Decimal('12'))
    assert percent(1,0) is None


def test_duplicates_voids_and_future_orders_do_not_inflate_sales():
    sample=orders();sample.append(deepcopy(sample[0]))
    sample.append({'guid':'demo-void','voided':True,'checks':[{'amount':999}]})
    sample.append({'guid':'demo-future','promisedDate':'2026-05-02T12:00:00-04:00','checks':[{'amount':999}]})
    report=sales_summary(sample,DEMO_DAY,DEMO_NOW)
    assert report['net_sales']==216 and report['orders']==4 and len(report['upcoming'])==1


def test_unknown_portion_size_is_not_zero_pieces():
    sample=orders();sample[0]['checks'][0]['selections'][1]['displayName']='Wings (size pending)'
    mix=product_mix(sample,GROUPS,DEMO_NOW)['categories']['wings']
    assert mix['total_pieces'] is None and mix['unknown_size_portions']==2 and mix['known_pieces']==26


def test_modifiers_are_not_counted_as_top_level_wings():
    sample=orders();sample[0]['checks'][0]['selections'][0]['modifiers']=[{'displayName':'100 Wings','quantity':10}]
    assert product_mix(sample,GROUPS,DEMO_NOW)['categories']['wings']['total_pieces']==46


@pytest.mark.parametrize('day,hours',[(date(2026,3,7),23),(date(2026,10,31),25)])
def test_overnight_business_dates_cover_real_dst_elapsed_hours(day,hours):
    a,b=bounds(day)
    assert (b.astimezone(timezone.utc)-a.astimezone(timezone.utc)).total_seconds()/3600==hours
    assert business_day(datetime(2026,4,30,3,59,tzinfo=ET))==date(2026,4,29)


def test_time_cutoffs_exclude_target_and_future_values():
    rows=daily_series();target='2026-05-01'
    before=forecast(rows,target,horizon_days=1)
    after=forecast(rows+[{'day':target,'value':999999,'quality':'complete'}],target,horizon_days=1)
    assert before==after
    folds=backtest(rows,horizon_days=1)['folds']
    assert folds and all(f['training_end']<f['issue_day']<f['day'] for f in folds)


def test_incomplete_data_and_zero_denominators_are_explicit():
    assert not baseline([{'day':'2026-04-24','value':5}],'2026-05-01')['available']
    assert errors([])['mae'] is None
    assert errors([{'predicted':1,'actual':0}])['wape'] is None
    assert errors([{'predicted':8,'actual':10}])['bias']==-2
