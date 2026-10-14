import asyncio
import json
import logging
import math
import re
import time

import aiohttp

log = logging.getLogger('studio.vast')
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
# Milestones of a SwarmUI server coming up, in log order: (pattern, stage shown to the user).
STAGES = [
    (re.compile(r'studio-provision\].*updating SwarmUI'), 'Автонастройка: обновление SwarmUI'),
    (re.compile(r'studio-provision\].*installing ComfyUI backend'), 'Автонастройка: установка движка'),
    (re.compile(r'studio-provision\].*downloading (\S+)'), 'Автонастройка: загрузка модели'),
    (re.compile(r'studio-provision\].*installing node packs'), 'Автонастройка: установка узлов ComfyUI'),
    (re.compile(r'studio-provision\].*provisioning finished'), 'Автонастройка завершена'),
    (re.compile(r'Rebuilding|Build succeeded'), 'Сборка SwarmUI'),
    (re.compile(r'SwarmUI v[\d.]+ .* is now running'), 'SwarmUI запущен, загружается движок'),
    (re.compile(r'Self-Start ComfyUI-\d+ on port \d+ started'), 'Движок запущен'),
]
SECRET = re.compile(r"('[^']*(?:token|api_key|password)[^']*': )'[^']*'")


class VastError(Exception):
    pass


class Vast:
    API = 'https://console.vast.ai'

    def __init__(self, api_key, session, template_id=None, image=None):
        self.api_key = api_key.strip()
        self.session = session
        self.template_id = str(template_id).strip() if template_id else None
        self.image = (image or 'vastai/comfy:@vastai-automatic-tag').strip()
        self.recommended = None
        self.log_cache = {}
        self.reached = {}

    async def request(self, method, path, **kwargs):
        headers = {'Authorization': 'Bearer '+self.api_key, 'Accept': 'application/json'}
        started = time.monotonic()
        log.debug('-> %s %s %s', method, path, kwargs.get('json') or kwargs.get('params') or '')
        try:
            async with self.session.request(method, self.API+path, headers=headers, **kwargs) as response:
                try:
                    data = await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    data = {'message': (await response.text())[:1200]}
                status = response.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            log.warning('<- %s %s failed in %.0f ms: %r', method, path, (time.monotonic()-started)*1000, error)
            raise VastError(f'Vast.ai недоступен: {error!r}'[:1200])
        elapsed = (time.monotonic()-started)*1000
        rejected = isinstance(data, dict) and data.get('success') is False
        if status >= 400 or rejected:
            log.warning('<- %s %s HTTP %s in %.0f ms: %s', method, path, status, elapsed, str(data)[:2000])
            if data.get('error') == 'resources_unavailable':
                raise VastError('GPU этого сервера сейчас занят другим клиентом Vast. Запуск возможен, когда он освободится: '
                                'повторите позже или арендуйте другой сервер.')
            message = data.get('msg') or data.get('message') or data.get('error') or str(data)
            raise VastError(f'Vast.ai HTTP {status}: {str(message)[:1200]}')
        log.info('<- %s %s HTTP %s in %.0f ms', method, path, status, elapsed)
        log.debug('<- body %s', SECRET.sub(r"\1'***'", str(data))[:4000])
        return data

    @staticmethod
    def _number(value):
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _instance(cls, item):
        return {key: item.get(key) for key in (
            'id', 'actual_status', 'intended_status', 'label', 'gpu_name', 'num_gpus',
            'gpu_ram', 'cpu_ram', 'cpu_cores_effective', 'disk_space', 'dph_total',
            'reliability2', 'public_ipaddr', 'ssh_host', 'ssh_port', 'start_date',
            'geolocation', 'machine_id')} | {'swarm_url': cls._swarm_url(item)}

    @staticmethod
    def _swarm_url(item):
        # SwarmUI of the template is published on 7865 behind the instance token (Caddy);
        # the hash opens its Comfy Workflow tab, where the studio workflows live.
        mapped = (item.get('ports') or {}).get('7865/tcp')
        if item.get('actual_status') == 'running' and mapped and item.get('public_ipaddr') and item.get('jupyter_token'):
            return f"http://{item['public_ipaddr'].strip()}:{mapped[0]['HostPort']}/?token={item['jupyter_token']}#,comfyworkflow"
        return None

    @classmethod
    def _offer(cls, item):
        return {key: item.get(key) for key in (
            'id', 'gpu_name', 'num_gpus', 'gpu_ram', 'cpu_ram', 'cpu_cores_effective',
            'disk_space', 'dph_total', 'reliability2', 'inet_down', 'inet_up',
            'geolocation', 'machine_id', 'verification', 'inet_down_cost', 'inet_up_cost')}

    async def instances(self):
        data = await self.request('GET', '/api/v1/instances')
        rows = data.get('instances', data if isinstance(data, list) else [])
        return [self._instance(row) for row in rows]

    async def offers(self, gpu_names, max_price, disk=200, limit=20):
        allowed = {'RTX 3090', 'RTX 4090', 'RTX 5090', 'RTX A5000', 'A100 SXM4', 'A100 PCIE', 'H100 SXM', 'H100 PCIE'}
        names = [name for name in gpu_names if name in allowed]
        if not names:
            raise ValueError('Выберите хотя бы один поддерживаемый GPU')
        price = self._number(max_price)
        if price is None or price <= 0 or price > 20:
            raise ValueError('Максимальная цена должна быть от 0 до 20 $/ч')
        try:
            disk = int(disk)
            limit = int(limit)
        except (TypeError, ValueError):
            raise ValueError('Диск и лимит должны быть целыми числами')
        if not 50 <= disk <= 2000 or not 1 <= limit <= 50:
            raise ValueError('Диск: 50–2000 ГБ; число предложений: 1–50')
        query = {
            'type': 'ondemand', 'limit': limit, 'order': [['dph_total', 'asc']],
            'allocated_storage': disk, 'rentable': {'eq': True}, 'rented': {'eq': False},
            'verified': {'eq': True}, 'num_gpus': {'eq': 1},
            'gpu_name': {'in': names}, 'dph_total': {'lte': price},
            'reliability2': {'gte': 0.97}, 'disk_space': {'gte': disk},
            # Hugging Face and Civitai downloads are unreliable from there.
            'geolocation': {'notin': ['CN']},
        }
        # Hosts the image cannot run on (e.g. too old CUDA) would start a broken instance.
        query.update(json.loads((await self._recommended() or {}).get('extra_filters') or '{}'))
        data = await self.request('POST', '/api/v0/bundles', json=query)
        rows = data.get('offers', data if isinstance(data, list) else [])
        log.info('offers gpu=%s max_price=%s disk=%s -> %d found', names, price, disk, len(rows))
        return [self._offer(row) for row in rows]

    async def _recommended(self):
        # Vast's current recommended template for our image, cached for an hour.
        if self.recommended and time.monotonic()-self.recommended[0] < 3600:
            return self.recommended[1]
        repo = self.image.split(':', 1)[0]
        data = await self.request('GET', '/api/v0/template/', params={
            'select_filters': json.dumps({'recommended': {'eq': True}})})
        for row in data.get('templates', []):
            if row.get('image') == repo and row.get('hash_id'):
                log.info('template resolved for %s: %s (%s:%s) filters=%s', repo, row['hash_id'],
                         row.get('name'), row.get('tag'), row.get('extra_filters'))
                self.recommended = time.monotonic(), row
                return row
        log.warning('no recommended Vast template found for image %s', repo)
        return None

    async def _template(self):
        # The template carries the real image tag, ports and env; use Vast's current one unless pinned.
        if self.template_id:
            return self.template_id
        return (await self._recommended() or {}).get('hash_id')

    async def _container_logs(self, instance_id):
        # Vast writes the container log to a temporary URL a few seconds after the request.
        cached = self.log_cache.get(instance_id)
        if cached and time.monotonic()-cached[0] < 8:
            return cached[1]
        lines = []
        try:
            # A long tail keeps the stage markers that noisy installers would otherwise push out.
            ticket = await self.request('PUT', f'/api/v0/instances/request_logs/{instance_id}/', json={'tail': '3000'})
            for _ in range(6):
                await asyncio.sleep(1.5)
                async with self.session.get(ticket['result_url']) as response:
                    if response.status == 200:
                        text = ANSI.sub('', await response.text())
                        # Progress bars redraw one line with carriage returns; keep only what is finally shown.
                        lines = [line.rsplit('\r', 1)[-1] for line in text.split('\n')]
                        lines = [line for line in lines if line.strip() and 'No such container' not in line]
                        break
        except (VastError, KeyError, aiohttp.ClientError, asyncio.TimeoutError) as error:
            log.warning('logs for instance %s unavailable: %r', instance_id, error)
            return cached[1] if cached else []
        self.log_cache[instance_id] = time.monotonic(), lines
        return lines

    async def _swarm_ready(self, row):
        # Ask SwarmUI itself (through the instance's token proxy) whether its backend is up.
        mapped = (row.get('ports') or {}).get('7865/tcp')
        if not (mapped and row.get('public_ipaddr') and row.get('jupyter_token')):
            return False
        base = f"http://{row['public_ipaddr'].strip()}:{mapped[0]['HostPort']}/API/"
        headers = {'Authorization': 'Bearer '+row['jupyter_token']}
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with self.session.post(base+'GetNewSession', json={}, headers=headers, timeout=timeout) as r:
                session_id = (await r.json(content_type=None)).get('session_id')
            async with self.session.post(base+'ListBackends', json={'session_id': session_id}, headers=headers, timeout=timeout) as r:
                backends = await r.json(content_type=None)
            return any(b.get('status') == 'running' for b in backends.values() if isinstance(b, dict))
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, AttributeError) as error:
            log.debug('SwarmUI of %s not answering: %r', row.get('id'), error)
            return False

    @staticmethod
    def _stage(row, lines):
        status = row.get('actual_status')
        if row.get('intended_status') == 'stopped' or status in ('exited', 'stopped'):
            return 'Остановлен'
        if status != 'running':
            message = re.sub(r'^#\d+ [\d.]+ ', '', ((row.get('status_msg') or '').strip().splitlines() or [''])[-1])
            return 'Загрузка образа' + (': '+message[:160] if message else '')
        stage, position = 'Контейнер запущен', -1
        for pattern, name in STAGES:
            for index in range(len(lines)-1, position, -1):
                match = pattern.search(lines[index])
                if match:
                    stage, position = name + (' '+match.group(1).split('/')[-1] if match.groups() else ''), index
                    break
        return stage

    async def progress(self, instance_id):
        instance_id = int(instance_id)
        try:
            data = await self.request('GET', f'/api/v0/instances/{instance_id}/')
        except VastError as error:
            # Right after renting Vast may not describe the new instance yet.
            log.info('instance %s not described yet: %s', instance_id, error)
            data = {}
        row = data.get('instances') or {}
        if row and row.get('id') != instance_id:
            raise ValueError('Инстанс не найден среди ваших серверов')
        if not row:
            return {'id': instance_id, 'status': None, 'status_msg': '', 'stage': 'Сервер создаётся', 'ready': False,
                    'swarm_url': None, 'logs': []}
        lines = await self._container_logs(instance_id) if row.get('actual_status') else []
        ready = row.get('actual_status') == 'running' and await self._swarm_ready(row)
        stage = 'Готов к генерации' if ready else self._stage(row, lines)
        if row.get('actual_status') == 'running':
            # Never step back: a marker can still drop out of the log tail.
            rank = self._rank(stage)
            best = self.reached.get(instance_id, (-1, stage))
            if rank < best[0]:
                stage = best[1]
            else:
                self.reached[instance_id] = rank, stage
        else:
            self.reached.pop(instance_id, None)
        return {'id': instance_id, 'status': row.get('actual_status'), 'status_msg': (row.get('status_msg') or '')[-600:],
                'stage': stage, 'ready': ready, 'swarm_url': self._swarm_url(row) if ready else None, 'logs': lines[-400:]}

    @staticmethod
    def _rank(stage):
        if stage == 'Готов к генерации':
            return len(STAGES)
        return next((i for i, (_, name) in enumerate(STAGES) if stage.startswith(name)), -1)

    async def _owned(self, instance_id):
        try:
            instance_id = int(instance_id)
        except (TypeError, ValueError):
            raise ValueError('Некорректный ID инстанса')
        if not any(row.get('id') == instance_id for row in await self.instances()):
            raise ValueError('Инстанс не найден среди ваших серверов')
        return instance_id

    async def set_state(self, instance_id, state):
        if state not in ('running', 'stopped'):
            raise ValueError('Разрешены только running и stopped')
        instance_id = await self._owned(instance_id)
        log.info('set_state instance=%s state=%s', instance_id, state)
        return await self.request('PUT', f'/api/v0/instances/{instance_id}/', json={'state': state})

    async def destroy(self, instance_id):
        instance_id = await self._owned(instance_id)
        log.info('destroy instance=%s', instance_id)
        return await self.request('DELETE', f'/api/v0/instances/{instance_id}/')

    async def rent(self, offer_id, disk):
        try:
            offer_id, disk = int(offer_id), int(disk)
        except (TypeError, ValueError):
            raise ValueError('Некорректный ID предложения или размер диска')
        if not 50 <= disk <= 2000:
            raise ValueError('Диск должен быть от 50 до 2000 ГБ')
        # Without client_id Vast answers no_such_ask for offers that are available.
        body = {'client_id': 'me', 'disk': disk}
        template = await self._template()
        if template:
            body['template_hash_id'] = template
        elif '@' in self.image:
            raise VastError('Не найден шаблон Vast для образа '+self.image+'. Укажите VAST_TEMPLATE_ID.')
        else:
            body.update(image=self.image, onstart='')
        log.info('rent offer=%s body=%s', offer_id, body)
        result = await self.request('PUT', f'/api/v0/asks/{offer_id}/', json=body)
        log.info('rent offer=%s new_contract=%s', offer_id, result.get('new_contract'))
        return result
