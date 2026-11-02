"""Official SIWC public-client OAuth and streamed Responses, with no API-key path."""
import asyncio
import base64
import hashlib
import json
import secrets
import time
import uuid
from .storage import utcnow
from urllib.parse import urlencode

import httpx
import jwt

AUTH = 'https://auth.openai.com'
RESOURCE = 'https://api.openai.com/v1'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
TERMINAL_REFRESH = {'invalid_grant', 'invalid_refresh_token', 'token_expired', 'refresh_token_expired', 'refresh_token_invalidated', 'refresh_token_reused'}


class AIError(Exception):
    def __init__(self, code, status=None, request_id=None, param=None):
        self.code, self.status, self.request_id, self.param = code, status, request_id, param
        super().__init__(code)


class ChatGPT:
    supports_intent_routing = True
    supports_conversation_planning = True

    def __init__(self, vault, db, http=None):
        self.vault, self.db = vault, db
        self.http = http or httpx.AsyncClient(trust_env=False, timeout=90, follow_redirects=False)
        self.lock = asyncio.Lock()
        self.inference_lock = asyncio.Lock()
        self.pending = {}
        self.models_cache = {}
        self.jwks = None
        self.jwks_at = 0
        if not db.get('host_id'):
            db.set('host_id', 'urn:uuid:' + str(uuid.uuid4()))

    def records(self):
        return self.vault.read().get('chatgpt_accounts', {})

    def selected(self):
        return self.records().get(self.db.get('active_account'), {})

    def status(self):
        accounts = self.records()
        active = self.db.get('active_account')
        record = accounts.get(active, {})
        return {'connected': bool(record.get('access_token')), 'plan_enabled': 'chatgpt.tokens.use.direct' in record.get('scopes', []),
                'active': active, 'accounts': [{'id': k, 'label': v.get('email', 'ChatGPT') + ' · ' + k[-8:]} for k, v in accounts.items()],
                'provider': 'ChatGPT subscription (OAuth; no API key)',
                'authenticated': bool(record.get('access_token')),
                'token_state': 'missing' if not record.get('access_token') else 'refresh due' if record.get('expires_at', 0) < time.time()+60 else 'valid',
                'refresh_available': bool(record.get('refresh_token')),
                'expires_at': record.get('expires_at'),
                'last_success': self.db.get('ai_last_success'), 'last_refresh':self.db.get('ai_last_refresh'),
                'last_tool':self.db.get('ai_last_tool'), 'stage': self.db.get('ai_stage'),
                'pause_reason': self.db.get('ai_pause_reason'),
                'fallback_active': not bool(record.get('access_token')) or bool(self.db.get('ai_error')) or bool(self.db.get('ai_paused')),
                'model': self.db.get('model:' + str(active)), 'error': self.db.get('ai_error'),
                'paused': self.db.get('ai_paused', False), 'usage_url': 'https://chatgpt.com/settings/usage'}

    def begin(self, session, port, account=None, reconsent=False):
        record = self.records().get(account, {}) if account else {}
        client = record.get('client_id', 'dynamic_agent_client')
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        redirect = f'http://127.0.0.1:{port}/auth/callback'
        self.pending = {k: v for k, v in self.pending.items() if v['until'] > time.time()}
        self.pending[state] = {'session': session, 'nonce': nonce, 'verifier': verifier, 'redirect': redirect,
                               'client': client, 'subject': record.get('subject'), 'until': time.time() + 600}
        query = dict(client_id=client, ext_agent_host_id=self.db.get('host_id'), response_type='code', redirect_uri=redirect,
                     scope=SCOPES, resource=RESOURCE, state=state, nonce=nonce, code_challenge_method='S256',
                     code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='))
        if client == 'dynamic_agent_client':
            query['agent_name_hint'] = "Tony Restaurant Ops"
        if reconsent:
            query['prompt'] = 'consent'
        return AUTH + '/api/accounts/authorize?' + urlencode(query)

    async def token_request(self, data):
        try:
            response = await self.http.post(AUTH + '/api/accounts/oauth/token', data=data)
            value = response.json()
        except (httpx.HTTPError, ValueError):
            raise AIError('oauth_network_unavailable')
        if response.status_code != 200:
            error = value.get('error', 'oauth_error')
            raise AIError(error.get('code', 'oauth_error') if isinstance(error, dict) else error, response.status_code)
        return value

    async def identity(self, token, client, nonce):
        try:
            header = jwt.get_unverified_header(token)
            if self.jwks is None or time.time() - self.jwks_at > 600 or not any(k.get('kid') == header.get('kid') for k in self.jwks['keys']):
                r = await self.http.get(AUTH + '/.well-known/jwks.json')
                r.raise_for_status()
                self.jwks, self.jwks_at = r.json(), time.time()
            key = next(k for k in self.jwks['keys'] if k.get('kid') == header.get('kid'))
            claims = jwt.decode(token, jwt.PyJWK.from_dict(key).key, algorithms=['RS256'], issuer=AUTH, audience=client,
                                leeway=5, options={'require': ['sub', 'exp', 'iat', 'nonce']})
            if not secrets.compare_digest(claims['nonce'], nonce):
                raise ValueError('nonce')
            return claims
        except Exception:
            raise AIError('identity_validation_failed') from None

    async def callback(self, query, session):
        attempt = self.pending.pop(query.get('state', ''), None)
        if not attempt or attempt['until'] < time.time() or attempt['session'] != session:
            raise AIError('invalid_oauth_state')
        if query.get('error'):
            raise AIError('authorization_declined')
        client = query.get('client_id', attempt['client'])
        if client == 'dynamic_agent_client' or not client or (attempt['client'] != 'dynamic_agent_client' and client != attempt['client']):
            raise AIError('invalid_oauth_client')
        if not query.get('code'):
            raise AIError('missing_authorization_code')
        tokens = await self.token_request(dict(grant_type='authorization_code', client_id=client, code=query['code'],
                                              code_verifier=attempt['verifier'], redirect_uri=attempt['redirect'], resource=RESOURCE))
        identity = await self.identity(tokens.get('id_token', ''), client, attempt['nonce'])
        if attempt['subject'] and identity['sub'] != attempt['subject']:
            raise AIError('account_identity_mismatch')
        async with self.inference_lock, self.lock:
            accounts = self.records()
            record = {'subject': identity['sub'], 'email': identity.get('email', 'ChatGPT'), 'client_id': client}
            record.update(self.token_fields(tokens))
            accounts[client] = record
            self.vault.update(chatgpt_accounts=accounts)
            self.db.set('active_account', client)
            self.db.set('ai_error', None)
            self.db.set('ai_paused', False)
            self.db.set('ai_pause_reason', None)
            self.models_cache.clear()

    @staticmethod
    def token_fields(tokens):
        fields = {k: tokens[k] for k in ['access_token', 'refresh_token', 'id_token', 'earliest_refresh_at'] if k in tokens}
        fields.update(expires_at=time.time() + int(tokens.get('expires_in', 3600)), scopes=tokens.get('scope', '').split())
        return fields

    async def token(self):
        self.db.set('ai_stage', 'authentication')
        async with self.lock:
            client = self.db.get('active_account')
            accounts = self.records()
            record = accounts.get(client, {})
            if not record.get('access_token'):
                raise AIError('sign_in_required')
            if 'chatgpt.tokens.use.direct' not in record.get('scopes', []):
                raise AIError('plan_permission_required')
            if record.get('expires_at', 0) < time.time() + 60:
                if not record.get('refresh_token'):
                    raise AIError('sign_in_required')
                earliest = record.get('earliest_refresh_at', 0)
                if isinstance(earliest, str):
                    from .calculations import parsed
                    earliest = parsed(earliest).timestamp()
                if earliest and time.time() < earliest:
                    if record.get('expires_at', 0) <= time.time():
                        raise AIError('refresh_not_yet_available')
                    return record['access_token']
                try:
                    tokens = await self.token_request(dict(grant_type='refresh_token', client_id=client,
                                                          refresh_token=record['refresh_token'], resource=RESOURCE))
                except AIError as e:
                    if e.code in TERMINAL_REFRESH:
                        for key in ['access_token', 'refresh_token', 'id_token']:
                            record.pop(key, None)
                        self.vault.update(chatgpt_accounts=accounts)
                    raise
                if not tokens.get('access_token') or not tokens.get('refresh_token'):
                    raise AIError('invalid_refresh_response')
                scopes = record['scopes']
                record.update(self.token_fields(tokens))
                if 'scope' not in tokens:
                    record['scopes'] = scopes
                self.vault.update(chatgpt_accounts=accounts)
                self.db.set('ai_last_refresh', utcnow())
            return record['access_token']

    async def models(self):
        token = await self.token()
        self.db.set('ai_stage', 'model discovery')
        client = self.db.get('active_account')
        cached = self.models_cache.get(client)
        if cached and time.time() - cached[0] < 300:
            return cached[1]
        r = await self.http.get(RESOURCE + '/models', headers={'Authorization': 'Bearer ' + token})
        if r.status_code != 200:
            raise self.api_error(r)
        models = [{'slug': m['slug'], 'display_name': m.get('display_name', m['slug'])} for m in r.json().get('models', []) if m.get('visibility') == 'list']
        self.models_cache[client] = (time.time(), models)
        return models

    def api_error(self, response, value=None):
        if value is None:
            try:
                value = response.json()
            except ValueError:
                value = {}
        err = value.get('error', {})
        if not isinstance(err, dict):
            err = {'code': str(err)}
        code = err.get('code') or ('admission_error' if 'detail' in value else 'http_error')
        return AIError(code, response.status_code, response.headers.get('x-request-id'), err.get('param'))

    def record_error(self, error):
        if error.code == 'plan_requests_paused_check_usage':
            # Keep the upstream cause visible while local retries are blocked.
            return
        self.db.set('ai_error', {'code': error.code, 'status': error.status, 'request_id': error.request_id, 'param': error.param,
                                 'stage': self.db.get('ai_stage'), 'at': utcnow()})
        if error.code in {'subscription_sharing_usage_limit_exceeded', 'subscription_sharing_user_not_eligible'}:
            self.db.set('ai_paused', True)
            self.db.set('ai_pause_reason', {'code': error.code, 'status': error.status,
                                             'request_id': error.request_id, 'at': utcnow()})

    async def response(self, inputs, tools, tool_choice=None, *, local_output_chars=None):
        async with self.inference_lock:
            return await self._response(inputs, tools, tool_choice, local_output_chars=local_output_chars)

    async def _response(self, inputs, tools, tool_choice=None, *, local_output_chars=None):
        if local_output_chars is not None and (type(local_output_chars) is not int or not 100 <= local_output_chars <= 12000):
            raise ValueError('Invalid local stream limit.')
        if self.db.get('ai_paused'):
            raise AIError('plan_requests_paused_check_usage')
        token = await self.token()
        models = await self.models()
        selected = self.db.get('model:' + str(self.db.get('active_account')))
        if selected not in [m['slug'] for m in models]:
            if not models:
                raise AIError('no_models_available')
            selected = models[0]['slug']
            self.db.set('model:' + str(self.db.get('active_account')), selected)
        body = dict(model=selected, store=False, stream=True, input=inputs, tools=tools)
        if tool_choice is not None:
            body['tool_choice'] = tool_choice
        completed = None
        items = {}
        output_chars = 0
        self.db.set('ai_stage', 'inference')
        try:
            async with self.http.stream('POST', RESOURCE + '/responses', headers={'Authorization': 'Bearer ' + token}, json=body) as r:
                if r.status_code != 200:
                    await r.aread()
                    raise self.api_error(r)
                data = []
                async for line in r.aiter_lines():
                    if line.startswith('data:'):
                        data.append(line[5:].lstrip())
                    elif not line and data:
                        raw, data = '\n'.join(data), []
                        if raw == '[DONE]':
                            continue
                        event = json.loads(raw)
                        kind = event.get('type')
                        if kind == 'response.output_text.delta' and local_output_chars is not None:
                            output_chars += len(event.get('delta', ''))
                            if output_chars > local_output_chars:
                                # Close this stream; this is not an upstream token or billing cap.
                                raise AIError('local_output_limit')
                        if kind in {'response.failed', 'response.incomplete', 'error'}:
                            error = (event.get('response') or {}).get('error') or event.get('error') or {}
                            raise AIError(error.get('code', kind), r.status_code, r.headers.get('x-request-id'), error.get('param'))
                        if kind == 'response.completed':
                            completed = event['response']
                        elif kind == 'response.output_item.done':
                            items[event['output_index']] = event['item']
        except httpx.HTTPError:
            raise AIError('inference_network_unavailable') from None
        except (ValueError, KeyError, TypeError):
            raise AIError('invalid_inference_stream') from None
        if completed is None:
            raise AIError('stream_interrupted')
        # Subscription streams can omit items from the final response envelope.
        # Only use finished items, and only after the stream reports completion.
        if not completed.get('output') and items:
            completed['output'] = [items[index] for index in sorted(items)]
        if not completed.get('output'):
            raise AIError('empty_response')
        self.db.set('ai_error', None)
        self.db.set('ai_pause_reason', None)
        self.db.set('ai_last_success', utcnow())
        self.db.set('ai_stage', 'ready')
        return completed

    async def signout(self):
        async with self.inference_lock, self.lock:
            accounts = self.records()
            client = self.db.get('active_account')
            record = accounts.get(client, {})
            confirmed = False
            if record.get('refresh_token'):
                for attempt in range(3):
                    try:
                        discovery = await self.http.get(AUTH + '/.well-known/openid-configuration')
                        endpoint = discovery.json()['revocation_endpoint']
                        if not endpoint.startswith(AUTH + '/'):
                            break
                        r = await self.http.post(endpoint, data={'token': record['refresh_token'], 'token_type_hint': 'refresh_token', 'client_id': client})
                        confirmed = r.status_code == 200
                        if confirmed or r.status_code < 500:
                            break
                    except (httpx.HTTPError, ValueError, KeyError):
                        pass
                    await asyncio.sleep(2 ** attempt)
            for key in ['access_token', 'refresh_token', 'id_token']:
                record.pop(key, None)
            self.vault.update(chatgpt_accounts=accounts)
            self.models_cache.clear()
            return confirmed
