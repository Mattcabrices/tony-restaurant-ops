import pytest
pytest.importorskip('msal',reason='Operational Windows dependencies required.')
from copy import deepcopy
from datetime import date,datetime
import json
import pytest

from app.calculations import ET
from app.pizza_splits import reconcile_pizza_splits
from app.whole_pizzas import whole_pizza_summary
from app.corner.service import CornerService
from app.corner.item_quality import save_whole_pizzas,latest_counts
from app.providers import Toast

MAPPING={'version':'test','groups':{'lg':'Lrg','mini':'Mini'},'items':{'pizza':{'name':'Large Cheese','size':'Lrg'}}}
DAY=date(2026,9,30);NOW=datetime(2026,10,2,12,tzinfo=ET)


def order(quantities=(.5,.5),amounts=(12.5,12.5)):
    checks=[]
    for i,(quantity,amount) in enumerate(zip(quantities,amounts)):
        checks.append({'guid':'check'+str(i),'splitOrigin':{'guid':'check0'} if i else None,
            'selections':[{'guid':'line'+str(i),'displayName':'Large Cheese','item':{'guid':'pizza'},
              'itemGroup':{'guid':'lg'},'quantity':quantity,'receiptLinePrice':25,'preDiscountPrice':amount,
              'splitOrigin':{'guid':'line0'} if i else None}]})
    return {'guid':'order','businessDate':20260930,'checks':checks}


def summary(value):return whole_pizza_summary([value],DAY,NOW,4,mapping=MAPPING)


def test_proven_halves_reconcile_without_source_mutation():
    value=order();original=deepcopy(value)
    result=summary(value)
    assert result['quantity']==1 and result['by_size']['Lrg']==1
    assert result['issues']=={} and result['reconciliation']=={'groups':1,'source_lines':2,'whole_pizzas':1}
    assert value==original
    # A separate identical pizza has a different selection ID and is not merged.
    extra=deepcopy(value['checks'][0]['selections'][0]);extra.update(guid='separate',quantity=1,preDiscountPrice=25)
    value['checks'][0]['selections'].append(extra)
    assert summary(value)['quantity']==2


@pytest.mark.parametrize('change',[
    'missing_origin','voided_child','duplicate_selection','check_cycle','missing_check_lineage',
    'selection_cycle','different_item','different_name','different_group','price_mismatch','missing_price',
    'incomplete_fraction','negative_quantity','nonfinite_quantity'])
def test_unproven_groups_stay_flagged(change):
    value=order();root=value['checks'][0]['selections'][0];child=value['checks'][1]['selections'][0]
    if change=='missing_origin':child['splitOrigin']={'guid':'absent'}
    elif change=='voided_child':child['voided']=True
    elif change=='duplicate_selection':child['guid']=root['guid']
    elif change=='check_cycle':value['checks'][1]['splitOrigin']={'guid':'check1'}
    elif change=='missing_check_lineage':value['checks'][1]['splitOrigin']=None
    elif change=='selection_cycle':root['splitOrigin']={'guid':'line1'}
    elif change=='different_item':child['item']={'guid':'other'}
    elif change=='different_name':child['displayName']='Large Pepperoni'
    elif change=='different_group':child['itemGroup']={'guid':'mini'}
    elif change=='price_mismatch':child['preDiscountPrice']=10
    elif change=='missing_price':child.pop('receiptLinePrice')
    elif change=='incomplete_fraction':child['quantity']=.25;child['preDiscountPrice']=6.25
    elif change=='negative_quantity':child['quantity']=-.5
    elif change=='nonfinite_quantity':child['quantity']=float('nan')
    result=summary(value)
    assert result['quantity'] is None and result['quality']=='partial'
    assert result['reconciliation']['groups']==0


def test_thirds_nested_links_and_multiple_pizzas():
    value=order((1/3,1/3,1/3),(8.33,8.33,8.34))
    value['checks'][2]['splitOrigin']={'guid':'check1'}
    value['checks'][2]['selections'][0]['splitOrigin']={'guid':'line1'}
    assert summary(value)['quantity']==1
    assert summary(order((1,1),(25,25)))['quantity']==2


def test_no_cross_order_or_name_only_reconciliation():
    value=order();value['checks'][1]['selections'][0]['splitOrigin']=None
    assert summary(value)['quantity'] is None
    first=order();second=deepcopy(first)
    first['checks']=first['checks'][:1];second['guid']='other-order';second['checks']=second['checks'][1:]
    result=whole_pizza_summary([first,second],DAY,NOW,4,mapping=MAPPING)
    assert result['quantity'] is None


def test_split_provenance_is_saved_and_revises_partial_snapshot(tmp_path):
    s=CornerService(tmp_path)
    partial=summary(order((.5,.25),(12.5,6.25)));save_whole_pizzas(s.store,partial)
    full=summary(order());save_whole_pizzas(s.store,full)
    row=latest_counts(s.store)[0]
    assert row['quantity']==1 and row['quality']=='complete'
    assert row['calculation_version']=='whole-pizza-v2-linked-splits'
    assert json.loads(row['reconciliation'])['groups']==1
    assert len(s.store.rows('SELECT * FROM item_count_snapshots'))==2


@pytest.mark.asyncio
async def test_flavor_search_uses_identical_reconciliation(vault,monkeypatch):
    import app.providers as providers
    monkeypatch.setattr(providers,'load_mapping',lambda:(MAPPING,'abc'))
    toast=Toast(vault);toast.config=lambda:{'restaurant_guid':'restaurant'}
    async def optional(*args,**kwargs):return {'general':{'closeoutHour':4}}
    async def get(*args,**kwargs):return {'menus':[{'guid':'lg','name':'Large Pizzas'}]}
    async def get_orders(*args):return [order()]
    toast.optional=optional;toast.get=get;toast.orders=get_orders
    try:
        result=await toast.item_sales(DAY,'cheese pizza')
        assert result['exact'] and result['matched_quantity']==1
        assert result['pizza_split_reconciliation']['groups']==1
    finally:await toast.http.aclose()
