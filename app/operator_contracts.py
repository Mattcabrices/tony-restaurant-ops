"""Versioned, validated operator requests shared by HTTP and AI read tools."""
from datetime import date, timedelta
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from .calculations import reporting_week_start

PERIODS = ('today', 'yesterday', 'this_week', 'last_week', 'last_14_days', 'custom')


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Period(StrictModel):
    period: Literal['today', 'yesterday', 'this_week', 'last_week', 'last_14_days', 'custom'] = 'yesterday'
    start: date | None = None
    end: date | None = None

    @model_validator(mode='after')
    def dates(self):
        if self.period == 'custom':
            if not self.start or not self.end or self.start > self.end or (self.end-self.start).days > 92:
                raise ValueError('Custom periods require 1–93 inclusive business dates.')
        elif self.start is not None or self.end is not None:
            raise ValueError('Explicit dates require period=custom.')
        return self


class SalesQuery(Period):
    kind: Literal['sales']
    metric: Literal['sales', 'orders', 'whole_pizzas', 'wing_orders', 'wing_pieces', 'finger_orders', 'finger_pieces', 'labor_hours'] = 'sales'
    dimension: Literal['', 'Lrg', 'Med', 'Mini', 'GF'] = ''
    compare: Literal['none', 'previous_period', 'previous_week'] = 'none'

    @model_validator(mode='after')
    def dimension_valid(self):
        if self.dimension and self.metric != 'whole_pizzas': raise ValueError('Size applies only to whole pizzas.')
        return self


class OrdersQuery(Period):
    kind: Literal['orders']
    minimum: float = Field(default=0, ge=0, le=10000000)
    maximum: float = Field(default=10000000, ge=0, le=10000000)
    basis: Literal['order_total', 'check_total', 'net_sales'] = 'order_total'

    @model_validator(mode='after')
    def amounts(self):
        if self.minimum > self.maximum: raise ValueError('Minimum must not exceed maximum.')
        if self.start and self.end and (self.end-self.start).days > 30: raise ValueError('Receipt scans allow at most 31 dates.')
        return self


class ReceiptQuery(StrictModel):
    kind: Literal['receipt']
    day: date
    reference: str = Field(pattern=r'^(?:[0-9]{1,12}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})$')


class ChannelsQuery(StrictModel):
    kind: Literal['channels']
    day: date
    start_hour: int = Field(default=0, ge=0, le=23, strict=True)
    end_hour: int = Field(default=24, ge=1, le=24, strict=True)

    @model_validator(mode='after')
    def window(self):
        if self.start_hour >= self.end_hour: raise ValueError('Start hour must precede exclusive end hour.')
        return self


class ForecastQuery(StrictModel):
    kind: Literal['forecast']
    target: date
    metric: Literal['sales', 'evening_sales', 'orders', 'wing_orders', 'wing_pieces', 'finger_orders', 'finger_pieces', 'labor_hours'] = 'sales'


OperatorQuery = Annotated[Union[SalesQuery, OrdersQuery, ReceiptQuery, ChannelsQuery, ForecastQuery], Field(discriminator='kind')]
QUERY_ADAPTER = TypeAdapter(OperatorQuery)


def period_dates(query, today, weekday=2):
    if query.period == 'custom': first, last = query.start, query.end
    elif query.period == 'today': first = last = today
    elif query.period == 'yesterday': first = last = today-timedelta(days=1)
    elif query.period == 'this_week': first, last = reporting_week_start(today, weekday), today
    elif query.period == 'last_week':
        last = reporting_week_start(today, weekday)-timedelta(days=1); first = last-timedelta(days=6)
    else: first, last = today-timedelta(days=14), today-timedelta(days=1)
    if last > today: raise ValueError('Actuals cannot include future dates. Use bookings or forecasts.')
    return first, last
