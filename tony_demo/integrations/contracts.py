"""Explicit integration contracts, not claims of account access or vendor SDKs."""
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class Availability(str,Enum):
    SYNTHETIC='synthetic'
    NOT_CONNECTED='not_connected'
    PARTIAL='partial'
    VERIFIED='verified'


@dataclass(frozen=True)
class Observation:
    source:str
    metric:str
    unit:str
    period_start:str
    period_end:str
    value:float|None
    availability:Availability
    retrieved_at:str|None=None
    limitations:tuple[str,...]=()


class ReadOnlyProvider(Protocol):
    async def observations(self,start:str,end:str)->list[Observation]:...


def pending(source,metric,unit,start,end):
    return Observation(source,metric,unit,start,end,None,Availability.NOT_CONNECTED,
                       limitations=('Connector access/configuration pending; unavailable is not zero.',))
