"""Same deterministic calculations for CLI, API and dashboard."""
from collections import defaultdict
from datetime import timedelta
from .fixtures import DEMO_DAY,DEMO_NOW,PROVENANCE,GROUPS,orders,daily_series
from .domain.calculations import sales_summary
from .domain.product_mix import product_mix
from .domain.forecasting import forecast,backtest

INTEGRATIONS=[
    {'name':'Toast','public_status':'Synthetic order adapter','production_status':'Read-only reporting implemented privately'},
    {'name':'Clover','public_status':'Contract and synthetic historical data','production_status':'Historical import and item analysis implemented privately'},
    {'name':'monday.com','public_status':'Optional read-only board checker; token required','production_status':'Read adapter ready; live activation pending token'},
    {'name':'Outlook','public_status':'Contract / coming soon','production_status':'Read-only adapter exists; authorization pending'},
    {'name':'Google Calendar','public_status':'Planned / coming soon','production_status':'Direct Calendar API not connected; private app currently reads a Google Sheets event calendar'},
    {'name':'Restaurant365','public_status':'Contract / coming soon','production_status':'Provider interfaces only; access pending'},
]


def build_summary():
    records=orders()
    sales=sales_summary(records,DEMO_DAY,DEMO_NOW)
    mix=product_mix(records,GROUPS,DEMO_NOW)
    pies=defaultdict(float)
    for order in records:
        for check in order['checks']:
            for item in check['selections']:
                if item['itemGroup']['guid']=='demo-pizzas':
                    pies[order['channel']]+=item['quantity']
    target=(DEMO_DAY+timedelta(days=1)).isoformat()
    return {'schema_version':'tony.portfolio.v1','provenance':PROVENANCE,
        'business_date':DEMO_DAY.isoformat(),'sales':sales,'product_mix':mix,
        'whole_pizzas':{'total':sum(pies.values()),'by_channel':dict(pies),
            'definition':'Synthetic top-level items explicitly mapped to Pizzas; excludes modifiers and packages.'},
        'forecast':{'target_day':target,**forecast(daily_series(),target,horizon_days=1)},
        'backtest':backtest(daily_series(),horizon_days=1)['metrics'],
        'integrations':INTEGRATIONS,
        'limitations':['All data is synthetic; forecast scores do not measure real restaurant performance.',
            'Clover gross line sales and Toast net sales are different definitions; never silently combine them.',
            'Orders, item portions, individual pieces and ingredient usage are different units.',
            'Report metrics use four example orders; forecast/backtest metrics use a separate fabricated daily history.',
            'This public demo has no preconfigured live accounts, production credentials, private serving builds or self-modifying execution.']}
