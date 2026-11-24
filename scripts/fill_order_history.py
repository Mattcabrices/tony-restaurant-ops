"""One-time, resumable, read-only fill of the local order cache. Safe to stop and rerun.

Usage: python -m scripts.fill_order_history START END   (YYYY-MM-DD dates)
"""
import asyncio
import sys
import time
from datetime import date, datetime, timedelta, timezone

from app.calculations import business_day
from app.clover import Clover, CloverError
from app.order_lookup import OrderCache, OrderLookup, toast_start
from app.providers import ProviderError, Toast
from app.storage import ROOT, Vault


async def main(start, end):
    vault = Vault(ROOT / 'data')
    lookup = OrderLookup(Toast(vault), Clover(vault), OrderCache(ROOT / 'data' / 'order_cache.sqlite3'))
    today, day, done, failed, began = business_day(datetime.now(timezone.utc), 4), end, 0, [], time.time()
    while day >= start:
        try:
            orders = await (lookup._toast_day(day, today) if day >= toast_start() else lookup._clover_day(day))
            done += 1
            if not orders:
                failed.append(f'{day} empty')
        except (ProviderError, CloverError, ValueError, KeyError, TypeError) as error:
            failed.append(f'{day} {str(error)[:80]}')
            if len(failed) > 60:
                print('Stopping: too many failures.', flush=True)
                break
        if done % 25 == 0:
            print(f'{day}: {done} days, {int(time.time() - began)}s', flush=True)
        day -= timedelta(days=1)
    print(f'Finished: {done} days. Not saved: {failed}', flush=True)


if __name__ == '__main__':
    asyncio.run(main(date.fromisoformat(sys.argv[1]), date.fromisoformat(sys.argv[2])))
