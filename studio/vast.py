import math

import aiohttp


class VastError(Exception):
    pass


class Vast:
    API = 'https://console.vast.ai'

    def __init__(self, api_key, session, template_id=None, image=None):
        self.api_key = api_key.strip()
        self.session = session
        self.template_id = str(template_id).strip() if template_id else None
        self.image = (image or 'vastai/comfyui:@vastai-automatic-tag').strip()

    async def request(self, method, path, **kwargs):
        headers = {'Authorization': 'Bearer '+self.api_key, 'Accept': 'application/json'}
        async with self.session.request(method, self.API+path, headers=headers, **kwargs) as response:
            try:
                data = await response.json(content_type=None)
            except (ValueError, aiohttp.ContentTypeError):
                data = {'message': (await response.text())[:1200]}
            if response.status >= 400:
                message = data.get('msg') or data.get('message') or data.get('error') or str(data)
                raise VastError(f'Vast.ai HTTP {response.status}: {str(message)[:1200]}')
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
        return [self._offer(row) for row in rows]

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
        return await self.request('PUT', f'/api/v0/instances/{instance_id}/', json={'state': state})

    async def destroy(self, instance_id):
        instance_id = await self._owned(instance_id)
        return await self.request('DELETE', f'/api/v0/instances/{instance_id}/')

    async def rent(self, offer_id, disk):
        try:
            offer_id, disk = int(offer_id), int(disk)
        except (TypeError, ValueError):
            raise ValueError('Некорректный ID предложения или размер диска')
        if not 50 <= disk <= 2000:
            raise ValueError('Диск должен быть от 50 до 2000 ГБ')
        body = {'disk': disk, 'image': self.image, 'onstart': ''}
        if self.template_id:
            body['template_id'] = self.template_id
        return await self.request('PUT', f'/api/v0/asks/{offer_id}/', json=body)
