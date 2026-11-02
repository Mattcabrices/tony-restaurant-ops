"""Bound actual ASGI request bytes before downstream JSON/body parsing."""
import asyncio
from starlette.responses import JSONResponse


class RequestBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        limit = 8_000_000 if scope.get('path') == '/api/monday/import' else 131072
        chunks, size = [], 0
        try:
            # The complete accepted body is bounded, including chunked requests.
            # Never consume any byte beyond the first rejected ASGI frame.
            async with asyncio.timeout(30):
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return
                    if message['type'] != 'http.request':
                        continue
                    chunk = message.get('body', b'')
                    size += len(chunk)
                    if size > limit:
                        return await JSONResponse({'detail': 'Request too large'}, 413)(scope, receive, send)
                    if chunk:
                        chunks.append(chunk)
                    if not message.get('more_body', False):
                        break
        except TimeoutError:
            return await JSONResponse({'detail': 'Request body timeout'}, 408)(scope, receive, send)
        body = b''.join(chunks)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()

        await self.app(scope, replay, send)
