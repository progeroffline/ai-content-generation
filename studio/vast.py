import asyncio
import json
import logging
import math
import time

import aiohttp

log = logging.getLogger('studio.vast')


class VastError(Exception):
    pass


class Vast:
    API = 'https://console.vast.ai'

    def __init__(self, api_key, session, template_id=None, image=None):
        self.api_key = api_key.strip()
        self.session = session
        self.template_id = str(template_id).strip() if template_id else None
        self.image = (image or 'vastai/comfy:@vastai-automatic-tag').strip()

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
            message = data.get('msg') or data.get('message') or data.get('error') or str(data)
            raise VastError(f'Vast.ai HTTP {status}: {str(message)[:1200]}')
        log.info('<- %s %s HTTP %s in %.0f ms', method, path, status, elapsed)
        log.debug('<- body %s', str(data)[:4000])
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
            'geolocation', 'machine_id')}

    @classmethod
    def _offer(cls, item):
        return {key: item.get(key) for key in (
            'id', 'gpu_name', 'num_gpus', 'gpu_ram', 'cpu_ram', 'cpu_cores_effective',
            'disk_space', 'dph_total', 'reliability2', 'inet_down', 'inet_up',
            'geolocation', 'machine_id', 'verification')}

    async def instances(self):
        data = await self.request('GET', '/api/v1/instances')
        rows = data.get('instances', data if isinstance(data, list) else [])
        return [self._instance(row) for row in rows]

    async def offers(self, gpu_names, max_price, disk=200, limit=20):
        allowed = {'RTX 3090', 'RTX 4090', 'RTX 5090', 'A100 SXM4', 'A100 PCIE', 'H100 SXM', 'H100 PCIE'}
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
        }
        data = await self.request('POST', '/api/v0/bundles', json=query)
        rows = data.get('offers', data if isinstance(data, list) else [])
        log.info('offers gpu=%s max_price=%s disk=%s -> %d found', names, price, disk, len(rows))
        return [self._offer(row) for row in rows]

    async def _template(self):
        # The template carries the real image tag, ports and env; resolve Vast's current one unless pinned.
        if self.template_id:
            return self.template_id
        repo = self.image.split(':', 1)[0]
        data = await self.request('GET', '/api/v0/template/', params={
            'select_filters': json.dumps({'recommended': {'eq': True}})})
        for row in data.get('templates', []):
            if row.get('image') == repo and row.get('hash_id'):
                log.info('template resolved for %s: %s (%s:%s)', repo, row['hash_id'], row.get('name'), row.get('tag'))
                return row['hash_id']
        log.warning('no recommended Vast template found for image %s', repo)
        return None

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
        body = {'disk': disk, 'cancel_unavail': True}
        template = await self._template()
        if template:
            body['template_hash_id'] = template
        elif '@' in self.image:
            raise VastError('Не найден шаблон Vast для образа '+self.image+'. Укажите VAST_TEMPLATE_ID.')
        else:
            body.update(image=self.image, onstart='')
        log.info('rent offer=%s body=%s', offer_id, body)
        result = await self.request('PUT', f'/api/v0/asks/{offer_id}/', json=body)
        log.info('rent offer=%s result=%s', offer_id, str(result)[:1200])
        return result
