"""Hash-approved fixed task runner. No generated code, shell, installs or deployments."""
import asyncio
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
from .storage import ROOT
import re
import secrets
from uuid import UUID

from .calculations import ET, business_day
from .corner.collect import RequestBudget
from .corner.item_quality import latest_counts
from .corner.store import now
from .item_sales import menu_names
from .providers import Toast, ProviderError
from .public_reader import HOSTS, read_page, validate_url
from .whole_pizzas import load_mapping, whole_pizza_summary

SKILLS = {'coverage_review': 'Review stored data coverage and pizza-quality flags',
          'pizza_audit': 'Read-only Toast pizza diagnostics for 1–3 exact dates',
          'public_page': 'Read one approved public HTTPS page as text'}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def safe_guid(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError):
        return None


def revision(skill):
    if skill not in SKILLS:
        raise ValueError('Unknown fixed task skill.')
    base = Path(__file__).parent
    data = (base/'task_skills'/f'{skill}.md').read_bytes()+Path(__file__).read_bytes()
    if skill == 'public_page':
        data += (base/'public_reader.py').read_bytes()
    elif skill == 'pizza_audit':
        for filename in ('whole_pizzas.py', 'pizza_splits.py', 'providers.py'):
            data += (base/filename).read_bytes()
        mapping=ROOT/'data'/'toast_pizza_mapping.json'
        data += mapping.read_bytes() if mapping.is_file() else b'owner-mapping-not-configured'
    return hashlib.sha256(data).hexdigest()


def arguments(skill, args):
    if not isinstance(args, dict):
        raise ValueError('Task arguments must be an object.')
    if skill == 'coverage_review' and args == {}:
        return {}
    if skill == 'public_page' and set(args) == {'url'}:
        return {'url': validate_url(args['url'])}
    if skill == 'pizza_audit' and set(args) == {'dates'} and isinstance(args['dates'], list) and 1 <= len(args['dates']) <= 3:
        dates = sorted(set(args['dates']))
        for day in dates:
            parsed = date.fromisoformat(day)
            if not date(2026, 7, 20) <= parsed < business_day(datetime.now(ET), 4):
                raise ValueError('Choose closed Toast dates on or after July 20, 2026.')
        return {'dates': dates}
    raise ValueError('Arguments do not match an allowed task skill.')


class TaskBudget(RequestBudget):
    def __init__(self, store, task_id):
        super().__init__(60, store)
        self.task_id = task_id

    async def before_request(self, request):
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            checkpoint = json.loads(c.execute('SELECT checkpoint FROM approved_tasks WHERE id=?', (self.task_id,)).fetchone()[0])
            used = checkpoint.get('http_requests', 0)
            if used >= 12:
                raise ProviderError('Approved task reached its 12-request cap.')
            day = now()[:10]
            total = c.execute("SELECT used FROM budgets WHERE day=? AND kind='http'", (day,)).fetchone()
            if total and total[0] >= 60:
                raise ProviderError('Corner daily HTTP request budget exhausted.')
            c.execute("INSERT INTO budgets VALUES(?,'http',1) ON CONFLICT(day,kind) DO UPDATE SET used=used+1", (day,))
            checkpoint['http_requests'] = used+1
            c.execute('UPDATE approved_tasks SET checkpoint=?,heartbeat_at=? WHERE id=?',
                      (encoded(checkpoint), now(), self.task_id))
            self.used = used+1


class ApprovedTasks:
    def __init__(self, service, vault):
        self.service, self.store, self.vault = service, service.store, vault
        self.lock = asyncio.Lock()
        self.health = {'status': 'Starting'}

    def event(self, task_id, state, note):
        with self.store.connect() as c:
            c.execute('INSERT INTO approved_task_events(task_id,created_at,state,note) VALUES(?,?,?,?)',
                      (task_id, now(), state, note[:1600]))

    def read(self, task_id):
        if not re.fullmatch(r'[a-f0-9]{12}', task_id):
            raise ValueError('Invalid task ID.')
        rows = self.store.rows('SELECT * FROM approved_tasks WHERE id=?', (task_id,))
        if not rows:
            raise ValueError('Task not found.')
        row = rows[0]
        for key in ('manifest', 'checkpoint', 'result'):
            row[key] = json.loads(row[key])
        return row

    def listing(self):
        tasks = [self.read(r['id']) for r in self.store.rows('SELECT id FROM approved_tasks ORDER BY created_at DESC LIMIT 20')]
        for row in tasks:
            row.pop('checkpoint', None)
            row['result'] = {k: v for k, v in row['result'].items() if k in {'acceptance_passed', 'outcome', 'reason', 'error_type'}}
        return {'tasks': tasks, 'worker': self.health,
                'skills': [{'id': k, 'description': v, 'revision': revision(k)} for k, v in SKILLS.items()],
                'limits': {'tasks_per_utc_day': 5, 'http_per_task': 12, 'seconds_per_task': 120, 'model_calls': 0},
                'public_hosts': sorted(HOSTS), 'enabled': os.getenv('TONY_TASK_WORKER_DISABLED') != '1',
                'policy': 'Inbox status is not task approval. Exact manifest hash required. No arbitrary code or production writes.'}

    def propose(self, skill, args):
        if skill not in SKILLS:
            raise ValueError('Unknown fixed task skill.')
        args = arguments(skill, args)
        task_id, stamp = secrets.token_hex(6), now()
        manifest = {'id': task_id, 'skill': skill, 'arguments': args, 'skill_revision': revision(skill),
                    'instructions': (Path(__file__).parent/'task_skills'/f'{skill}.md').read_text(encoding='utf-8'),
                    'expires_at': (datetime.fromisoformat(stamp)+timedelta(hours=24)).isoformat(),
                    'limits': {'http_requests': 12, 'seconds': 120, 'model_calls': 0},
                    'permissions': 'Read-only task. No code, mapping, schedule, integration, installation, messaging or production changes.'}
        digest = hashlib.sha256(encoded(manifest).encode()).hexdigest()
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT COUNT(*) FROM approved_tasks WHERE state IN ('proposed','approved','running')").fetchone()[0] >= 20:
                raise ValueError('Review or cancel existing tasks before adding more.')
            c.execute("INSERT INTO approved_tasks(id,created_at,manifest,sha256,state) VALUES(?,?,?,?,'proposed')",
                      (task_id, stamp, encoded(manifest), digest))
            c.execute("INSERT INTO approved_task_events(task_id,created_at,state,note) VALUES(?,?,'proposed','Exact scope prepared; not approved.')", (task_id, stamp))
        return self.read(task_id)

    def verify(self, row):
        m = row['manifest']
        if not hmac.compare_digest(hashlib.sha256(encoded(m).encode()).hexdigest(), row['sha256']):
            raise ValueError('Task manifest changed.')
        if m['skill_revision'] != revision(m['skill']):
            raise ValueError('Task skill changed; prepare a new proposal for review.')
        if arguments(m['skill'], m['arguments']) != m['arguments']:
            raise ValueError('Task arguments changed.')
        if datetime.fromisoformat(m['expires_at']) < datetime.now(timezone.utc):
            raise ValueError('Task approval window expired; prepare a new proposal.')

    def approve(self, task_id, prefix, source='authenticated owner'):
        row = self.read(task_id)
        self.verify(row)
        if not isinstance(prefix, str) or len(prefix) != 16 or not hmac.compare_digest(row['sha256'][:16], prefix):
            raise ValueError('Use the exact 16-character task hash from the proposal.')
        with self.store.connect() as c:
            result = c.execute("UPDATE approved_tasks SET state='approved',approved_at=?,approval_source=? WHERE id=? AND state='proposed' AND sha256=?",
                               (now(), source[:120], task_id, row['sha256']))
            if not result.rowcount:
                raise ValueError('Task is not awaiting approval.')
        self.event(task_id, 'approved', 'Owner approved the exact manifest. Worker will execute within its limits.')
        return self.read(task_id)

    def cancel(self, task_id):
        self.read(task_id)
        with self.store.connect() as c:
            r = c.execute("UPDATE approved_tasks SET state='cancelled',finished_at=? WHERE id=? AND state IN ('proposed','approved')", (now(), task_id))
            if not r.rowcount:
                raise ValueError('Only unstarted tasks can be cancelled.')
        self.event(task_id, 'cancelled', 'Cancelled by owner; no task execution.')
        return self.read(task_id)

    def checkpoint(self, task_id, value):
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            current = json.loads(c.execute('SELECT checkpoint FROM approved_tasks WHERE id=?', (task_id,)).fetchone()[0])
            c.execute('UPDATE approved_tasks SET checkpoint=?,heartbeat_at=? WHERE id=?', (encoded(current | value), now(), task_id))

    async def execute(self, row):
        skill, args = row['manifest']['skill'], row['manifest']['arguments']
        budget = TaskBudget(self.store, row['id'])
        if skill == 'coverage_review':
            status = self.service.status()
            flags = [{'day': r['day'], 'issues': json.loads(r['issues'])} for r in latest_counts(self.store) if r['quality'] != 'complete']
            return {'acceptance_passed': True, 'outcome': 'diagnostic_complete', 'coverage': status['coverage'],
                    'pizza_quality': status['whole_pizza_data_quality'], 'flagged_dates': flags,
                    'limitations': 'This diagnostic does not resolve any counts or independently validate POS totals.'}
        if skill == 'public_page':
            result = await read_page(args['url'], budget)
            text = result['text'].strip()
            challenge = re.search(r'verify you are human|just a moment|access denied|enable javascript|enable cookies', text[:500], re.I)
            if len(text) < 100 or (challenge and len(text) < 2500):
                raise ValueError('Page did not expose sufficient readable text; it may require JavaScript or login.')
            return {'acceptance_passed': True, 'outcome': 'public_page_read', 'retrieved_at': now(), **result}
        if skill == 'pizza_audit':
            import httpx
            completed = row['checkpoint'].get('dates', {})
            async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False,
                                         event_hooks={'request': [budget.before_request]}) as http:
                toast = Toast(self.vault, http=http)
                cfg = toast.config()
                restaurant = await toast.get('/restaurants/v1/restaurants/'+cfg['restaurant_guid'])
                cutoff = restaurant.get('general', {}).get('closeoutHour')
                if cutoff is None:
                    raise ValueError('Verified Toast closeout is unavailable.')
                names = menu_names(await toast.get('/menus/v2/menus'))
                mapping, _ = load_mapping()
                for value in args['dates']:
                    if value in completed:
                        continue
                    day = date.fromisoformat(value)
                    if day >= business_day(datetime.now(ET), int(cutoff)):
                        raise ValueError('Date is not closed according to Toast.')
                    orders = await toast.orders(day)
                    report = whole_pizza_summary(orders, day, datetime.now(timezone.utc), int(cutoff), group_names=names)
                    lines = []
                    for order in orders:
                        for check in order.get('checks') or []:
                            for line in check.get('selections') or []:
                                if line.get('deleted') or line.get('voided') or line.get('selectionType') not in (None, 'NONE'):
                                    continue
                                item = (line.get('item') or {}).get('guid')
                                group = (line.get('itemGroup') or {}).get('guid')
                                if (item in mapping['items'] and line.get('splitOrigin')) or (group and group not in names and item not in mapping['items']):
                                    if len(lines) < 100:
                                        lines.append({'order_sha256': hashlib.sha256(str(order.get('guid')).encode()).hexdigest(),
                                            'item_id': safe_guid(item), 'group_id': safe_guid(group),
                                            'quantity': line.get('quantity') if type(line.get('quantity')) in (int, float) and math.isfinite(line['quantity']) else None,
                                            'has_split_origin': bool(line.get('splitOrigin'))})
                    completed[value] = {'quality': report['quality'], 'quantity': report['quantity'],
                        'observed_quantity': report['observed_quantity'], 'issues': report['issues'],
                        'reconciliation': report.get('reconciliation'), 'identity_diagnostics': lines,
                        'retrieved_at': now()}
                    self.checkpoint(row['id'], {'dates': completed})
            return {'acceptance_passed': set(completed) == set(args['dates']), 'outcome': 'diagnostic_complete',
                    'dates': completed, 'http_requests': budget.used,
                    'limitations': 'No counts, mapping or POS records changed. Partial days still need review; raw orders and personal fields were not saved.'}
        raise ValueError('No handler for task skill.')

    async def tick(self):
        if os.getenv('TONY_TASK_WORKER_DISABLED') == '1':
            return {'status': 'disabled'}
        async with self.lock:
            stamp = now()
            # Durable lease: resume a stopped process once, using per-date checkpoints.
            cutoff = (datetime.fromisoformat(stamp)-timedelta(minutes=3)).isoformat()
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                c.execute("UPDATE approved_tasks SET state=CASE WHEN attempts<2 THEN 'approved' ELSE 'blocked' END,error='Prior process stopped; lease expired.' WHERE state='running' AND heartbeat_at<?", (cutoff,))
                if c.execute("SELECT 1 FROM approved_tasks WHERE state='running' LIMIT 1").fetchone():
                    return {'status': 'busy'}
                if c.execute('SELECT COALESCE(SUM(attempts),0) FROM approved_tasks WHERE substr(started_at,1,10)=?', (stamp[:10],)).fetchone()[0] >= 5:
                    return {'status': 'daily_limit'}
                selected = c.execute("SELECT id FROM approved_tasks WHERE state='approved' ORDER BY approved_at LIMIT 1").fetchone()
                if not selected:
                    return {'status': 'idle'}
                task_id = selected[0]
                changed = c.execute("UPDATE approved_tasks SET state='running',started_at=?,heartbeat_at=?,attempts=attempts+1 WHERE id=? AND state='approved'", (stamp, stamp, task_id))
                if not changed.rowcount:
                    return {'status': 'idle'}
            row = self.read(task_id)
            self.event(task_id, 'running', 'Fixed approved skill started.')
            try:
                self.verify(row)
                result = await asyncio.wait_for(self.execute(row), timeout=120)
                if result.get('acceptance_passed') is not True:
                    raise ValueError('Task acceptance checks were not met.')
                directory = self.service.workspace/'tasks'/task_id
                directory.mkdir(parents=True, exist_ok=True)
                (directory/'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
                state, error = 'completed', None
            except Exception as exception:
                result = {'acceptance_passed': False, 'error_type': type(exception).__name__,
                          'reason': str(exception)[:220] if isinstance(exception, (ValueError, ProviderError)) else 'Task unavailable; prior checkpoints retained.'}
                state, error = 'blocked', result['reason']
            with self.store.connect() as c:
                c.execute('UPDATE approved_tasks SET state=?,finished_at=?,heartbeat_at=?,result=?,error=? WHERE id=?',
                          (state, now(), now(), encoded(result), error, task_id))
            self.event(task_id, state, error or 'Acceptance checks passed; result saved. Underlying ambiguities may still require review.')
            self.store.journal(datetime.now(ET).date().isoformat(), 'approved_task',
                f'Task {task_id} ({row["manifest"]["skill"]}): {state}. No code or production data changed.', {'task_id': task_id})
            return {'id': task_id, 'status': state}

    async def run(self):
        while True:
            try:
                result = await self.tick()
                self.health = {'status': result['status'], 'last_check': now()}
            except Exception as error:
                self.health = {'status': 'unavailable', 'last_check': now(), 'error_type': type(error).__name__}
            await asyncio.sleep(5)


def proposal_text(task):
    return (f"Task {task['id']} — {task['state']}\nScope and limits:\n{json.dumps(task['manifest'], indent=2)}\n"
            f"Approve this exact task: /task_approve {task['id']} {task['sha256'][:16]}\n"
            f"Cancel before it starts: /task_cancel {task['id']}\nResults: /task_status {task['id']}")
