"""Parse a bounded monday.com Excel export into a local board snapshot."""
import io
import re
import zipfile
from collections import Counter
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

from .sheet_calendar import MAIN, REL, PKG_REL, _cells
from .storage import utcnow


CELL = re.compile(r'^([A-Z]+)(\d+)$')


def _sheets(data):
    if len(data) > 8_000_000:
        raise ValueError('Board export exceeds the 8 MB file limit.')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(part.file_size for part in archive.infolist()) > 40_000_000:
            raise ValueError('Board export expands beyond the size limit.')
        book = ET.fromstring(archive.read('xl/workbook.xml'))
        rels = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        targets = {rel.attrib['Id']: rel.attrib['Target'].lstrip('/') for rel in rels.iter(PKG_REL + 'Relationship')}
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            root = ET.fromstring(archive.read('xl/sharedStrings.xml'))
            strings = [''.join(t.text or '' for t in entry.iter(MAIN + 't')) for entry in root.iter(MAIN + 'si')]
        result = {}
        for sheet in book.iter(MAIN + 'sheet'):
            target = targets[sheet.attrib[REL + 'id']]
            path = target if target.startswith('xl/') else 'xl/' + target
            values = _cells(ET.fromstring(archive.read(path)), strings)
            if len(values) > 300_000:
                raise ValueError('Board export has too many cells.')
            rows = {}
            for address, value in values.items():
                match = CELL.fullmatch(address)
                if match:
                    rows.setdefault(int(match.group(2)), {})[match.group(1)] = value
            result[sheet.attrib['name']] = rows
        return result


def _date(raw):
    if not raw:
        return ''
    try:
        return (datetime(1899, 12, 30) + timedelta(days=float(raw))).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return str(raw)[:40]


def parse_export(data, board_url):
    sheets = _sheets(data)
    board_rows = next((rows for name, rows in sheets.items() if name.casefold() != 'updates'), None)
    if not board_rows:
        raise ValueError('No board items sheet was found in the Excel export.')
    board_name = str(board_rows.get(1, {}).get('A') or 'monday.com board')[:120]
    group = ''
    items = []
    headers = {}
    for number, row in sorted(board_rows.items()):
        if number == 1:
            continue
        if row.get('A') == 'Name' and any('Item ID' in str(value) for value in row.values()):
            headers = row
            continue
        if len(row) == 1 and row.get('A'):
            group = str(row['A'])[:120]
            continue
        id_column = next((col for col, title in headers.items() if 'Item ID' in str(title)), None)
        item_id = str(row.get(id_column) or '') if id_column else ''
        if not item_id.isdigit() or not row.get('A'):
            continue
        fields = {}
        for column, title in headers.items():
            if column in ('A', id_column) or not title or not row.get(column):
                continue
            value = _date(row[column]) if 'date' in str(title).casefold() else str(row[column])[:1000]
            fields[str(title)[:80]] = value
        items.append({'id': item_id, 'name': str(row['A'])[:300],
                      'url': board_url.rstrip('/') + '/pulses/' + item_id,
                      'group': group, 'fields': fields})
    if not items:
        raise ValueError('No monday.com item IDs and names were found in the export.')
    if len(items) > 20_000:
        raise ValueError('Board export has too many items.')
    updates = {}
    for name, rows in sheets.items():
        if name.casefold() != 'updates':
            continue
        for number, row in rows.items():
            if number < 3:
                continue
            item_id = str(row.get('A') or '')
            content = str(row.get('G') or '').strip()
            if item_id.isdigit() and content:
                updates.setdefault(item_id, []).append({'author': str(row.get('E') or '')[:100],
                                                         'created_at': str(row.get('F') or '')[:100],
                                                         'text': content[:700]})
    for item in items:
        if item['id'] in updates:
            item['recent_updates'] = updates[item['id']][-2:]
    statuses = Counter(item['fields'].get('Status') or '(blank)' for item in items)
    groups = Counter(item['group'] or '(ungrouped)' for item in items)
    return {'source': 'monday.com Excel export (manual snapshot)', 'board': board_name,
            'board_url': board_url, 'imported_at': utcnow(), 'items': items,
            'status_counts': dict(statuses), 'group_counts': dict(groups),
            'updates_count': sum(len(value) for value in updates.values())}
