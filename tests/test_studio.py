import base64
import unittest

from aiohttp.test_utils import TestClient, TestServer

from studio.server import create_app, STATE
from studio.vast import Vast, VastError

PASSWORD = 'test-password-never-deploy'
AUTH = {'Authorization':'Basic '+base64.b64encode(('studio:'+PASSWORD).encode()).decode(),
        'X-Studio-Request':'1'}


class FakeVast:
    template_id = 'test-template'

    def __init__(self):
        self.actions = []

    async def instances(self):
        return [{'id': 42, 'gpu_name': 'RTX 3090', 'actual_status': 'running', 'dph_total': .2}]

    async def offers(self, names, price, disk):
        self.actions.append(('offers', names, price, disk))
        return [{'id': 99, 'gpu_name': 'RTX 3090', 'dph_total': .2}]

    async def rent(self, offer_id, disk):
        self.actions.append(('rent', int(offer_id), int(disk)))
        return {'new_contract': 84}

    async def set_state(self, instance_id, state):
        self.actions.append(('state', int(instance_id), state))

    async def destroy(self, instance_id):
        self.actions.append(('destroy', int(instance_id)))

    async def progress(self, instance_id):
        return {'id': int(instance_id), 'stage': 'Готов к генерации', 'ready': True, 'logs': ['line']}


class VastPayloadTests(unittest.IsolatedAsyncioTestCase):
    class RecordedVast(Vast):
        templates = [{'image': 'other/image', 'hash_id': 'wrong'}, {'image': 'vastai/comfy', 'hash_id': 'auto-hash'}]

        async def request(self, method, path, **kwargs):
            if method == 'GET':
                return {'templates': self.templates}
            self.sent = method, path, kwargs['json']
            return {'new_contract': 84}

    async def test_rent_uses_pinned_template(self):
        vast = self.RecordedVast('test-key', None, template_id='template-hash')
        await vast.rent(99, 200)
        self.assertEqual(vast.sent, ('PUT', '/api/v0/asks/99/', {
            'client_id': 'me', 'disk': 200, 'template_hash_id': 'template-hash'}))

    def test_instance_exposes_swarm_link_only_when_running(self):
        row = {'actual_status': 'running', 'public_ipaddr': '3.3.3.3 ', 'jupyter_token': 'tok',
               'ports': {'7865/tcp': [{'HostPort': '5000'}]}}
        self.assertEqual(Vast._instance(row)['swarm_url'], 'http://3.3.3.3:5000/?token=tok#,comfyworkflow')
        self.assertIsNone(Vast._instance({**row, 'actual_status': 'loading'})['swarm_url'])

    async def test_rent_resolves_recommended_template_for_image(self):
        vast = self.RecordedVast('test-key', None)
        await vast.rent(99, 200)
        self.assertEqual(vast.sent[2]['template_hash_id'], 'auto-hash')
        self.assertNotIn('image', vast.sent[2])

    async def test_rent_refuses_placeholder_tag_without_template(self):
        vast = self.RecordedVast('test-key', None)
        vast.templates = []
        with self.assertRaises(VastError):
            await vast.rent(99, 200)

    def test_stage_follows_latest_milestone_in_log(self):
        running = {'actual_status': 'running', 'intended_status': 'running'}
        log = ['[studio-provision] 08:58:55 updating SwarmUI',
               '[studio-provision] 08:58:56 downloading Models/Stable-Diffusion/model.safetensors',
               '[studio-provision] 09:03:54 provisioning finished',
               'Rebuilding...']
        self.assertEqual(Vast._stage(running, log), 'Сборка SwarmUI')
        self.assertEqual(Vast._stage(running, log[:2]), 'Автонастройка: загрузка модели model.safetensors')
        self.assertEqual(Vast._stage(running, []), 'Контейнер запущен')
        self.assertEqual(Vast._stage({'actual_status': 'loading', 'status_msg': 'a\nPull complete'}, []),
                         'Загрузка образа: Pull complete')
        self.assertEqual(Vast._stage({'actual_status': 'exited', 'intended_status': 'stopped'}, log), 'Остановлен')
        self.assertEqual(Vast._stage({'actual_status': 'loading', 'status_msg': '#12 0.529 Installing IPython kernel spec'}, []),
                         'Загрузка образа: Installing IPython kernel spec')
        self.assertLess(Vast._rank('Контейнер запущен'), Vast._rank('Автонастройка: установка движка'))
        self.assertLess(Vast._rank('Сборка SwarmUI'), Vast._rank('Готов к генерации'))

    async def test_progress_never_steps_back_and_cleans_log(self):
        class LogVast(Vast):
            text = ''
            async def request(self, method, path, **kwargs):
                return {'instances': {'id': 7, 'actual_status': 'running', 'intended_status': 'running'}}
            async def _container_logs(self, instance_id):
                return self.text.split('\n')
            async def _swarm_ready(self, row):
                return False
        vast = LogVast('k', None)
        vast.text = '[studio-provision] 1 installing ComfyUI backend'
        self.assertEqual((await vast.progress(7))['stage'], 'Автонастройка: установка движка')
        vast.text = 'Downloading wheel'
        self.assertEqual((await vast.progress(7))['stage'], 'Автонастройка: установка движка')


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = TestClient(TestServer(create_app(PASSWORD)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_authentication_and_csrf(self):
        self.assertEqual((await self.client.get('/')).status, 401)
        self.assertEqual((await self.client.get('/', headers=AUTH)).status, 200)
        headers = {'Authorization': AUTH['Authorization']}
        self.assertEqual((await self.client.post('/api/vast/rent', json={}, headers=headers)).status, 403)

    async def test_vast_disabled_does_not_expose_a_secret(self):
        r = await self.client.get('/api/vast/config', headers=AUTH)
        self.assertEqual(await r.json(), {'enabled': False, 'template_configured': False})
        self.assertEqual((await self.client.get('/api/vast/instances', headers=AUTH)).status, 503)

    async def test_vast_rent_without_confirmation_and_progress(self):
        fake = FakeVast()
        self.client.app[STATE]['vast'] = fake
        self.assertEqual((await (await self.client.get('/api/vast/instances', headers=AUTH)).json())['instances'][0]['id'], 42)
        r = await self.client.post('/api/vast/offers', headers=AUTH, json={'gpu_names': ['RTX 3090'], 'max_price': .5, 'disk': 60})
        self.assertEqual((await r.json())['offers'][0]['id'], 99)
        r = await self.client.post('/api/vast/rent', headers=AUTH, json={'offer_id': 99, 'disk': 60})
        self.assertEqual(await r.json(), {'ok': True, 'instance_id': 84})
        r = await self.client.get('/api/vast/instances/42/progress', headers=AUTH)
        self.assertTrue((await r.json())['ready'])
        await self.client.post('/api/vast/instances/42/state', headers=AUTH, json={'state': 'stopped'})
        await self.client.delete('/api/vast/instances/42', headers=AUTH)
        self.assertEqual(fake.actions, [('offers', ['RTX 3090'], .5, 60), ('rent', 99, 60),
                                        ('state', 42, 'stopped'), ('destroy', 42)])


if __name__ == '__main__':
    unittest.main()
