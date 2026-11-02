import asyncio
import time
import hashlib
from .code_proposals import CodeProposals
from .storage import utcnow
from .briefing_format import telegram_briefing
from .guest_lookup import is_guest_lookup

import httpx


def allowed(message, config):
    return (message.get('chat', {}).get('type') == 'private'
            and str(message.get('from', {}).get('id')) == str(config.get('user_id'))
            and str(message.get('chat', {}).get('id')) == str(config.get('chat_id')))


def chunks(text, limit=3500):
    # Telegram limit is UTF-16 units, not Python code points (emoji use two).
    piece, length = '', 0
    for char in text:
        units = len(char.encode('utf-16-le')) // 2
        if length + units > limit:
            yield piece
            piece, length = '', 0
        piece += char
        length += units
    if piece:
        yield piece


class Telegram:
    def __init__(self, vault, db, operations):
        self.vault, self.db, self.ops = vault, db, operations
        self.http = httpx.AsyncClient(trust_env=False, timeout=40, follow_redirects=False)
        self.last_message = 0
        self.status = 'Disabled'
        self.briefing_delivery_lock = asyncio.Lock()
        self.send_lock = asyncio.Lock()
        self.closing = False
        self.inflight = asyncio.Event()
        self.inflight.set()
        self.code = CodeProposals(db, ai=getattr(operations, 'ai', None))
        self.code.recover_incomplete()

    def config(self):
        return self.vault.read().get('telegram', {})

    async def call(self, method, payload):
        token = self.config().get('bot_token')
        if not token:
            raise RuntimeError('Telegram not configured')
        # Never log request URLs: Telegram embeds its credential in the URL.
        for attempt in range(3):
            response = await self.http.post(f'https://api.telegram.org/bot{token}/{method}', json=payload)
            value = response.json()
            if response.status_code == 429 and attempt < 2:
                await asyncio.sleep(min(30, value.get('parameters', {}).get('retry_after', 2)))
                continue
            if response.status_code != 200 or not value.get('ok'):
                raise RuntimeError('Telegram request failed; check token, chat permissions, and polling conflict.')
            return value['result']
        raise RuntimeError('Telegram rate limit')

    async def send(self, text, delivery_id=None):
        cfg = self.config()
        if not all(cfg.get(k) for k in ['bot_token', 'user_id', 'chat_id']):
            raise RuntimeError('Telegram requires both user and private-chat allowlists.')
        async with self.send_lock:
            key = 'delivery:' + delivery_id if delivery_id else None
            state = self.db.get(key, {}) if key else {}
            digest = hashlib.sha256(text.encode()).hexdigest()
            if state and state.get('digest') != digest:
                raise RuntimeError('Delivery content changed during retry; manual review required.')
            if state.get('status') == 'uncertain':
                raise RuntimeError('Telegram delivery outcome uncertain; check the chat before retrying manually.')
            sent = state.get('sent_chunks', 0)
            try:
                for index, chunk in enumerate(chunks(text)):
                    if index < sent: continue
                    # If a process/network failure occurs after Telegram accepted a chunk,
                    # avoid blindly duplicating it. Telegram has no sendMessage idempotency key.
                    state = {'digest':digest,'sent_chunks':index,'status':'uncertain','at':utcnow()}
                    if key: self.db.set(key,state)
                    self.db.set('telegram_delivery', {'status':'Sending','at':utcnow(),'delivery_id':delivery_id})
                    result = await self.call('sendMessage', {'chat_id': cfg['chat_id'], 'text': chunk, 'protect_content': True})
                    state.update(sent_chunks=index+1,status='confirmed',at=utcnow(),message_id=result.get('message_id'))
                    if key: self.db.set(key,state)
                self.db.set('telegram_delivery', {'status':'Delivered','at':utcnow(),'delivery_id':delivery_id,'chunks':state.get('sent_chunks',0)})
            except Exception:
                self.db.set('telegram_delivery', {'status':'Failed or uncertain; inspect chat before manual retry','at':utcnow(),'delivery_id':delivery_id})
                raise

    def status_text(self):
        from .scheduling import schedule_status
        ai=self.ops.ai.status(); scheduler=schedule_status(self.db,self.ops.settings()['telegram_briefings'])
        return (f'Mode: {"DEMO" if self.ops.settings()["demo"] else "Live Toast"}\n'
                f'AI authenticated: {ai["connected"]}; fallback: {ai.get("fallback_active")}\n'
                f'Model: {ai.get("model") or "Not selected"}\nLast AI success: {ai.get("last_success") or "None"}\n'
                f'AI error: {(ai.get("error") or {}).get("code", "None")}\n'
                f'Plan pause reason: {(ai.get("pause_reason") or {}).get("code", "None")}\n'
                f'Telegram: private allowlist enabled\nMorning delivery enabled: {scheduler["enabled"]}\n'
                f'Next briefing: {scheduler["next_briefing"]}\nLast delivery: {self.db.get("telegram_delivery")}\n'
                f'Outlook: {self.ops.outlook.status()["status"] if self.ops.outlook else "Not configured"}')

    async def handle(self, update):
        message = update.get('message', {})
        if not allowed(message, self.config()):
            return
        text = message.get('text', '').strip()[:4000]
        if not text:
            return
        key = 'telegram_reply:' + str(update['update_id'])
        writeups = getattr(self.ops,'writeups',None)
        guest_followup=getattr(self.ops,'guest_followup_requested',lambda *args:False)(text,'telegram')
        if writeups and writeups.handles(text,'telegram') and not guest_followup:
            if self.db.get(key) is not None:
                return
            if getattr(self.ops,'dialogue',None):self.ops.dialogue.clear('telegram')
            # Only a marker is persisted in Telegram metadata. Personnel notes
            # and PDFs use the separate encrypted workspace, never chat history.
            result = await writeups.handle(text,'telegram')
            self.db.set(key,{'private_writeup':True})
            await self.send(result['text'],key)
            if result.get('writeup_pdf'):
                await self.send_writeup(result['writeup_pdf'])
            return
        ai_first=bool(getattr(getattr(self.ops,'conversation_planner',None),'enabled',False))
        if (is_guest_lookup(text) or guest_followup) and (not ai_first or text.lstrip().startswith('/')):
            # Persist only a delivery marker, never a query or guest reply.
            # Failed/uncertain delivery requires a fresh owner request.
            if self.db.get(key) is not None:
                return
            reply = (await self.ops.chat(text, 'telegram'))['text']
            self.db.set(key, {'sensitive_guest_lookup': True})
            await self.send(reply, key)
            return
        if text.split()[0] in {'/exceptions', '/source_health'}:
            if allowed(message, self.config()):
                if text.split()[0] == '/source_health':
                    from .source_health import inspect
                    health = inspect(self.db)
                    reply = 'Local source health (no live refresh):\n'+'\n'.join(r['title']+': '+r['status'] for r in health['sources'])
                else:
                    ledger = getattr(self.ops, 'exception_ledger', None)
                    records = ledger.listing()['items'] if ledger else []
                    reply = 'Open exceptions:\n'+('\n'.join(r['severity']+' | '+r['status']+' | '+r['title'] for r in records[:15]) or 'None recorded.')
                    reply += '\nReview/acknowledge in the dashboard Exceptions page. No external actions.'
                await self.send(reply)
            return
        if text.split()[0] in {'/shift', '/followups', '/followup', '/followup_done'}:
            reply = self.db.get(key)
            if reply is None:
                desk = getattr(self.ops, 'shift_desk', None)
                try:
                    if not desk:
                        raise ValueError('Shift Desk is unavailable.')
                    command = text.split()[0]
                    if command in {'/shift', '/followups'}:
                        from .shift_desk import telegram_text
                        reply = telegram_text(desk.view())
                    elif command == '/followup':
                        body = text[len(command):].strip()
                        title, sep, due = body.partition('|')
                        result = desk.add(title.strip(), due.strip() if sep else '')
                        reply = f"Follow-up {result['id']} {'saved' if result['created'] else 'already recorded'}. Use /shift to see it. No board items or external records changed."
                    else:
                        parts = text.split()
                        if len(parts) != 2:
                            raise ValueError('Use /followup_done ID.')
                        desk.complete(parts[1])
                        reply = 'Follow-up marked done. The monday.com board was not changed.'
                except ValueError as error:
                    reply = 'Follow-up unavailable: '+str(error)[:800]
                self.db.set(key, reply)
            await self.send(reply, key)
            return
        if text.split()[0] in {'/task', '/tasks', '/task_status', '/task_approve', '/task_cancel', '/browse'}:
            reply = self.db.get(key)
            if reply is None:
                worker = getattr(self.ops, 'task_worker', None)
                try:
                    if not worker:
                        raise ValueError('Approved task worker is unavailable.')
                    import json
                    from .approved_tasks import proposal_text
                    parts = text.split()
                    command = parts[0]
                    if command == '/tasks':
                        tasks = worker.listing()['tasks']
                        reply = 'Approved tasks\n'+'\n'.join(f"{t['id']} | {t['manifest']['skill']} | {t['state']}" for t in tasks)
                        if not tasks:
                            reply += 'No tasks yet. Use /task coverage_review or /browse HTTPS_URL.'
                    elif command == '/task_status' and len(parts) == 2:
                        task = worker.read(parts[1])
                        reply = f"Task {task['id']}: {task['state']}\n"+json.dumps(task['result'], indent=2, ensure_ascii=False)
                    elif command == '/task_approve' and len(parts) == 3:
                        task = worker.approve(parts[1], parts[2], 'Owner via authorized private Telegram')
                        reply = f"Task {task['id']} approved. The worker will run it within its stated limits. Use /task_status {task['id']} for the saved result."
                    elif command == '/task_cancel' and len(parts) == 2:
                        task = worker.cancel(parts[1])
                        reply = f"Task {task['id']} cancelled before execution."
                    elif command == '/browse' and len(parts) == 2:
                        reply = proposal_text(worker.propose('public_page', {'url': parts[1]}))
                    elif command == '/task' and len(parts) >= 2:
                        skill = parts[1]
                        if skill == 'skills':
                            reply = 'Fixed skill library\n'+'\n'.join(x['id']+': '+x['description'] for x in worker.listing()['skills'])
                        else:
                            args = {} if skill == 'coverage_review' and len(parts) == 2 else {'dates': parts[2:]} if skill == 'pizza_audit' else {'url': parts[2]} if skill == 'public_page' and len(parts) == 3 else None
                            reply = proposal_text(worker.propose(skill, args))
                    else:
                        raise ValueError('Use /task coverage_review, /task pizza_audit YYYY-MM-DD [up to 3 dates], /browse HTTPS_URL, /tasks, /task_status ID, /task_approve ID HASH, or /task_cancel ID.')
                    if len(reply) > 12000:
                        reply = reply[:11800]+'\nResult shortened; open the task in Tony Corner for the full saved result.'
                except (ValueError, TypeError, KeyError) as error:
                    reply = 'Task unavailable: '+str(error)[:1000]
                self.db.set(key, reply)
            await self.send(reply, key)
            return
        if text == '/corner' or text.startswith('/corner '):
            cached = self.db.get(key)
            if cached is not None:
                await self.send(cached, key)
                return
            corner = getattr(self.ops, 'corner', None)
            if not corner:
                reply = "Tony's Corner is unavailable; existing operations remain available."
            else:
                import json
                parts = text.split()
                try:
                    result = corner.query(parts[1] if len(parts)>1 else 'overview', parts[2] if len(parts)>2 else None)
                    reply = "Tony's Corner — read-only research records\n" + json.dumps(result, indent=2, ensure_ascii=False)
                    if len(reply) > 12000:
                        reply = reply[:11800] + '\nResults truncated; use Tony’s Corner in the dashboard or ask for a narrower view.'
                except ValueError:
                    reply = 'Use /corner [overview|journal|predictions|mistakes|hypotheses|events|surprises|improvements] [YYYY-MM-DD].'
            self.db.set(key, reply)
            await self.send(reply, key)
            return
        if text.startswith('/code '):
            if self.db.get(key) is None:
                await self.send('Preparing a code proposal. I will send the complete diff and an exact approval command; no live code is changed yet.', key + ':ack')
                try:
                    proposal, diff = await self.code.propose(text[6:].strip())
                    self.db.set('last_code_proposal', proposal['id'])
                    review = (f"Code proposal {proposal['id']}\n"
                              f"Request: {proposal['request']}\n"
                              f"Files: {', '.join(e['path'] for e in proposal['edits'])}\n"
                              f"SHA-256: {proposal['sha256']}\n"
                              f"Expires in 24 hours. Review every line below before approval.\n\n"
                              f"{diff}\n"
                              f"To apply exactly this patch: /approve {proposal['id']} {proposal['sha256'][:16]}\n"
                              f"To discard it: /reject {proposal['id']}")
                    await self.send(review, 'code_patch:' + proposal['id'])
                    self.code.mark_reviewable(proposal['id'])
                    reply = f"Proposal {proposal['id']} delivered for review. Nothing has been applied."
                except Exception as error:
                    reply = f'Code proposal unavailable: {error}'
                self.db.set(key, reply)
            await self.send(self.db.get(key), key)
            return
        if text.startswith('/approve '):
            if self.db.get(key) is None:
                parts = text.split()
                try:
                    if len(parts) != 3:
                        raise ValueError('Use /approve <proposal ID> <16-character diff hash>.')
                    proposal = await self.code.approve(parts[1], parts[2])
                    reply = (f"Approved proposal {proposal['id']} applied. All tests passed. "
                             "Restarting Tony now; he may be briefly unavailable.")
                    restart = True
                except Exception as error:
                    reply, restart = f'Approval refused: {error}', False
                self.db.set(key, {'text': reply, 'restart': restart})
            state = self.db.get(key)
            await self.send(state['text'], key)
            if state['restart']:
                self.code.restart_after_reply()
            return
        if text.startswith('/reject '):
            if self.db.get(key) is None:
                try:
                    parts = text.split()
                    if len(parts) != 2:
                        raise ValueError('Use /reject <proposal ID>.')
                    proposal = self.code.reject(parts[1])
                    reply = f"Rejected proposal {proposal['id']}; no code was changed."
                except Exception as error:
                    reply = f'Rejection unavailable: {error}'
                self.db.set(key, reply)
            await self.send(self.db.get(key), key)
            return
        if text == '/code_status':
            last = self.db.get('last_code_proposal')
            proposal = self.code.read(last) if last else None
            await self.send((f"Last code proposal: {last} — {proposal['status']}" if proposal else 'No code proposal yet.'), key)
            return
        reply = self.db.get(key)
        if isinstance(reply,dict) and (reply.get('sensitive_guest_lookup') or reply.get('private_writeup')):
            return  # A private semantic-routed reply is never cached/replayed.
        if reply is None:
            if text.split()[0] in ['/start', '/help']:
                reply = "the restaurant Operations\n/sales — current report\n/briefing — today's morning report\n/catchup — changes since your last check-in\n/status — integration status\n/guest name Jane Smith — Toast guest lookup (also email or phone)\n/writeup — dictate a private coaching draft; /writeup pdf returns your form\n/code <request> — prepare a code patch for review\n/code_status — last proposal status\n/approve <ID> <hash> — apply reviewed patch\n/reject <ID> — discard patch\nOr ask an operational question.\nMemory changes and settings are available in the authenticated dashboard."
            elif text == '/status':
                reply = self.status_text()
            elif text == '/briefing':
                reply = telegram_briefing(await self.ops.briefing())
            elif text == '/sales':
                from .operations import factual
                reply = factual(await self.ops.report(), self.db.memories())
            else:
                question = {'/sales': 'How are sales looking today?', '/catchup': 'Anything I missed today?'}.get(text, text)
                result = await self.ops.chat(question, 'telegram')
                reply = result['text']
                if result.get('sensitive'):
                    self.db.set(key,{'private_writeup':True} if result.get('writeup_private') else {'sensitive_guest_lookup':True})
                    await self.send(reply,key)
                    if result.get('writeup_pdf'):
                        await self.send_writeup(result['writeup_pdf'])
                    return
            self.db.set(key, reply)
        await self.send(reply, key)

    async def send_writeup(self, artifact):
        # Explicit /writeup pdf is authorization to return this draft to the
        # existing allowlisted owner chat, not to HR or any other recipient.
        cfg=self.config()
        if not all(cfg.get(k) for k in ('bot_token','user_id','chat_id')):
            raise RuntimeError('Private Telegram configuration required.')
        pdf=self.ops.writeups.pdf(artifact)
        token=cfg['bot_token']
        response=await self.http.post('https://api.telegram.org/bot'+token+'/sendDocument',
            data={'chat_id':str(cfg['chat_id']),'protect_content':'true',
                  'caption':'UNSIGNED DRAFT - manager/HR review required. Not submitted.'},
            files={'document':('Coaching-Draft-Unsigned.pdf',pdf,'application/pdf')})
        # No automatic retries: delivery may have succeeded before a network failure.
        if response.status_code!=200 or not response.json().get('ok'):
            raise RuntimeError('Private draft delivery was not confirmed. Review Telegram before requesting the PDF again.')

    async def run(self):
        while not self.closing:
            self.db.set('telegram_heartbeat', utcnow())
            cfg = self.config()
            if not cfg.get('enabled') or not all(cfg.get(k) for k in ['bot_token', 'user_id', 'chat_id']):
                self.status = 'Disabled'
                await asyncio.sleep(10)
                continue
            try:
                updates = await self.call('getUpdates', {'offset': self.db.get('telegram_offset', 0), 'timeout': 25, 'allowed_updates': ['message']})
                self.status = 'Connected'
                for update in updates:
                    if self.closing: break
                    # Sequential handling + persisted replies prevent duplicated inference on retries.
                    if allowed(update.get('message', {}), self.config()):
                        await asyncio.sleep(max(0, 2-(time.monotonic()-self.last_message)))
                    self.inflight.clear()
                    try:
                        await self.handle(update)
                    finally:
                        self.inflight.set()
                    self.last_message = time.monotonic()
                    self.db.set('telegram_offset', update['update_id'] + 1)
                    self.db.execute("DELETE FROM meta WHERE key LIKE 'telegram_reply:%' AND key != ?", ('telegram_reply:' + str(update['update_id']),))
            except asyncio.CancelledError:
                raise
            except Exception:
                self.status = 'Unavailable — check diagnostics/configuration'
                await asyncio.sleep(15)
