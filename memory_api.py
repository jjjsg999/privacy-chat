"""Retained Memory route contracts. No persistence or automatic processing."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix='/api/memory')

def empty():
    return {'status': 'NOT_IMPLEMENTED', 'message': 'Not yet implemented',
            'revision': 0, 'actions': [], 'projects': [], 'project': None,
            'core': {}, 'context': {}, 'conflicts': [], 'profile_revision': 0,
            'has_baseline': False, 'can_match_subject': False, 'enabled': False}

@router.get('/actions')
@router.get('/projects')
@router.get('/projects/{identifier}')
@router.get('/role')
@router.get('/status')
@router.get('/history-preview')
async def read_memory(identifier: str = ''):
    return empty()

@router.post('/actions')
@router.patch('/actions/{identifier}')
@router.delete('/actions/{identifier}')
@router.patch('/projects/{identifier}')
@router.put('/role')
@router.put('/settings')
@router.post('/refresh')
async def retired_mutation(identifier: str = ''):
    return JSONResponse({'detail': {'code': 'NOT_IMPLEMENTED',
                                    'message': 'Not yet implemented'}}, status_code=501)
