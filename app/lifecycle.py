"""Manage the local server through authenticated requests and its Windows task."""
import hashlib
import json
import os
import secrets
import subprocess
import time
from contextlib import contextmanager
import httpx
from .storage import ROOT

TASK = 'TonyRestaurantOps-LocalOperations'

@contextmanager
def client(db):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    db.execute('INSERT INTO sessions VALUES (?,?,?)', (digest, csrf, time.time()+600))
    port = int(os.getenv('TONY_PORT', '8790'))
    origin = f'http://127.0.0.1:{port}'
    try:
        with httpx.Client(base_url=origin, timeout=300, trust_env=False,
                          cookies={'tonyops_session': token}, headers={'Origin': origin, 'X-CSRF-Token': csrf}) as connection:
            yield connection
    finally:
        db.execute('DELETE FROM sessions WHERE token=?', (digest,))

def request(db, command):
    with client(db) as c:
        r = c.get('/api/status',timeout=5) if command in {'status','diagnostics'} else c.post('/api/manage/'+command)
        if r.status_code != 200:
            raise RuntimeError(f'Local management request failed (HTTP {r.status_code}). See data/app.log.')
        value = r.json()
        if command in {'status','diagnostics'} and value.get('application') != 'TonyRestaurantOps':
            raise RuntimeError('Listener does not identify as TonyRestaurantOps; no action taken.')
        return value

def running(db):
    try:
        return request(db, 'status')
    except (httpx.NetworkError,httpx.RemoteProtocolError,httpx.TimeoutException):
        return None

def lock_released(db):
    from .server_lock import acquire_server_lock
    handle=acquire_server_lock(db.path.parent/'server.lock')
    if handle is None: return False
    handle.close()
    return True

def start(db):
    existing = running(db)
    if existing:
        return {'status':'Already running', 'pid': existing['runtime']['pid']}
    if not lock_released(db):
        raise RuntimeError('TonyRestaurantOps owns its process lock but is not responding. It may be starting or shutting down; check data/app.log before retrying.')
    result = subprocess.run(['schtasks.exe','/Run','/TN',TASK], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Startup task unavailable. Run Install-Startup.ps1 under the Windows user who owns this vault.')
    for _ in range(40):
        time.sleep(.5)
        status = running(db)
        if status:
            return {'status':'Started by Windows Task Scheduler','pid':status['runtime']['pid']}
    raise RuntimeError('Server did not become ready. Check data/app.log and Task Scheduler history.')

def stop(db):
    existing=running(db)
    if not existing and lock_released(db):
        return {'status':'Already stopped'}
    if not existing:
        raise RuntimeError('TonyRestaurantOps is not responding but still owns its process lock; no process was killed. Check data/app.log.')
    result = request(db, 'stop')
    for _ in range(360):
        time.sleep(.5)
        # Do not create new HTTP connections while Windows asyncio closes the listener.
        # The process releases this lock only after server.run() and worker drain finish.
        if lock_released(db):
            # Let the scheduled task observe the clean exit before a restart.
            time.sleep(1)
            return result
    raise RuntimeError('Graceful stop timed out; no unrelated process was terminated.')

def execute(db, command):
    if command == 'start': return start(db)
    if command == 'stop': return stop(db)
    if command == 'restart':
        stop(db)
        return start(db)
    if command in {'status','diagnostics'}:
        return running(db) or {'application':'TonyRestaurantOps','web_server':'Stopped' if lock_released(db) else 'Not responding; process lock held','log':str(ROOT/'data'/'app.log')}
    return request(db,command)
