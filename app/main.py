import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import zipfile
import httpx
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, time as walltime
from pathlib import Path
from xml.etree import ElementTree
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .ai import AIError, ChatGPT
from .calculations import ET
from .operations import Operations
from .providers import ProviderError, Toast, validate_ezcater
from .storage import DB, ROOT, Vault, utcnow
from .telegram import Telegram
from .outlook import Outlook
from .games import FootballCalendar
from .clover import Clover, CloverError
from .sheet_calendar import SheetCalendar
from .monday import Monday, MondayError
from .scheduling import deliver_briefing, schedule_status
from .corner.service import CornerService
from .weekly_sources import WeeklySources
from .approved_tasks import ApprovedTasks
from .shift_desk import ShiftDesk
from .menu_knowledge import get_menu
from .request_limits import RequestBodyLimit
from .exceptions import ExceptionLedger
from .source_health import inspect as inspect_source_health
from .verified_api import summary as verified_summary
from .writeups import Writeups

load_dotenv(ROOT / '.env')
logger = logging.getLogger('tonyops')


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def check_password(password, stored):
    try:
        return hmac.compare_digest(hash_password(password, stored.split(':')[0]), stored)
    except (ValueError, AttributeError):
        return False


def scheduler_delay(now):
    target = datetime.combine(now.astimezone(ET).date(), walltime(9), ET)
    remaining = (target - now.astimezone(ET)).total_seconds()
    return min(300, remaining) if remaining > 0 else 300


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    setup_code: str = Field(default='', max_length=100)


class FollowupInput(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    due_day: str = Field(default='', max_length=10)
    note: str = Field(default='', max_length=1500)
    priority: int = Field(default=2, ge=1, le=3)


class TaskProposal(BaseModel):
    skill: str = Field(pattern='^(coverage_review|pizza_audit|public_page)$')
    arguments: dict = Field(default_factory=dict)


class TaskApproval(BaseModel):
    hash: str = Field(pattern='^[a-f0-9]{16}$')


class ImprovementUpdate(BaseModel):
    status: str = Field(pattern='^(proposed|queued|blocked|done|dismissed)$')
    note: str = Field(min_length=1, max_length=1600)


class ExceptionReview(BaseModel):
    status: str = Field(pattern="^(open|acknowledged|resolved|false_positive)$")
    note: str = Field(min_length=1, max_length=1500)
    assigned_to: str = Field(default="", max_length=120)
    expected_revision: int = Field(ge=1)


class ExceptionInput(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    note: str = Field(min_length=1, max_length=1500)
    severity: str = Field(default="warning", pattern="^(info|warning|critical)$")
    due_at: str | None = Field(default=None, max_length=40)


class Chat(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class Memory(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    kind: str = Field(pattern='^(permanent|temporary|task)$')
    expires: datetime | None = None
    done: bool = False


class Settings(BaseModel):
    demo: bool = True
    closeout_hour: int = Field(default=4, ge=0, le=23)
    workweek_start: int | None = Field(default=None, ge=0, le=6)
    overtime_multiplier: float | None = Field(default=None, ge=1, le=3)
    future_days: int = Field(default=7, ge=1, le=30)
    large_order_threshold: float = Field(default=300, ge=1, le=100000)
    briefing_ai: bool = False
    telegram_briefings: bool = False
    web_search_enabled: bool = False


class CloverCredential(BaseModel):
    merchant_id: str = Field(pattern=r'^[A-Za-z0-9]{8,32}$')
    token: str = Field(min_length=8, max_length=2048)


class SheetLink(BaseModel):
    url: str = Field(min_length=50, max_length=500)


class MondayCredential(BaseModel):
    url: str = Field(min_length=25, max_length=500)
    token: str = Field(min_length=1, max_length=4096)


def create_app(directory=None, vault=None, background=True):
    directory = Path(directory or os.getenv('TONY_DATA_DIR', ROOT / 'data'))
    db = DB(directory)
    vault = vault or Vault(directory)
    if not vault.read().get('password_hash') and not vault.read().get('setup_code'):
        vault.update(setup_code=secrets.token_urlsafe(18))
    toast = Toast(vault)
    ai = ChatGPT(vault, db)
    outlook = Outlook(vault, db)
    football = FootballCalendar()
    calendar = SheetCalendar(vault, db)
    clover = Clover(vault)
    monday = Monday(vault, db)
    weekly_sources = WeeklySources(db, calendar, monday, ROOT/'imports'/'monday' if directory.resolve() == (ROOT/'data').resolve() else directory/'imports'/'monday')
    corner_error = None
    try:
        if os.getenv('TONY_CORNER_DISABLED') == '1':
            raise RuntimeError('Corner disabled by local rollback switch.')
        workspace = ROOT / 'Tonys-Corner' if directory.resolve() == (ROOT / 'data').resolve() else directory / 'Tonys-Corner'
        corner = CornerService(workspace)
        corner.config()
    except Exception as error:
        corner, corner_error = None, type(error).__name__
        logger.warning('Corner initialization unavailable; existing operations continue.')
    ops = Operations(db, toast, ai, outlook, football, calendar, monday, corner)
    ops.writeups = Writeups(db, ai, directory=directory/'writeups')
    task_worker = ApprovedTasks(corner, vault) if corner else None
    ops.task_worker = task_worker
    ops.shift_desk = ShiftDesk(db)
    ledger = ExceptionLedger(db)
    ops.exception_ledger = ledger
    telegram = Telegram(vault, db, ops)
    port = int(os.getenv('TONY_PORT', '8790'))
    origin = os.getenv('TONY_ORIGIN', f'http://127.0.0.1:{port}').rstrip('/')
    if urlsplit(origin).hostname not in {'localhost', '127.0.0.1'} and not origin.startswith('https://'):
        raise RuntimeError('Remote origin requires HTTPS.')
    origins = {origin, f'http://127.0.0.1:{port}', f'http://localhost:{port}'}
    hosts = {urlsplit(x).netloc for x in origins}
    if not background:
        hosts.add('testserver')
    attempts = []

    async def scheduler():
        while not app.state.stopping:
            now = datetime.now(ET)
            db.set('scheduler', {'last_check': utcnow(), 'status': 'Running'})
            try:
                health = inspect_source_health(db, {t.get_name(): 'Stopped' if t.done() else 'Running' for t in app.state.background_tasks})
                ledger.reconcile(health['findings'], health['healthy_keys'])
            except Exception as error:
                logger.warning('Local source health check unavailable: %s', type(error).__name__)
            if now.hour >= 9:
                try:
                    report = await ops.briefing()
                    if app.state.stopping: break
                    if ops.settings()['telegram_briefings'] and telegram.config().get('enabled') and report['mode'] == 'live':
                        await deliver_briefing(ops, telegram)
                    db.set('scheduler', {'last_check': utcnow(), 'status': 'Ready', 'next': '09:00 America/New_York; catches up after restart'})
                except Exception as e:
                    # Only exception class, never upstream response bodies/URLs/tokens.
                    logger.warning(json.dumps({'event': 'briefing_unavailable', 'type': type(e).__name__}))
                    db.set('scheduler', {'last_check': utcnow(), 'status': 'Retrying unavailable briefing in 5 minutes'})
            await asyncio.sleep(scheduler_delay(datetime.now(ET)))

    @asynccontextmanager
    async def lifespan(app):
        tasks = [asyncio.create_task(scheduler(),name='scheduler'), asyncio.create_task(telegram.run(),name='telegram')] if background else []
        if background and task_worker and os.getenv('TONY_TASK_WORKER_DISABLED') != '1':
            tasks.append(asyncio.create_task(task_worker.run(), name='approved_tasks'))
        if background and db.get('weekly_sources_config', {}).get('enabled'):
            tasks.append(asyncio.create_task(weekly_sources.run(), name='weekly_sources'))
        if background and corner and corner.config()['enabled']:
            tasks.append(asyncio.create_task(corner.scheduled_loop(vault, ops.settings, ai), name='corner'))
        app.state.background_tasks=tasks
        yield
        app.state.stopping=True
        if corner:
            corner.stopping.set()
        telegram.closing=True
        async def drain():
            await telegram.inflight.wait()
            async with telegram.briefing_delivery_lock, ops.briefing_lock, telegram.send_lock:
                pass
        try:
            await asyncio.wait_for(drain(),timeout=120)
        except asyncio.TimeoutError:
            logger.warning('Graceful worker drain timed out; pending delivery remains recorded for review.')
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await toast.http.aclose()
        await ai.http.aclose()
        await telegram.http.aclose()
        await outlook.http.aclose()
        await football.close()
        await clover.http.aclose()
        await monday.http.aclose()

    app = FastAPI(title="the restaurant AI Operations", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(RequestBodyLimit)
    app.state.db, app.state.vault, app.state.ops = db, vault, ops
    app.state.ai, app.state.toast, app.state.telegram = ai, toast, telegram
    app.state.started_at = utcnow()
    app.state.stopping = False
    app.state.background_tasks = []

    @app.middleware('http')
    async def security(request, call_next):
        path = request.url.path
        if request.headers.get('host') not in hosts:
            return JSONResponse({'detail': 'Unrecognized host'}, 400)
        try:
            content_length = int(request.headers.get('content-length', '0') or 0)
        except ValueError:
            return JSONResponse({'detail': 'Invalid content length'}, 400)
        if content_length < 0:
            return JSONResponse({'detail': 'Invalid content length'}, 400)
        if content_length > (8_000_000 if path == '/api/monday/import' else 131072):
            return JSONResponse({'detail': 'Request too large'}, 413)
        token = request.cookies.get('tonyops_session', '')
        digest = hashlib.sha256(token.encode()).hexdigest()
        session = db.rows('SELECT * FROM sessions WHERE token=? AND expires>?', (digest, time.time()))
        request.state.session = session[0] if session else None
        public = path in ['/api/login', '/api/session', '/healthz', '/auth/callback', '/webhooks/ezcater'] or not path.startswith(('/api/', '/auth/'))
        if not public and not session:
            return JSONResponse({'detail': 'Sign in required'}, 401)
        if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} and path != '/webhooks/ezcater':
            if request.headers.get('origin') not in origins:
                return JSONResponse({'detail': 'Invalid request origin'}, 403)
            if path == '/api/login':
                if request.headers.get('x-tony-request') != '1':
                    return JSONResponse({'detail': 'Invalid login request'}, 403)
            else:
                supplied = request.headers.get('x-csrf-token', '')
                if not session or not hmac.compare_digest(supplied, session[0]['csrf']):
                    return JSONResponse({'detail': 'Invalid session request'}, 403)
        response = await call_next(request)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
                                 'Referrer-Policy': 'no-referrer', 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.exception_handler(ProviderError)
    async def provider_error(request, error):
        return JSONResponse({'detail': str(error)}, 503)

    @app.exception_handler(AIError)
    async def ai_error(request, error):
        ai.record_error(error)
        return JSONResponse({'detail': error.code, 'request_id': error.request_id}, 503)

    @app.get('/healthz')
    def health():
        return {'status': 'ok'}

    @app.get('/api/session')
    def session(request: Request):
        return {'authenticated': bool(request.state.session), 'setup_required': not bool(vault.read().get('password_hash')),
                'csrf': request.state.session['csrf'] if request.state.session else None}

    @app.post('/api/login')
    def login(data: Login, request: Request):
        nonlocal attempts
        attempts = [t for t in attempts if time.time()-t < 300]
        if len(attempts) >= 10:
            raise HTTPException(429, 'Too many login attempts; wait five minutes.')
        attempts.append(time.time())
        saved = vault.read()
        if not saved.get('password_hash'):
            if request.client.host not in {'127.0.0.1', '::1', 'testclient'} or not hmac.compare_digest(data.setup_code, saved.get('setup_code', '')) or len(data.password) < 12:
                raise HTTPException(401, 'Enter the local setup code and a password of at least 12 characters.')
            vault.update(password_hash=hash_password(data.password), setup_code='')
        elif not check_password(data.password, saved['password_hash']):
            raise HTTPException(401, 'Incorrect password.')
        attempts = []
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
        db.execute('INSERT INTO sessions VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), csrf, time.time()+43200))
        response = JSONResponse({'csrf': csrf})
        response.set_cookie('tonyops_session', token, httponly=True, secure=request.headers.get('origin', '').startswith('https://'), samesite='lax', max_age=43200)
        return response

    @app.post('/api/logout')
    def logout(request: Request):
        db.execute('DELETE FROM sessions WHERE token=?', (request.state.session['token'],))
        response = JSONResponse({'ok': True})
        response.delete_cookie('tonyops_session')
        return response

    @app.get('/api/overview')
    async def overview(date: str | None = None):
        try:
            return await ops.report(date)
        except ValueError:
            raise HTTPException(422, 'Invalid date; use YYYY-MM-DD.')

    @app.get('/api/changes')
    async def changes():
        return await ops.changes('web', commit=False)

    @app.post('/api/checkin')
    async def checkin():
        return await ops.changes('web')

    @app.post('/api/chat')
    async def chat(data: Chat):
        return await ops.chat(data.text)

    @app.get('/api/chat')
    def history(channel: str = 'web'):
        if channel not in ('web','telegram'):raise HTTPException(400,'Unknown conversation channel.')
        return ops.conversation_history(channel)

    @app.get('/api/writeups/{artifact}/pdf')
    def writeup_pdf(artifact: str):
        try:
            data = ops.writeups.pdf(artifact)
        except ValueError:
            raise HTTPException(404,'Private coaching draft not available.') from None
        return Response(data,media_type='application/pdf',headers={
            'Content-Disposition':'attachment; filename="Coaching-Draft-Unsigned.pdf"'})

    @app.delete('/api/chat')
    def clear_history():
        db.execute('DELETE FROM messages')
        ops.conversation_archive.clear('web')
        ops.conversation_archive.clear('telegram')
        return {'ok': True}

    @app.get('/api/memory')
    def memory():
        return db.rows('SELECT * FROM memory ORDER BY id DESC')

    def memory_values(data):
        if data.kind == 'temporary' and data.expires is None:
            raise HTTPException(422, 'Temporary notes require an expiration.')
        if data.expires and data.expires.tzinfo is None:
            raise HTTPException(422, 'Expiration needs a timezone.')
        return (data.text, data.kind, data.expires.astimezone(__import__('datetime').timezone.utc).isoformat() if data.expires else None, int(data.done))

    @app.post('/api/memory')
    def add_memory(data: Memory):
        return {'id': db.execute('INSERT INTO memory(text,kind,expires,done,created) VALUES (?,?,?,?,?)', (*memory_values(data), utcnow()))}

    @app.put('/api/memory/{id}')
    def edit_memory(id: int, data: Memory):
        db.execute('UPDATE memory SET text=?,kind=?,expires=?,done=? WHERE id=?', (*memory_values(data), id))
        return {'ok': True}

    @app.delete('/api/memory/{id}')
    def delete_memory(id: int):
        db.execute('DELETE FROM memory WHERE id=?', (id,))
        return {'ok': True}

    @app.get('/api/briefings')
    def briefings():
        return db.rows('SELECT * FROM briefings ORDER BY id DESC LIMIT 60')

    @app.post('/api/briefings')
    async def generate_briefing():
        return await ops.briefing()

    @app.get('/api/settings')
    def settings():
        return ops.settings()

    @app.put('/api/settings')
    def save_settings(data: Settings):
        db.set('settings', data.model_dump())
        ops.cache.clear()
        return {'ok': True}

    @app.post('/api/clover/connect')
    async def connect_clover(data: CloverCredential):
        try:
            await clover.verify(data.merchant_id, data.token)
        except CloverError as error:
            raise HTTPException(422, str(error)) from None
        vault.update(clover={'merchant_id': data.merchant_id, 'token': data.token})
        return {'configured': True, 'merchant_id': data.merchant_id}

    @app.post('/api/calendar/connect')
    async def connect_calendar(data: SheetLink):
        try:
            return await calendar.connect(data.url)
        except (ValueError, httpx.HTTPError, KeyError, IndexError, zipfile.BadZipFile, ElementTree.ParseError) as error:
            raise HTTPException(422, 'Could not read the shared monthly Google Sheet: ' + str(error)[:180]) from None

    @app.post('/api/calendar/refresh')
    async def refresh_calendar():
        if not calendar.id():
            raise HTTPException(404, 'No Google Sheet calendar is connected.')
        return await calendar.refresh(force=True)

    @app.post('/api/monday/connect')
    async def connect_monday(data: MondayCredential):
        try:
            return await monday.connect(data.url, data.token)
        except MondayError as error:
            raise HTTPException(422, str(error)) from None

    @app.post('/api/monday/import')
    async def import_monday(request: Request):
        if request.headers.get('content-type') != 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
            raise HTTPException(415, 'Choose a monday.com .xlsx board export.')
        chunks = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 8_000_000:
                raise HTTPException(413, 'Board export exceeds the 8 MB file limit.')
            chunks.append(chunk)
        data = b''.join(chunks)
        try:
            return monday.import_export(data, request.headers.get('x-monday-board-url', ''))
        except (MondayError, ValueError, KeyError, IndexError, TypeError, zipfile.BadZipFile, ElementTree.ParseError) as error:
            raise HTTPException(422, 'Could not import the monday.com board export: ' + str(error)[:160]) from None

    def safe_corner_status():
        try:
            return corner.status() if corner else {'available': False, 'error': corner_error}
        except Exception as error:
            return {'available': False, 'error': type(error).__name__}

    @app.get('/api/status')
    def status():
        configured = bool(vault.read().get('toast', {}).get('client_secret'))
        clover_coverage = db.rows('SELECT MIN(day) AS first_day, MAX(day) AS last_day, COUNT(*) AS days FROM clover_days')[0]
        return {'application':'TonyRestaurantOps', 'runtime':{'pid':os.getpid(),'started_at':app.state.started_at,'web_server':'Healthy',
                'telegram_heartbeat':db.get('telegram_heartbeat'),
                'workers':{task.get_name():'Stopped' if task.done() else 'Running' for task in app.state.background_tasks},
                'log':str(directory/'app.log')},
                'chatgpt': ai.status(), 'toast': {'status': 'Configured — awaiting successful read' if configured and not toast.health else 'Not connected' if not configured else 'See endpoint checks', 'endpoints': toast.health},
                'web_search': {'enabled': ops.settings()['web_search_enabled'], 'daily_requests': db.get('web_search_requests:' + datetime.now(ET).date().isoformat(), 0), 'daily_request_limit': 25, 'last_error': db.get('web_search_last_error')},
                'telegram': telegram.status, 'clover': {**clover.status(), 'history': clover_coverage}, 'calendar': calendar.status(), 'r365': 'Not connected — provider interface only', 'ezcater': 'Disabled — signed receiver available; no hosted endpoint',
                'outlook': outlook.status(), 'monday': monday.status(),
                'corner': safe_corner_status(), 'weekly_sources': weekly_sources.status(),
                'approved_task_worker': task_worker.listing()['worker'] if task_worker else {'status':'Unavailable'},
                'scheduler': schedule_status(db, ops.settings()['telegram_briefings']), 'briefing_error': db.get('briefing_error'),
                'timezone': 'America/New_York', 'version': '0.2.0-alpha', 'transport': origin}

    @app.get('/api/source-health')
    def source_health():
        return inspect_source_health(db, {t.get_name(): 'Stopped' if t.done() else 'Running' for t in app.state.background_tasks})

    @app.get('/api/exceptions')
    def exceptions(include_closed: bool = False):
        return ledger.listing(include_closed)

    @app.post('/api/exceptions/refresh')
    def refresh_exceptions():
        health = source_health()
        ledger.reconcile(health['findings'], health['healthy_keys'])
        return {'health': health, 'exceptions': ledger.listing(), 'external_actions': False}

    @app.post('/api/exceptions')
    def add_exception(data: ExceptionInput):
        try:
            key = 'manager:'+hashlib.sha256(data.title.strip().casefold().encode()).hexdigest()
            return ledger.observe(key, data.title, 'Manager-entered', data.severity, {'note':data.note}, data.due_at)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.post('/api/exceptions/{identity}')
    def review_exception(identity: str, data: ExceptionReview):
        try:
            return ledger.transition(identity, data.status, data.note, data.assigned_to, data.expected_revision)
        except ValueError as error:
            raise HTTPException(409 if 'changed' in str(error) else 422, str(error)) from None

    @app.get('/api/v1/verified-summary')
    def max_summary():
        return verified_summary(db, ledger, corner)

    @app.get('/api/corner')
    def corner_records(view: str = 'overview', date: str | None = None):
        if not corner:
            return {'available': False, 'reason': "Tony's Corner is unavailable; existing operations continue."}
        try:
            return corner.query(view, date)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.post('/api/corner/improvements/{task_id}')
    def review_improvement(task_id: int, review: ImprovementUpdate):
        if not corner:
            raise HTTPException(503, 'Research unavailable.')
        from .corner.inbox import update
        try:
            return update(corner.store, task_id, review.status, review.note)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    def require_task_worker():
        if not task_worker:
            raise HTTPException(503, 'Task worker unavailable.')
        return task_worker

    @app.get('/api/shift')
    def shift_desk(day: str | None = None):
        try:
            return ops.shift_desk.view(day)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.get('/api/seasonal-menu')
    def seasonal_menu(query: str = 'Halloween'):
        return get_menu(db, query[:160])

    @app.post('/api/followups')
    def add_followup(body: FollowupInput):
        try:
            return ops.shift_desk.add(body.title, body.due_day, body.note, body.priority)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.post('/api/followups/{key}/done')
    def complete_followup(key: str):
        try:
            return ops.shift_desk.complete(key)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.get('/api/tasks')
    def tasks():
        return require_task_worker().listing()

    @app.get('/api/tasks/{task_id}')
    def task_details(task_id: str):
        try:
            return require_task_worker().read(task_id)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.post('/api/tasks/propose')
    def propose_task(body: TaskProposal):
        try:
            return require_task_worker().propose(body.skill, body.arguments)
        except (ValueError, TypeError) as error:
            raise HTTPException(422, str(error)) from None

    @app.post('/api/tasks/{task_id}/approve')
    def approve_task(task_id: str, body: TaskApproval):
        try:
            return require_task_worker().approve(task_id, body.hash)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.post('/api/tasks/{task_id}/cancel')
    def cancel_task(task_id: str):
        try:
            return require_task_worker().cancel(task_id)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.post('/api/corner/check-isolation')
    async def corner_isolation_check():
        if not corner:
            raise HTTPException(503, 'Research unavailable.')
        # Fixed synthetic denial test only. No arguments, shell or AI tool.
        try:
            return await asyncio.to_thread(corner.worker.probe)
        except Exception as error:
            from .corner.isolation import IsolationUnavailable
            return {'passed': False, 'error': str(error)[:250] if isinstance(error, IsolationUnavailable) else type(error).__name__,
                    'environment_present': {k: k in os.environ for k in ('SystemRoot','WINDIR','SystemDrive','USERPROFILE','LOCALAPPDATA','APPDATA')}}

    @app.post('/api/manage/{command}')
    async def manage(command: str):
        if command == 'stop':
            shutdown = getattr(app.state, 'shutdown', None)
            if not shutdown: raise HTTPException(409, 'Server was not launched through app.manage serve.')
            asyncio.get_running_loop().call_later(.5, shutdown)
            return {'status':'Graceful stop requested', 'pid':os.getpid()}
        if command == 'check-ai':
            try:
                choices = await ai.models()
                return {'models':choices,'ai':ai.status()}
            except (AIError,httpx.HTTPError) as e:
                ai.record_error(e if isinstance(e,AIError) else AIError('discovery_network_unavailable'))
                return {'ai':ai.status()}
        if command == 'test-ai':
            reply = await ops.chat('Use get_report to check live restaurant operations today. Give a concise factual summary and cite the business date and retrieval time.', 'diagnostic')
            return {**reply,'status':ai.status()}
        if command == 'test-item-query':
            day = (datetime.now(ET).date() - timedelta(days=1)).isoformat()
            reply = await ops.chat(f'How many large pizzas did we sell on {day}? Use Toast item-level quantities and give the breakdown.', 'diagnostic')
            return {**reply, 'last_tool': ai.status().get('last_tool')}
        if command == 'test-weekly-query':
            reply = await ops.chat('How were sales last week?', 'diagnostic')
            return {**reply, 'last_tool': ai.status().get('last_tool')}
        if command == 'test-pmix-query':
            reply = await ops.chat('How many wing and chicken-finger portions and pieces did we sell last week versus the week before? Give the item-size breakdown.', 'diagnostic')
            return {**reply, 'last_tool': ai.status().get('last_tool')}
        if command == 'test-forecast-query':
            reply = await ops.chat('Predict our Toast net sales for today. Consider our recent trend and any football games, and give a planning range and limitations.', 'diagnostic')
            return {**reply, 'last_tool': ai.status().get('last_tool')}
        if command == 'test-build-query':
            reply = await ops.chat('According to our line build sheet, how many meatballs go in one meatball appetizer? Give the source and note whether this is actual inventory usage.', 'diagnostic')
            return {**reply, 'last_tool': ai.status().get('last_tool')}
        if command == 'test-ai-telegram':
            reply = await ops.chat('How is the restaurant doing today? Use the live Toast report and give a short summary.', 'telegram')
            await telegram.send('AI + TOAST DELIVERY TEST\n'+reply['text'])
            return {'ai':reply['ai'],'delivery':db.get('telegram_delivery'),'model':ai.status()['model']}
        if command == 'test-briefing': return await deliver_briefing(ops,telegram,manual=True)
        if command == 'check-outlook':
            result=await outlook.messages(category='important')
            return {'outlook':outlook.status(),'mail':result}
        if command == 'check-telegram':
            bot=await telegram.call('getMe',{})
            return {'connected':True,'bot':bot.get('username'),'private_allowlist':True,'worker':telegram.status}
        if command == 'test-telegram':
            cfg=telegram.config()
            for index,text in enumerate(['/status','/sales']):
                await telegram.handle({'update_id':'test-'+str(time.time_ns())+'-'+str(index),
                    'message':{'chat':{'type':'private','id':cfg['chat_id']},'from':{'id':cfg['user_id']},'text':text}})
            return {'status':'Status and factual sales sent','delivery':db.get('telegram_delivery')}
        raise HTTPException(404,'Unknown management command')

    @app.post('/api/backup')
    def backup():
        return {'file': db.backup()}

    @app.post('/api/ai/start')
    async def start_ai(request: Request):
        data = await request.json()
        # Keep authorization URL (including any future hints) out of JavaScript payloads.
        url = ai.begin(request.state.session['token'], port, data.get('account'), data.get('reconsent', False))
        nonce = secrets.token_urlsafe(24)
        ai.pending['redirect:' + nonce] = {'url': url, 'session': request.state.session['token'], 'until': time.time()+60}
        return {'url': '/auth/launch/' + nonce}

    @app.get('/auth/launch/{nonce}')
    def launch_ai(nonce: str, request: Request):
        attempt = ai.pending.pop('redirect:' + nonce, None)
        if not attempt or attempt['session'] != request.state.session['token'] or attempt['until'] < time.time():
            raise HTTPException(400, 'Authorization link expired.')
        return RedirectResponse(attempt['url'], 303)

    @app.get('/auth/callback')
    async def callback(request: Request):
        if not request.state.session:
            raise HTTPException(401, 'Start sign-in from http://127.0.0.1 on this computer.')
        try:
            await ai.callback(dict(request.query_params), request.state.session['token'])
        except AIError as e:
            ai.record_error(e)
        return RedirectResponse('/#settings', 303)

    @app.get('/api/ai/models')
    async def models():
        return await ai.models()

    @app.post('/api/ai/model')
    async def model(request: Request):
        data = await request.json()
        choices = await ai.models()
        if data.get('model') not in [x['slug'] for x in choices]:
            raise HTTPException(400, 'Select a model offered to this account.')
        db.set('model:' + str(db.get('active_account')), data['model'])
        return {'ok': True}

    @app.post('/api/ai/account')
    async def account(request: Request):
        data = await request.json()
        async with ai.inference_lock, ai.lock:
            if data.get('account') not in ai.records():
                raise HTTPException(400, 'Unknown account.')
            db.set('active_account', data['account'])
            db.set('ai_paused', False)
            db.set('ai_error', None)
            db.set('ai_pause_reason', None)
            ai.models_cache.clear()
        return {'ok': True}

    @app.post('/api/ai/signout')
    async def ai_signout():
        return {'revocation_confirmed': await ai.signout()}

    @app.post('/api/ai/resume')
    def resume_ai():
        db.set('ai_paused', False)
        db.set('ai_pause_reason', None)
        db.set('ai_error', None)
        return {'ok': True}

    @app.post('/webhooks/ezcater')
    async def ezcater(request: Request):
        cfg = vault.read().get('ezcater', {})
        if not cfg.get('enabled') or not cfg.get('secret'):
            raise HTTPException(404, 'Disabled')
        body = await request.body()
        if len(body) > 131072 or not validate_ezcater(body, request.headers.get('x-ezcater-signature'), cfg['secret']):
            raise HTTPException(401, 'Invalid webhook signature')
        digest = hashlib.sha256(body).hexdigest()
        db.execute('INSERT OR IGNORE INTO webhook_receipts VALUES (?,?)', (digest, utcnow()))
        # Only retain a receipt hash until an actual subscription payload mapping is configured.
        return {'received': True, 'detail': 'Verified receipt only; order hydration pending.'}

    app.mount('/static', StaticFiles(directory=ROOT / 'app' / 'static'), name='static')

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'app' / 'static' / 'index.html')

    return app
