import base64
import binascii
import hmac
import logging
import os
import time
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web

from .logs import context_id, setup as setup_logging
from .vast import Vast, VastError

STATIC = Path(__file__).parent/'static'
STATE = web.AppKey('state', dict)
log = logging.getLogger('studio.http')


@web.middleware
async def access(request, handler):
    context_id.set(uuid.uuid4().hex[:8])
    started = time.monotonic()
    status = 499
    log.debug('-> %s %s from %s', request.method, request.path_qs, request.remote)
    try:
        response = await handler(request)
        status = response.status
        return response
    except web.HTTPException as error:
        status = error.status
        raise
    except Exception:
        status = 500
        log.exception('%s %s crashed', request.method, request.path)
        raise
    finally:
        quiet = request.method in ('GET', 'HEAD') and status < 400
        log.log(logging.DEBUG if quiet else logging.INFO, '<- %s %s %s in %.0f ms',
                request.method, request.path, status, (time.monotonic()-started)*1000)


@web.middleware
async def security(request, handler):
    password = request.app[STATE]['password']
    try:
        kind, encoded = request.headers.get('Authorization', '').split(' ', 1)
        user, supplied = base64.b64decode(encoded, validate=True).decode().split(':', 1)
        valid = kind.lower() == 'basic' and hmac.compare_digest(user, 'studio') and hmac.compare_digest(supplied.encode(), password.encode())
    except (ValueError, UnicodeError, binascii.Error):
        valid = False
    if not valid:
        raise web.HTTPUnauthorized(headers={'WWW-Authenticate':'Basic realm="AI Studio", charset="UTF-8"'})
    if request.method not in ('GET','HEAD') and request.headers.get('X-Studio-Request') != '1':
        raise web.HTTPForbidden(text='Missing CSRF header')
    try:
        response = await handler(request)
    except ValueError as e:
        log.warning('%s %s rejected: %s', request.method, request.path, e)
        response = web.json_response({'error':str(e)}, status=400)
    except VastError as e:
        response = web.json_response({'error':str(e)}, status=502)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


async def index(request):
    return web.FileResponse(STATIC/'index.html')


async def asset(request):
    name = request.match_info['name']
    if name not in ('app.js','style.css'):
        raise web.HTTPNotFound()
    return web.FileResponse(STATIC/name)


def vast_client(request):
    client = request.app[STATE].get('vast')
    if not client:
        raise web.HTTPServiceUnavailable(text='{"error":"Vast.ai не настроен на сервере"}', content_type='application/json')
    return client


async def vast_config(request):
    client = request.app[STATE].get('vast')
    return web.json_response({'enabled': bool(client), 'template_configured': bool(client and client.template_id)})


async def vast_instances(request):
    return web.json_response({'instances': await vast_client(request).instances()})


async def vast_offers(request):
    data = await request.json()
    offers = await vast_client(request).offers(data.get('gpu_names', []), data.get('max_price'), data.get('disk', 60))
    return web.json_response({'offers': offers})


async def vast_rent(request):
    data = await request.json()
    result = await vast_client(request).rent(data.get('offer_id'), data.get('disk', 60))
    return web.json_response({'ok': True, 'instance_id': result.get('new_contract')})


async def vast_state(request):
    data = await request.json()
    await vast_client(request).set_state(request.match_info['id'], data.get('state'))
    return web.json_response({'ok': True})


async def vast_destroy(request):
    await vast_client(request).destroy(request.match_info['id'])
    return web.json_response({'ok': True})


async def vast_progress(request):
    return web.json_response(await vast_client(request).progress(request.match_info['id']))


def create_app(password, vast_api_key=None, vast_template_id=None, vast_image=None, vast_image_login=None):
    if len(password) < 16:
        raise ValueError('STUDIO_PASSWORD должен содержать не менее 16 символов')
    app = web.Application(middlewares=[access, security])
    app[STATE] = {'password':password}

    async def lifecycle(app):
        s = app[STATE]
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as session:
            s['vast'] = Vast(vast_api_key,session,vast_template_id,vast_image,vast_image_login) if vast_api_key else None
            log.info('started vast=%s template=%s image=%s', bool(s['vast']), vast_template_id or 'auto',
                     s['vast'].image if s['vast'] else None)
            yield
    app.cleanup_ctx.append(lifecycle)
    app.add_routes([web.get('/',index),web.get('/static/{name}',asset),
        web.get('/api/vast/config',vast_config),web.get('/api/vast/instances',vast_instances),
        web.post('/api/vast/offers',vast_offers),web.post('/api/vast/rent',vast_rent),
        web.get('/api/vast/instances/{id}/progress',vast_progress),
        web.post('/api/vast/instances/{id}/state',vast_state),
        web.delete('/api/vast/instances/{id}',vast_destroy)])
    return app


if __name__ == '__main__':
    setup_logging()
    app = create_app(os.getenv('STUDIO_PASSWORD',''),vast_api_key=os.getenv('VAST_API_KEY'),
        vast_template_id=os.getenv('VAST_TEMPLATE_ID'),vast_image=os.getenv('VAST_IMAGE'),
        vast_image_login=os.getenv('DOCKERHUB_TOKEN') and f"-u {os.getenv('DOCKERHUB_USER')} -p {os.getenv('DOCKERHUB_TOKEN')} docker.io")
    web.run_app(app,host=os.getenv('STUDIO_HOST','127.0.0.1'),port=int(os.getenv('STUDIO_PORT','8080')),access_log=None)
