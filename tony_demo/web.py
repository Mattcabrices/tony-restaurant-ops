"""Loopback-only demo interface. Synthetic read endpoints, no credentials or writes."""
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .summary import build_summary


def create_app():
    app=FastAPI(title='Tony | Restaurant Operations Portfolio Demo',version='0.1.0',docs_url=None,redoc_url=None)
    static=Path(__file__).with_name('static')
    app.mount('/static',StaticFiles(directory=static),name='static')

    @app.get('/')
    def homepage():return FileResponse(static/'index.html')

    @app.get('/api/demo')
    def summary():return build_summary()

    @app.get('/docs',include_in_schema=False)
    def reference():return FileResponse(static/'api.html')

    @app.get('/healthz')
    def health():return {'status':'ok','synthetic':True}

    @app.middleware('http')
    async def headers(request,call_next):
        response=await call_next(request)
        response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"})
        return response
    return app
