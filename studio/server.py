import asyncio
import base64
import binascii
import hmac
import fcntl
import io
import os
import time
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web
from PIL import Image, UnidentifiedImageError

from .comfy import Comfy, worker
from .store import Store
from .vast import Vast, VastError
from .workflows import validate

STATIC = Path(__file__).parent/'static'
Image.MAX_IMAGE_PIXELS = 20_000_000
STATE = web.AppKey('state', dict)


@web.middleware
async def security(request, handler):
    state = request.app[STATE]
    password = state['password']
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
        response = web.json_response({'error':str(e)}, status=400)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' blob:; media-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


async def index(request):
    return web.FileResponse(STATIC/'index.html')


async def asset(request):
    name = request.match_info['name']
    if name not in ('app.js','style.css','gpu.css'):
        raise web.HTTPNotFound()
    return web.FileResponse(STATIC/name)


async def status(request):
    s = request.app[STATE]
    try:
        stats = await s['comfy'].request('GET','/system_stats')
        return web.json_response({'online':True, 'devices':[d.get('name') for d in stats.get('devices',[])],
                                  'time':time.time()})
    except Exception:
        return web.json_response({'online':False, 'devices':[], 'time':time.time()})


async def jobs(request):
    return web.json_response({'jobs':request.app[STATE]['store'].jobs()})


async def create_job(request):
    store = request.app[STATE]['store']
    key = request.headers.get('Idempotency-Key','')
    try:
        uuid.UUID(key)
    except ValueError:
        raise ValueError('Требуется Idempotency-Key (UUID)')
    data = await request.json()
    params = validate(data)
    old = store.db.execute('SELECT id FROM jobs WHERE request_key=?',(key,)).fetchone()
    if old:
        previous = store.job(old['id'])
        comparable = dict(params)
        if data.get('seed') is None:
            comparable['seed'] = previous['params']['seed']
        if comparable != previous['params']:
            return web.json_response({'error':'Ключ уже использован с другими параметрами'},status=409)
        return web.json_response(previous)
    if params['mode'] == 'image_video':
        source = store.file(params['source_id'])
        if not source or not source['mime'].startswith('image/') or not store.file_path(source).is_file():
            raise ValueError('Исходное изображение не найдено')
    count = store.db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','submitting','running')").fetchone()[0]
    if count >= 10:
        return web.json_response({'error':'В очереди уже 10 заданий'},status=429)
    return web.json_response(store.create(params,key),status=201)


async def upload(request):
    if request.content_type not in ('image/png','image/jpeg','image/webp'):
        raise ValueError('Разрешены PNG, JPEG и WebP')
    raw = await request.read()
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.format not in ('PNG','JPEG','WEBP') or im.width*im.height > 20_000_000:
                raise ValueError('Неподдерживаемое или слишком большое изображение')
            im.load()
            out = io.BytesIO()
            im.convert('RGB').save(out, format='PNG')
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError('Не удалось прочитать изображение')
    store = request.app[STATE]['store']
    id = uuid.uuid4().hex
    name = id+'.png'
    dest = store.root/'files'/name
    dest.write_bytes(out.getvalue())
    store.add_file(name,'image/png',file_id=id)
    return web.json_response({'id':id,'url':'/api/files/'+id},status=201)


async def files(request):
    store = request.app[STATE]['store']
    file = store.file(request.match_info['id'])
    if not file or not store.file_path(file).is_file():
        raise web.HTTPNotFound()
    headers = {'Content-Type':file['mime']}
    if request.query.get('download') == '1':
        headers['Content-Disposition'] = 'attachment; filename="'+file['name']+'"'
    return web.FileResponse(store.file_path(file),headers=headers)


async def workflow_file(request):
    store = request.app[STATE]['store']
    job = store.job(request.match_info['id'])
    if not job:
        raise web.HTTPNotFound()
    path = store.root/'workflows'/(job['id']+'.json')
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path,headers={'Content-Disposition':'attachment; filename="workflow.json"'})


def vast_client(request):
    client = request.app[STATE].get('vast')
    if not client:
        raise web.HTTPServiceUnavailable(text='{"error":"Vast.ai не настроен на сервере"}', content_type='application/json')
    return client


async def vast_config(request):
    client = request.app[STATE].get('vast')
    return web.json_response({'enabled': bool(client), 'template_configured': bool(client and client.template_id)})


async def vast_instances(request):
    try:
        return web.json_response({'instances': await vast_client(request).instances()})
    except VastError as error:
        return web.json_response({'error': str(error)}, status=502)


async def vast_offers(request):
    data = await request.json()
    try:
        offers = await vast_client(request).offers(data.get('gpu_names', []), data.get('max_price'), data.get('disk', 200))
        return web.json_response({'offers': offers})
    except VastError as error:
        return web.json_response({'error': str(error)}, status=502)


async def vast_rent(request):
    data = await request.json()
    offer_id = data.get('offer_id')
    if data.get('confirm') != f'RENT {offer_id}':
        raise ValueError(f'Для аренды требуется подтверждение RENT {offer_id}')
    try:
        result = await vast_client(request).rent(offer_id, data.get('disk', 200))
        return web.json_response({'ok': True, 'result': result})
    except VastError as error:
        return web.json_response({'error': str(error)}, status=502)


async def vast_state(request):
    data = await request.json()
    try:
        result = await vast_client(request).set_state(request.match_info['id'], data.get('state'))
        return web.json_response({'ok': True, 'result': result})
    except VastError as error:
        return web.json_response({'error': str(error)}, status=502)


async def vast_destroy(request):
    instance_id = request.match_info['id']
    data = await request.json()
    if data.get('confirm') != f'DESTROY {instance_id}':
        raise ValueError(f'Для удаления требуется подтверждение DESTROY {instance_id}')
    try:
        result = await vast_client(request).destroy(instance_id)
        return web.json_response({'ok': True, 'result': result})
    except VastError as error:
        return web.json_response({'error': str(error)}, status=502)


def create_app(root, comfy_url, password, start_worker=True, vast_api_key=None,
               vast_template_id=None, vast_image=None, comfy_authorization=None):
    if len(password) < 16:
        raise ValueError('STUDIO_PASSWORD должен содержать не менее 16 символов')
    app = web.Application(middlewares=[security], client_max_size=20*1024*1024)
    app[STATE] = {'store':Store(root),'password':password}

    async def lifecycle(app):
        s = app[STATE]
        lock = (s['store'].root/'worker.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            s['store'].db.close()
            raise RuntimeError('Другой процесс Studio уже использует эту папку данных')
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session, \
                   aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as vast_session:
            s['comfy'] = Comfy(comfy_url,session,s['store'],comfy_authorization)
            s['vast'] = Vast(vast_api_key,vast_session,vast_template_id,vast_image) if vast_api_key else None
            task = asyncio.create_task(worker(s['store'],s['comfy'])) if start_worker else None
            try:
                yield
            finally:
                if task:
                    task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
                s['store'].db.close()
                lock.close()
    app.cleanup_ctx.append(lifecycle)
    app.add_routes([web.get('/',index),web.get('/static/{name}',asset),web.get('/api/status',status),
        web.get('/api/jobs',jobs),web.post('/api/jobs',create_job),web.post('/api/uploads',upload),
        web.get('/api/files/{id}',files),web.get('/api/jobs/{id}/workflow',workflow_file),
        web.get('/api/vast/config',vast_config),web.get('/api/vast/instances',vast_instances),
        web.post('/api/vast/offers',vast_offers),web.post('/api/vast/rent',vast_rent),
        web.post('/api/vast/instances/{id}/state',vast_state),
        web.delete('/api/vast/instances/{id}',vast_destroy)])
    return app


if __name__ == '__main__':
    app = create_app(os.getenv('STUDIO_DATA','./data'),os.getenv('COMFY_URL','http://127.0.0.1:8188'),
        os.getenv('STUDIO_PASSWORD',''),vast_api_key=os.getenv('VAST_API_KEY'),
        vast_template_id=os.getenv('VAST_TEMPLATE_ID'),vast_image=os.getenv('VAST_IMAGE'),
        comfy_authorization=os.getenv('COMFY_AUTHORIZATION'))
    web.run_app(app,host=os.getenv('STUDIO_HOST','127.0.0.1'),port=int(os.getenv('STUDIO_PORT','8080')),access_log=None)
