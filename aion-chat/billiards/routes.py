from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .companionship import chat, names, spoken_text
from .engine import available
from .service import get_service, potted_balls
from .matches import match_info, is_active, has_history, summary, standings

router = APIRouter()
STATIC = Path(__file__).with_name('static')


class BilliardsAssets(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        # A new lobby must never silently reuse the previous room controller.
        if Path(path).suffix in {'.js', '.css'}:
            response.headers['Cache-Control'] = 'no-cache'
        return response


class NewGame(BaseModel):
    opponent: Literal['aion', 'connor']
    difficulty: Literal['low', 'medium', 'high'] = 'medium'
    accuracy: float | None = Field(default=None, ge=0, le=100)
    mode: Literal['casual', 'strict'] = 'casual'


class Window(BaseModel):
    client: str = Field(default='', max_length=100)


class WatchGame(BaseModel):
    difficulty: Literal['low', 'medium', 'high'] = 'medium'


class Action(Window):
    revision: int
    action_id: str = Field(min_length=1, max_length=100)
    action: dict


class NextRack(Window):
    difficulty: Literal['low', 'medium', 'high'] | None = None
    accuracy: float | None = Field(default=None, ge=0, le=100)
    mode: Literal['casual', 'strict'] | None = None
    breaker: Literal[0, 1] = 0


class TableChat(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    target: str = Field(default='', max_length=20)


def public(game, client=''):
    import time
    result = {k: v for k, v in game.items() if k not in ('actions', 'pending', 'lease')}
    result['names'] = names()
    result['match'] = match_info(game)
    result['potted_balls'] = potted_balls(game)
    # Clean older table messages on read as well, without rewriting their saves.
    result['messages'] = []
    for message in game.get('messages', []):
        text = message['text'] if message['sender'] == 'user' else spoken_text(message['text'])
        if text:
            result['messages'].append({**message, 'text': text})
    lease = game.get('lease') or {}
    result['controlled_here'] = bool(client and lease.get('client') == client and lease.get('until', 0) > time.time())
    result['busy_ms'] = max(0, int((game['ready_at'] - time.time()) * 1000))
    if game.get('ai_preview'):
        result['ai_preview'] = {**game['ai_preview'],
            'elapsed_ms': max(0, int((time.time() - game['ai_preview']['started_at']) * 1000))}
    return result


async def guarded(awaitable):
    try:
        return await awaitable
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception as exc:
        print(f'[billiards] API: {type(exc).__name__}')
        raise HTTPException(503, '台球室暂时不可用，其他玩法可以正常使用') from None


@router.get('/billiards')
async def page():
    return FileResponse(STATIC / 'index.html', headers={'Cache-Control': 'no-store'})


@router.get('/api/billiards')
async def lobby():
    service = get_service()
    await guarded(service.start())
    games = service.store.all()
    return {'available': available(), 'names': names(),
            'games': [summary(g) for g in games if is_active(g)],
            'standings': standings(games), 'history_count': sum(has_history(g) for g in games)}


@router.get('/api/billiards/history')
async def history(offset: int = Query(default=0, ge=0), limit: int = Query(default=10, ge=1, le=30)):
    games = [g for g in get_service().store.all() if has_history(g)]
    return {'games': [summary(g) for g in games[offset:offset+limit]],
            'total': len(games), 'has_more': offset + limit < len(games)}


@router.post('/api/billiards')
async def create(body: NewGame):
    service = get_service()
    await guarded(service.start())
    game = await guarded(service.create(['user', body.opponent], difficulty=body.difficulty, accuracy=body.accuracy, mode=body.mode))
    return public(game)


@router.post('/api/billiards/watch')
async def watch(body: WatchGame):
    service = get_service()
    await guarded(service.start())
    return public(await guarded(service.watch(difficulty=body.difficulty)))


@router.get('/api/billiards/{sid}')
async def state(sid: str, client: str = Query(default='', max_length=100)):
    service = get_service()
    try:
        return public(service.get(sid), client)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


@router.post('/api/billiards/{sid}/resume')
async def resume(sid: str, body: Window):
    return public(await guarded(get_service().resume(sid, body.client)), body.client)


@router.post('/api/billiards/{sid}/pause')
async def pause(sid: str, body: Window):
    return public(await guarded(get_service().pause(sid, body.client)), body.client)


@router.post('/api/billiards/{sid}/heartbeat')
async def heartbeat(sid: str, body: Window):
    return public(await guarded(get_service().heartbeat(sid, body.client)), body.client)


@router.post('/api/billiards/{sid}/action')
async def act(sid: str, body: Action):
    return public(await guarded(get_service().act(sid, body.client, body.revision, body.action_id, body.action)), body.client)


@router.post('/api/billiards/{sid}/next')
async def next_rack(sid: str, body: NextRack):
    return public(await guarded(get_service().next_rack(sid, body.client, difficulty=body.difficulty, accuracy=body.accuracy, mode=body.mode, breaker=body.breaker)), body.client)


@router.post('/api/billiards/{sid}/finish')
async def finish(sid: str, body: Window):
    return public(await guarded(get_service().finish(sid, body.client)), body.client)


@router.post('/api/billiards/{sid}/restart')
async def restart(sid: str, body: NextRack):
    return public(await guarded(get_service().restart_rack(sid, body.client, difficulty=body.difficulty,
                  accuracy=body.accuracy, mode=body.mode, breaker=body.breaker)), body.client)


@router.post('/api/billiards/{sid}/chat')
async def table_chat(sid: str, body: TableChat):
    await guarded(chat(get_service(), sid, body.text, body.target))
    return public(get_service().get(sid))


def install(app):
    app.include_router(router)
    app.mount('/billiards-assets', BilliardsAssets(directory=str(STATIC)), name='billiards-assets')
