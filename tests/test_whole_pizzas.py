import pytest
pytest.importorskip('msal',reason='Operational Windows dependencies required.')
from copy import deepcopy
from datetime import date,datetime,timedelta
import json
import pytest

from app.calculations import ET
from app.whole_pizzas import whole_pizza_summary,whole_pizza_query
from app.corner.item_quality import save_whole_pizzas,latest_counts,whole_pizza_quality
from app.corner.service import CornerService
from app.item_leaderboard import item_leaderboard
from app.providers import Toast,ProviderError

DAY=date(2026,10,17)
LATER=datetime(2026,10,19,12,tzinfo=ET)
MAPPING={'version':'test-v1','verified_at':'2026-10-19T12:00:00+00:00',
    'items':{'large':{'name':'Large Cheese','size':'Lrg'},'mini':{'name':'Mini Cheese','size':'Mini'}},
    'groups':{'lg':'Lrg','mn':'Mini'}}


def selection(name='Large Cheese',identity='large',quantity=2,group='lg',**kwargs):
    return {'guid':identity+'-selection','displayName':name,'item':{'guid':identity},'itemGroup':{'guid':group},'quantity':quantity,**kwargs}


def orders(items):return [{'guid':'order','businessDate':20261017,'checks':[{'selections':items}]}]


def summarize(items):return whole_pizza_summary(orders(items),DAY,LATER,4,mapping=MAPPING,group_names={'salad':'Salads'})


def test_identity_mapping_excludes_packages_slices_and_modifiers():
    result=summarize([selection(),selection('Mini Cheese','mini',3,'mn'),
        selection('Classic Pizza Package','package',10,'packages',modifiers=[selection(quantity=99)]),
        selection('Pizza Slice','slice',50,'slices'),selection('Large Calzone','calzone',9,'calzones'),
        selection('Large Salad','salad',20,'salad'),selection('Extra Pizza Cheese','topping',20,'mods',selectionType='MODIFIER')])
    assert result['quality']=='complete' and result['quantity']==5
    assert result['by_size']=={'Lrg':2,'Med':0,'Mini':3,'GF':0}
    assert whole_pizza_query('large pizzas')=='Lrg'
    assert whole_pizza_query('GF pizzas')=='GF'
    assert whole_pizza_query('cheese pizza') is None
    assert whole_pizza_query('pizza packages') is None


@pytest.mark.parametrize('extra,issue',[
    (selection('Large New Pizza','new',1,'unknown'),'unmapped_pizza_selections'),
    (selection(splitOrigin={'guid':'original'}),'split_pizza_selections'),
    (selection(quantity=float('inf')),'invalid_pizza_quantity'),
    (selection(quantity=.5),'invalid_pizza_quantity'),
    (selection('Mini Cheese','large',1,'mn'),'mapped_identity_name_conflict')])
def test_ambiguous_counts_withheld(extra,issue):
    result=summarize([extra])
    assert result['quantity'] is None and result['quality']=='partial' and result['issues'][issue]


def test_duplicate_future_and_empty_days():
    result=summarize([selection(),selection()])
    assert result['quantity'] is None and result['issues']['duplicate_pizza_selections']==1
    future=whole_pizza_summary(orders([selection()]),DAY,datetime(2026,10,17,12,tzinfo=ET),4,mapping=MAPPING)
    assert future['quantity'] is None and future['issues']['business_day_not_closed']
    empty=whole_pizza_summary([],DAY,LATER,4,mapping=MAPPING)
    assert empty['quantity'] is None and empty['issues']['unconfirmed_empty_day']
    wrong=orders([selection()]);wrong[0]['businessDate']=20261018
    assert whole_pizza_summary(wrong,DAY,LATER,4,mapping=MAPPING)['issues']['unexpected_business_date']


def test_quality_revisions_do_not_fall_back_to_old_complete_counts(db,tmp_path):
    service=CornerService(tmp_path/'corner')
    good=summarize([selection()]);save_whole_pizzas(service.store,good);save_whole_pizzas(service.store,good)
    assert len(latest_counts(service.store))==1
    assert len(service.store.rows('SELECT * FROM item_count_snapshots'))==1
    result=item_leaderboard(db,'large pizza',corner=service,today=date(2026,10,19))
    assert result['sources'][1]['rankings'][0]['count']==2
    bad=summarize([selection(splitOrigin={'guid':'x'})]);save_whole_pizzas(service.store,bad)
    result=item_leaderboard(db,'pizza',corner=service,today=date(2026,10,19))
    assert result['sources'][1]['rankings']==[] and result['sources'][1]['flagged_days_excluded']==1
    quality=whole_pizza_quality(service.store,'2026-10-16','2026-10-18')
    assert quality['missing_count_days']==2 and quality['partial_count_days']==1
    assert quality['partial_details'][0]['issues']['split_pizza_selections']==1
    assert quality['complete_count_days']==0
    # The separate record cannot silently enter forecasting as a numeric series.
    assert service.store.rows('SELECT * FROM observations')==[]


@pytest.mark.asyncio
async def test_provider_uses_same_whole_pizza_definition(vault,monkeypatch):
    import app.whole_pizzas as module
    monkeypatch.setattr(module,'load_mapping',lambda:(MAPPING,'abc'))
    toast=Toast(vault)
    toast.config=lambda:{'restaurant_guid':'restaurant'}
    async def optional(*args,**kwargs):return {'general':{'closeoutHour':4}}
    async def get(*args,**kwargs):return {'menus':[{'guid':'lg','name':'Large Pizzas'}]}
    async def get_orders(*args):return orders([selection(),selection('Classic Pizza Package','package',20,'packages')])
    toast.optional=optional;toast.get=get;toast.orders=get_orders
    try:
        result=await toast.item_sales(DAY,'large pizzas')
        assert result['matched_quantity']==2 and result['exact']
        assert result['mapping_version']=='test-v1'
        def missing_mapping():raise FileNotFoundError('Mapping absent')
        monkeypatch.setattr(module,'load_mapping',missing_mapping)
        with pytest.raises(ProviderError,match='incomplete'):
            await toast.item_sales(DAY,'large pizzas')
    finally:await toast.http.aclose()
