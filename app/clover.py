"""Narrow, read-only access to Clover's North American Platform API."""
import asyncio
import re
from datetime import datetime, timedelta

import httpx


class CloverError(Exception):
    pass


class Clover:
    def __init__(self, vault, http=None):
        self.vault = vault
        self.http = http or httpx.AsyncClient(trust_env=False, timeout=20, follow_redirects=False)

    def status(self):
        saved = self.vault.read().get('clover', {})
        return {'configured': bool(saved.get('token') and saved.get('merchant_id')),
                'merchant_id': saved.get('merchant_id') if saved.get('token') else None}

    async def verify(self, merchant_id, token):
        """Validate order-read access before keeping a supplied credential."""
        try:
            response = await self.http.get(
                f'https://api.clover.com/v3/merchants/{merchant_id}/orders',
                params={'limit': 1},
                headers={'Authorization': f'Bearer {token}',
                         'Accept': 'application/json', 'User-Agent': 'TonyRestaurantOps/1.0'})
        except httpx.HTTPError:
            raise CloverError('Clover could not be reached. Nothing was saved.') from None
        if response.status_code in (401, 403):
            raise CloverError('Clover rejected this token or it lacks Read Orders permission. Nothing was saved.')
        if response.status_code != 200:
            raise CloverError(f'Clover order check failed (HTTP {response.status_code}). Nothing was saved.')
        try:
            data = response.json()
        except ValueError:
            raise CloverError('Clover returned an unreadable response. Nothing was saved.') from None
        if not isinstance(data, dict) or not isinstance(data.get('elements'), list):
            raise CloverError('Clover returned an unexpected order response. Nothing was saved.')
        return True

    async def orders_between(self, start: datetime, end: datetime, expand=''):
        """Return a complete, bounded historical window by client-created time."""
        if not start.tzinfo or not end.tzinfo or end <= start or end-start > timedelta(days=89):
            raise CloverError('Historical order windows must be 1–89 days with timezones.')
        saved = self.vault.read().get('clover', {})
        merchant_id, token = saved.get('merchant_id'), saved.get('token')
        if not merchant_id or not token:
            raise CloverError('Clover is not connected.')
        path = f'https://api.clover.com/v3/merchants/{merchant_id}/orders'
        filters = [('filter', f'clientCreatedTime>={int(start.timestamp()*1000)}'),
                   ('filter', f'clientCreatedTime<{int(end.timestamp()*1000)}')]
        headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json',
                   'User-Agent': 'TonyRestaurantOps/1.0'}
        collected, seen = [], set()
        limit = 500
        for offset in range(0, 50000, limit):
            params = filters + [('limit', str(limit)), ('offset', str(offset))]
            if expand:
                params.append(('expand', expand))
            for attempt in range(4):
                try:
                    response = await self.http.get(path, params=params, headers=headers)
                except httpx.HTTPError:
                    if attempt == 3:
                        raise CloverError('Clover historical orders request failed.') from None
                    await asyncio.sleep(2 ** attempt)
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt == 3:
                        raise CloverError(f'Clover historical orders unavailable (HTTP {response.status_code}).')
                    await asyncio.sleep(2 ** attempt)
                    continue
                if response.status_code != 200:
                    raise CloverError(f'Clover historical orders unavailable (HTTP {response.status_code}).')
                try:
                    data = response.json()
                except ValueError:
                    raise CloverError('Clover returned unreadable historical orders.') from None
                batch = data.get('elements') if isinstance(data, dict) else None
                if not isinstance(batch, list):
                    raise CloverError('Clover returned an unexpected historical order page.')
                break
            for order in batch:
                if not isinstance(order, dict) or not order.get('id'):
                    raise CloverError('Clover returned an order without an identifier.')
                if order['id'] in seen:
                    raise CloverError('Clover order pagination repeated an order; import withheld.')
                seen.add(order['id'])
                collected.append(order)
            if len(batch) < limit:
                return collected
            await asyncio.sleep(0.15)
        raise CloverError('Clover historical order pagination exceeded the safety limit.')

    async def line_items(self, order_id):
        if not isinstance(order_id, str) or not re.fullmatch(r'[A-Za-z0-9]{8,32}', order_id):
            raise CloverError('Clover order identifier is invalid.')
        saved = self.vault.read().get('clover', {})
        merchant_id, token = saved.get('merchant_id'), saved.get('token')
        if not merchant_id or not token:
            raise CloverError('Clover is not connected.')
        url = f'https://api.clover.com/v3/merchants/{merchant_id}/orders/{order_id}/line_items'
        headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json',
                   'User-Agent': 'TonyRestaurantOps/1.0'}
        result = []
        for offset in range(0, 10000, 500):
            try:
                response = await self.http.get(url, params={'limit': 500, 'offset': offset}, headers=headers)
            except httpx.HTTPError:
                raise CloverError('Clover line items could not be retrieved; period withheld.') from None
            if response.status_code != 200:
                raise CloverError(f'Clover line items unavailable (HTTP {response.status_code}); period withheld.')
            try:
                batch = response.json().get('elements')
            except ValueError:
                raise CloverError('Clover returned unreadable line items; period withheld.') from None
            if not isinstance(batch, list):
                raise CloverError('Clover returned unexpected line items; period withheld.')
            result.extend(batch)
            if len(batch) < 500:
                return result
        raise CloverError('Clover line-item pagination exceeded the safety limit.')
