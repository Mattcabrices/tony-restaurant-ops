"""Authenticated by the existing application's host/session/CSRF middleware."""
from fastapi import APIRouter
from .operator_contracts import OperatorQuery, QUERY_ADAPTER


def operator_router(service):
    router=APIRouter(prefix='/api/operator',tags=['Operator workspace'])

    @router.get('/capabilities')
    def capabilities(): return service.capabilities()

    @router.get('/schema')
    def schema(): return {'schema_version':'tony.operator.v1','request':QUERY_ADAPTER.json_schema()}

    @router.post('/query')
    async def query(body: OperatorQuery):
        return await service.execute(body.model_dump(mode='json'))

    @router.get('/queries')
    def recent_queries():
        return {'schema_version':'tony.operator.v1','queries':service.ops.db.rows(
            'SELECT request_id,kind,started_at,finished_at,status,elapsed_ms,error_code FROM operator_queries_v1 ORDER BY started_at DESC LIMIT 30')}

    return router
