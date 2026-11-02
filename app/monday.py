"""Read-only access to one explicitly connected monday.com board."""
import re
from urllib.parse import urlsplit

import httpx

from .storage import utcnow
from .monday_export import parse_export


API = 'https://api.monday.com/v2'
API_VERSION = '2026-07'  # Explicit stable version; review before vendor retirement.
BOARD = '''query ($board: [ID!]) {
  boards(ids: $board) {
    id name url
    columns { id title type }
    items_page(limit: 100) {
      cursor
      items { id name url updated_at group { title } column_values { id text } }
    }
  }
}'''
NEXT = '''query ($cursor: String!) {
  next_items_page(cursor: $cursor, limit: 100) {
    cursor
    items { id name url updated_at group { title } column_values { id text } }
  }
}'''


class MondayError(Exception):
    pass


def board_id(url):
    parts = urlsplit(url.strip())
    host = (parts.hostname or '').lower()
    match = re.search(r'/boards/(\d+)(?:/|$)', parts.path)
    if parts.scheme != 'https' or not (host == 'monday.com' or host.endswith('.monday.com')) or parts.username or parts.password or parts.port or not match:
        raise MondayError('Use a monday.com board URL such as https://example.monday.com/boards/1234567890.')
    return match.group(1)


class Monday:
    def __init__(self, vault, db=None, http=None):
        self.vault = vault
        self.db = db
        self.http = http or httpx.AsyncClient(trust_env=False, timeout=25, follow_redirects=False)

    def config(self):
        return self.vault.read().get('monday', {})

    def status(self):
        cfg = self.config()
        snapshot = self.db.get('monday_snapshot') if self.db else None
        live = bool(cfg.get('token') and cfg.get('board_id'))
        return {'status': 'Connected — read-only' if live else 'Excel snapshot — read-only' if snapshot else 'Not connected',
                'configured': live or bool(snapshot), 'mode': 'api' if live else 'snapshot' if snapshot else 'none',
                'board_id': cfg.get('board_id'), 'board_name': cfg.get('board_name') or (snapshot or {}).get('board'),
                'board_url': cfg.get('board_url') or (snapshot or {}).get('board_url'),
                'items': len((snapshot or {}).get('items', [])), 'imported_at': (snapshot or {}).get('imported_at'),
                'read_only': True, 'api_ready': True, 'api_version': API_VERSION,
                'token_scope_note': 'Application sends fixed read queries only; a personal token may have broader account permissions.'}

    def import_export(self, data, url):
        if not self.db:
            raise MondayError('Local board snapshot storage is unavailable.')
        bid = board_id(url)
        host = urlsplit(url.strip()).hostname
        snapshot = parse_export(data, f'https://{host}/boards/{bid}')
        self.db.set('monday_snapshot', snapshot)
        return self.status()

    async def query(self, token, query, variables):
        if query not in (BOARD, NEXT):
            raise MondayError('Only fixed read-only board queries are allowed.')
        try:
            response = await self.http.post(API, headers={'Authorization': token, 'Content-Type': 'application/json', 'API-Version': API_VERSION},
                                            json={'query': query, 'variables': variables})
            if response.status_code in (401, 403):
                raise MondayError('monday.com denied access; check the token and board permissions.')
            if response.status_code != 200:
                raise MondayError(f'monday.com is unavailable (HTTP {response.status_code}).')
            data = response.json()
        except httpx.HTTPError:
            raise MondayError('monday.com network request failed.') from None
        except ValueError:
            raise MondayError('monday.com returned an unreadable response.') from None
        if not isinstance(data, dict) or not isinstance(data.get('data', {}), (dict, type(None))):
            raise MondayError('monday.com returned an unexpected response.')
        if data.get('errors'):
            raise MondayError('monday.com rejected the board query; check board access and token validity.')
        return data.get('data') or {}

    async def connect(self, url, token):
        bid = board_id(url)
        token = token.strip() if isinstance(token, str) else ''
        if not token or len(token) > 4096:
            raise MondayError('Enter a valid monday.com API token.')
        result = await self.query(token, BOARD, {'board': [bid]})
        boards = result.get('boards') or []
        if len(boards) != 1 or str(boards[0].get('id')) != bid:
            raise MondayError('The token cannot read this board. Check its URL and your monday.com access.')
        board = boards[0]
        self.vault.update(monday={'token': token, 'board_id': bid, 'board_name': board.get('name') or bid,
                                  'board_url': board.get('url') or url.strip()})
        return self.status()

    async def items(self, search=''):
        cfg = self.config()
        if not isinstance(search, str) or len(search) > 100:
            raise MondayError('Keep the board search under 100 characters.')
        if not cfg.get('token') or not cfg.get('board_id'):
            snapshot = self.db.get('monday_snapshot') if self.db else None
            if not snapshot:
                raise MondayError('Connect a monday.com board or import an Excel export in Settings first.')
            entries = snapshot['items']
            key = search.strip().casefold()
            if key in {'open', 'unresolved', 'active', 'outstanding'}:
                matches = [item for item in entries if item['group'].casefold() != 'completed task'
                           and item['fields'].get('Status', '').casefold() != 'done']
            elif key in {'done', 'completed', 'closed'}:
                matches = [item for item in entries if item['group'].casefold() == 'completed task'
                           or item['fields'].get('Status', '').casefold() == 'done']
            else:
                matches = [item for item in entries if not key or key in ' '.join(
                    str(part) for part in [item['name'], item['group'], *item['fields'].values()]).casefold()]
            ambiguous = sum(1 for item in entries if item['group'].casefold() == 'completed task'
                            and item['fields'].get('Status', '').casefold() != 'done')
            return {'source': snapshot['source'], 'board': snapshot['board'], 'board_url': snapshot['board_url'],
                    'snapshot_imported_at': snapshot['imported_at'], 'query': search,
                    'export_provenance': snapshot.get('export_provenance'),
                    'weekly_import': {'enabled': bool(self.db.get('weekly_sources_config') and self.db.get('weekly_sources_config').get('enabled')),
                                      'schedule': 'Saturday 04:00 America/New_York',
                                      'automatic_download': False, 'method': 'Owner exports Excel into the local weekly import folder.'},
                    'scanned': len(entries), 'matching': len(matches), 'items': matches[:30],
                    'status_counts': snapshot['status_counts'], 'group_counts': snapshot['group_counts'],
                    'limitations': ['Manual Excel snapshot; board changes after import are not included.',
                                    f'{ambiguous} items are in the Completed Task group but lack a Done status; their state is ambiguous.',
                                    'At most 30 matching items are returned; ask for a narrower search if needed.' if len(matches) > 30 else
                                    'All matching items are returned.']}
        data = await self.query(cfg['token'], BOARD, {'board': [cfg['board_id']]})
        boards = data.get('boards') or []
        if len(boards) != 1:
            raise MondayError('The connected monday.com board is unavailable.')
        board = boards[0]
        columns = {col['id']: col.get('title') or col['id'] for col in board.get('columns') or [] if col.get('id')}
        page = board.get('items_page') or {}
        entries = list(page.get('items') or [])
        cursor = page.get('cursor')
        pages = 1
        seen_cursors = set()
        while cursor and pages < 5:
            if cursor in seen_cursors:
                raise MondayError('monday.com repeated a page cursor; results withheld.')
            seen_cursors.add(cursor)
            next_data = await self.query(cfg['token'], NEXT, {'cursor': cursor})
            page = next_data.get('next_items_page') or {}
            entries.extend(page.get('items') or [])
            cursor = page.get('cursor')
            pages += 1
        normalized = []
        for item in entries:
            fields = {columns.get(value.get('id'), value.get('id')): value.get('text')
                      for value in item.get('column_values') or [] if value.get('id') and value.get('text')}
            row = {'id': item.get('id'), 'name': item.get('name'), 'url': item.get('url'),
                   'group': (item.get('group') or {}).get('title'), 'updated_at': item.get('updated_at'),
                   'fields': fields}
            key = search.strip().casefold()
            completed = (row['group'] or '').casefold() == 'completed task' or (fields.get('Status') or '').casefold() == 'done'
            matches = (not completed if key in {'open','unresolved','active','outstanding'} else
                       completed if key in {'done','completed','closed'} else
                       not key or key in ' '.join(str(part) for part in [row['name'],row['group'],*fields.values()]).casefold())
            if matches:
                normalized.append(row)
        return {'source': 'monday.com board', 'board': board.get('name') or cfg['board_name'],
                'board_url': cfg['board_url'], 'fresh_at': utcnow(), 'query': search, 'api_version': API_VERSION,
                'scanned': len(entries), 'matching': len(normalized), 'items': normalized[:30],
                'limitations': ['Read-only board data; item content is user-entered and may be stale.',
                                'At most 500 items are scanned; results may be incomplete.' if cursor else
                                'At most 30 matching items are returned.' if len(normalized) > 30 else
                                'All scanned matching items are returned.']}
