"""Deterministic, invented restaurant data. Never imported from a real business."""
from datetime import date,datetime,timedelta
from .domain.calculations import ET

DEMO_DAY=date(2026,4,30)
DEMO_NOW=datetime(2026,4,30,23,tzinfo=ET)
PROVENANCE={'source':'synthetic-fixtures-v1','synthetic':True,
            'description':'Invented order values, names and dates; not historical restaurant results.'}


def orders():
    def selection(key,name,quantity,group):
        return {'guid':key,'displayName':name,'quantity':quantity,'itemGroup':{'guid':group}}
    records=[
        ('demo-order-a','takeout',82,[selection('demo-pizza-a','Large Cheese',2,'demo-pizzas'),selection('demo-wing-a','10 Wings',2,'demo-wings')]),
        ('demo-order-b','delivery',64,[selection('demo-pizza-b','Large Pepperoni',2,'demo-pizzas'),selection('demo-wing-b','6 Wings',1,'demo-wings')]),
        ('demo-order-c','dine_in',45,[selection('demo-pizza-c','Medium Cheese',1,'demo-pizzas'),selection('demo-finger-c','4 Chicken Fingers',2,'demo-fingers')]),
        ('demo-order-d','takeout',25,[selection('demo-wing-d','20 Wings',1,'demo-wings')]),
    ]
    return [{'guid':key,'channel':channel,'openedDate':f'{DEMO_DAY}T18:00:00-04:00',
             'checks':[{'amount':amount,'selections':items}]} for key,channel,amount,items in records]


def daily_series():
    start=date(2025,12,31)
    weekday=[1100,1250,1400,1550,2300,2700,1900]
    return [{'day':(start+timedelta(days=i)).isoformat(),
             'value':weekday[(start+timedelta(days=i)).weekday()]+((i*37)%181)-90,
             'quality':'complete'} for i in range(120)]


GROUPS={'demo-pizzas':'Pizzas','demo-wings':'Wings','demo-fingers':'Fingers'}
