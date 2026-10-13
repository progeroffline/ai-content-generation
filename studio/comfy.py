import asyncio
import hashlib
import json
import mimetypes
import time
from pathlib import PurePosixPath

import aiohttp

from .workflows import build_graph


class GenerationError(Exception):
    pass


class Comfy:
    def __init__(self, url, session, store, authorization=None):
        self.url = url.rstrip('/')
        self.session = session
        self.store = store
        self.headers = {'Authorization': authorization} if authorization else {}

    async def request(self, method, path, **kwargs):
        headers = {**self.headers, **kwargs.pop('headers', {})}
        async with self.session.request(method, self.url+path, headers=headers, **kwargs) as r:
            if r.status >= 400:
                body = await r.text()
                raise GenerationError(f'ComfyUI HTTP {r.status}: {body[:1200]}')
            return await r.json()

    async def ready(self, graph):
        info = await self.request('GET', '/object_info')
        for n in graph.values():
            spec = info.get(n['class_type'])
            if not spec:
                raise GenerationError('Нет узла ComfyUI: '+n['class_type'])
            for field in ('unet_name', 'clip_name', 'vae_name'):
                if field in n['inputs']:
                    choices = spec.get('input', {}).get('required', {}).get(field, [[]])[0]
                    if n['inputs'][field] not in choices:
                        raise GenerationError('Не установлен файл модели: '+n['inputs'][field])

    async def upload(self, file):
        data = aiohttp.FormData()
        with self.store.file_path(file).open('rb') as stream:
            data.add_field('image', stream, filename=file['name'], content_type=file['mime'])
            data.add_field('overwrite', 'false')
            r = await self.request('POST', '/upload/image', data=data)
        return '/'.join(filter(None, [r.get('subfolder'), r['name']]))

    async def submit(self, job):
        params = job['params']
        source = None
        if params['mode'] == 'image_video':
            source = await self.upload(self.store.file(params['source_id']))
        graph = build_graph(params, job['id'], source)
        await self.ready(graph)
        (self.store.root/'workflows'/(job['id']+'.json')).write_text(json.dumps(graph, ensure_ascii=False, indent=2))
        # Persist before POST. Ambiguous submission is never automatically repeated.
        self.store.update(job['id'], status='submitting', stage='Отправка в ComfyUI')
        r = await self.request('POST', '/prompt', json={'prompt': graph, 'client_id': job['id'],
                               'extra_data': {'studio_job_id': job['id']}})
        if not r.get('prompt_id') or r.get('error'):
            raise GenerationError('ComfyUI отклонил workflow: '+str(r)[:1200])
        self.store.update(job['id'], prompt_id=r['prompt_id'], status='running', stage='Ожидание ComfyUI')
        return r['prompt_id']

    async def events(self, job, ws):
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            payload = json.loads(msg.data)
            data = payload.get('data', {})
            current = self.store.job(job['id'])
            if data.get('prompt_id') != current['prompt_id']:
                continue
            kind = payload.get('type')
            if kind == 'progress' and data.get('max', 0) > 0:
                value, maximum = data['value'], data['max']
                self.store.update(job['id'], progress=min(100, max(0, int(value/maximum*100))),
                                  stage=f'Текущий этап: {value} / {maximum}')
            elif kind == 'executing' and data.get('node'):
                self.store.update(job['id'], progress=None, stage='Обработка • узел '+str(data['node']))
            elif kind in ('execution_error', 'execution_interrupted'):
                self.store.update(job['id'], status='failed', progress=None, stage='Ошибка генерации',
                                  error=str(data.get('exception_message', 'Генерация прервана'))[:1200])

    async def collect(self, job, history):
        status = history.get('status', {})
        if status.get('status_str') == 'error':
            raise GenerationError('ComfyUI: '+str(status.get('messages', 'Ошибка выполнения'))[:1200])
        if not status.get('completed'):
            return False
        found = 0
        for output in history.get('outputs', {}).values():
            for field in ('images', 'videos', 'gifs'):
                for item in output.get(field, []):
                    if item.get('type', 'output') != 'output':
                        continue
                    name = item.get('filename', '')
                    ext = PurePosixPath(name).suffix.lower()
                    if ext not in ('.png','.jpg','.jpeg','.webp','.mp4','.webm'):
                        continue
                    file_id = hashlib.sha256((job['id']+'|'+item.get('subfolder','')+'|'+name).encode()).hexdigest()[:32]
                    local_name = file_id+ext
                    dest = self.store.root/'files'/local_name
                    if not self.store.file(file_id) or not dest.exists():
                        temp = dest.with_suffix(ext+'.part')
                        async with self.session.get(self.url+'/view', headers=self.headers, params={
                            'filename': name, 'subfolder': item.get('subfolder',''), 'type':'output'},
                            timeout=aiohttp.ClientTimeout(total=600)) as r:
                            if r.status != 200:
                                raise GenerationError(f'Не удалось скачать результат из ComfyUI: HTTP {r.status}')
                            try:
                                with temp.open('wb') as f:
                                    async for chunk in r.content.iter_chunked(1024*1024):
                                        f.write(chunk)
                                if temp.stat().st_size == 0:
                                    raise GenerationError('ComfyUI вернул пустой файл')
                                temp.replace(dest)
                            finally:
                                temp.unlink(missing_ok=True)
                        self.store.add_file(local_name, mimetypes.guess_type(local_name)[0] or 'application/octet-stream', job['id'], file_id)
                    found += 1
        if not found:
            raise GenerationError('Workflow завершён без поддерживаемых файлов результата')
        self.store.update(job['id'], status='completed', stage='Готово', progress=100, error=None)
        return True

    async def run(self, job):
        # Connect before submission to avoid losing the first progress messages.
        ws = await self.session.ws_connect(self.url+'/ws', headers=self.headers,
                                           params={'clientId':job['id']}, heartbeat=20)
        listener = asyncio.create_task(self.events(job, ws))
        try:
            pid = job['prompt_id'] or await self.submit(job)
            missing_since = None
            while True:
                current = self.store.job(job['id'])
                if current['status'] == 'failed':
                    return
                history = await self.request('GET', '/history/'+pid)
                if pid in history and await self.collect(current, history[pid]):
                    return
                queue = await self.request('GET', '/queue')
                present = any(len(q)>1 and q[1] == pid for key in ('queue_running','queue_pending') for q in queue.get(key, []))
                if present or pid in history:
                    missing_since = None
                elif missing_since is None:
                    missing_since = time.monotonic()
                elif time.monotonic()-missing_since > 60:
                    raise GenerationError('Задание исчезло из ComfyUI. Возможно, GPU-сервер был перезапущен. Автоповтор отключён.')
                if listener.done():
                    listener.result()
                    raise aiohttp.ClientConnectionError('WebSocket disconnected')
                await asyncio.sleep(2)
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
            await ws.close()


async def worker(store, comfy):
    while True:
        job = store.next_job()
        if not job:
            await asyncio.sleep(1)
            continue
        if job['status'] == 'submitting' and not job['prompt_id']:
            store.update(job['id'], status='failed', stage='Нужно проверить ComfyUI',
                         error='Ответ на отправку потерян. Автоповтор отключён, чтобы не запустить генерацию дважды.')
            continue
        try:
            await comfy.run(job)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            store.update(job['id'], stage='Нет связи с ComfyUI • переподключение', progress=None)
            await asyncio.sleep(5)
        except (GenerationError, ValueError, KeyError, OSError) as e:
            store.update(job['id'], status='failed', stage='Ошибка', progress=None, error=str(e)[:1200])
