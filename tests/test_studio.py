import asyncio
import base64
import io
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from studio.server import create_app, STATE
from studio.store import Store
from studio.vast import Vast, VastError
from studio.workflows import validate, build_graph

PASSWORD = 'test-password-never-deploy'
AUTH = {'Authorization':'Basic '+base64.b64encode(('studio:'+PASSWORD).encode()).decode(),
        'X-Studio-Request':'1'}


class FakeComfy:
    """Protocol fixture, never used by the application in production."""
    def __init__(self):
        self.ws = {}
        self.history = {}
        self.pending = []
        self.submissions = 0
        self.uploads = 0
        self.fail = False
        self.delay = .12
        self.tasks = []
        self.png = io.BytesIO()
        Image.new('RGB',(16,16),'green').save(self.png,'PNG')

    def app(self):
        app = web.Application()
        app.add_routes([web.get('/ws',self.socket),web.post('/prompt',self.prompt),
            web.get('/history/{id}',self.get_history),web.get('/queue',self.queue),
            web.get('/object_info',self.info),web.get('/system_stats',self.stats),
            web.get('/view',self.view),web.post('/upload/image',self.upload)])
        async def cleanup(app):
            for t in self.tasks:t.cancel()
            await asyncio.gather(*self.tasks,return_exceptions=True)
        app.on_cleanup.append(cleanup)
        return app

    async def socket(self,r):
        ws=web.WebSocketResponse();await ws.prepare(r);self.ws[r.query['clientId']]=ws
        async for _ in ws:pass
        return ws

    async def info(self,r):
        result={}
        for mode in ('image','video','image_video'):
            g=build_graph(validate({'prompt':'test','mode':mode,'source_id':'a'*32}),'test','input.png')
            for n in g.values():
                spec=result.setdefault(n['class_type'],{'input':{'required':{}}})
                for k,v in n['inputs'].items():
                    if k in ('unet_name','clip_name','vae_name'):
                        spec['input']['required'].setdefault(k,[[]])[0].append(v)
        return web.json_response(result)

    async def prompt(self,r):
        body=await r.json();self.submissions+=1;pid=str(uuid.uuid4());self.pending.append([0,pid])
        async def finish():
            await asyncio.sleep(self.delay)
            ws=self.ws.get(body['client_id'])
            if ws is not None and not ws.closed:
                await ws.send_json({'type':'progress','data':{'prompt_id':pid,'value':2,'max':8}})
            await asyncio.sleep(self.delay)
            video=any(n['class_type']=='SaveVideo' for n in body['prompt'].values())
            self.history[pid]={'status':{'completed':not self.fail,'status_str':'error' if self.fail else 'success','messages':['CUDA out of memory'] if self.fail else []},'outputs':{'10':{'images':[{'filename':'result.mp4' if video else 'result.png','subfolder':'studio','type':'output'}]}}}
            self.pending.clear()
        self.tasks.append(asyncio.create_task(finish()))
        return web.json_response({'prompt_id':pid})

    async def get_history(self,r):
        pid=r.match_info['id'];return web.json_response({pid:self.history[pid]} if pid in self.history else {})
    async def queue(self,r):return web.json_response({'queue_running':self.pending,'queue_pending':[]})
    async def stats(self,r):return web.json_response({'devices':[{'name':'protocol-test-only'}]})
    async def view(self,r):
        if r.query['filename'].endswith('.mp4'):
            return web.Response(body=b'protocol-fixture-not-real-video',content_type='video/mp4')
        return web.Response(body=self.png.getvalue(),content_type='image/png')
    async def upload(self,r):
        self.uploads+=1
        reader=await r.multipart();field=await reader.next();await field.read();return web.json_response({'name':field.filename,'subfolder':''})


class FakeVast:
    template_id = 'test-template'

    def __init__(self):
        self.actions = []

    async def instances(self):
        return [{'id': 42, 'gpu_name': 'RTX 5090', 'num_gpus': 1, 'gpu_ram': 32768,
                 'actual_status': 'running', 'dph_total': .6}]

    async def offers(self, names, price, disk):
        self.actions.append(('offers', names, price, disk))
        return [{'id': 99, 'gpu_name': 'RTX 3090', 'num_gpus': 1, 'dph_total': .2}]

    async def rent(self, offer_id, disk):
        self.actions.append(('rent', int(offer_id), int(disk)))
        return {'new_contract': 84}

    async def set_state(self, instance_id, state):
        self.actions.append(('state', int(instance_id), state))
        return {'success': True}

    async def destroy(self, instance_id):
        self.actions.append(('destroy', int(instance_id)))
        return {'success': True}


class VastPayloadTests(unittest.IsolatedAsyncioTestCase):
    class RecordedVast(Vast):
        templates = [{'image': 'other/image', 'hash_id': 'wrong'}, {'image': 'vastai/comfy', 'hash_id': 'auto-hash'}]

        async def request(self, method, path, **kwargs):
            if method == 'GET':
                return {'templates': self.templates}
            self.sent = method, path, kwargs['json']
            return {'new_contract': 84}

    async def test_rent_uses_pinned_template_and_cancels_unavailable_offer(self):
        vast = self.RecordedVast('test-key', None, template_id='template-hash')
        await vast.rent(99, 200)
        self.assertEqual(vast.sent, ('PUT', '/api/v0/asks/99/', {
            'disk': 200, 'cancel_unavail': True, 'template_hash_id': 'template-hash'}))

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


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.fake=FakeComfy();self.comfy=TestServer(self.fake.app());await self.comfy.start_server()
        await self.open_app()

    async def open_app(self):
        self.client=TestClient(TestServer(create_app(self.temp.name,str(self.comfy.make_url('')).rstrip('/'),PASSWORD)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close();await self.comfy.close();self.temp.cleanup()

    async def create(self,data=None,key=None):
        return await self.client.post('/api/jobs',json=data or {'prompt':'A portrait'},headers={**AUTH,'Idempotency-Key':key or str(uuid.uuid4())})

    async def wait_job(self,id,status='completed'):
        for _ in range(100):
            r=await self.client.get('/api/jobs',headers=AUTH);jobs=(await r.json())['jobs'];job=next(j for j in jobs if j['id']==id)
            if job['status']==status:return job
            await asyncio.sleep(.08)
        self.fail('Job did not reach '+status+': '+str(job))

    async def test_end_to_end_persist_download_restart(self):
        r=await self.create();self.assertEqual(r.status,201);job=await r.json()
        done=await self.wait_job(job['id']);self.assertTrue(done['files'])
        file=done['files'][0]
        r=await self.client.get('/api/files/'+file['id']+'?download=1',headers=AUTH)
        self.assertEqual(r.status,200);self.assertIn('attachment',r.headers['Content-Disposition']);self.assertEqual(await r.read(),self.fake.png.getvalue())
        r=await self.client.get('/api/jobs/'+job['id']+'/workflow',headers=AUTH);self.assertEqual(r.status,200)
        await self.client.close();await self.open_app()
        r=await self.client.get('/api/jobs',headers=AUTH);self.assertEqual((await r.json())['jobs'][0]['status'],'completed')
        self.assertEqual(self.fake.submissions,1)

    async def test_same_request_is_not_submitted_twice(self):
        key=str(uuid.uuid4());a=await self.create(key=key);b=await self.create(key=key)
        a,b=await a.json(),await b.json();self.assertEqual(a['id'],b['id'])
        await self.wait_job(a['id']);self.assertEqual(self.fake.submissions,1)
        r=await self.create({'prompt':'different'},key=key);self.assertEqual(r.status,409)

    async def test_error_visible_and_no_result(self):
        self.fake.fail=True;r=await self.create();job=await r.json();failed=await self.wait_job(job['id'],'failed')
        self.assertIn('CUDA out of memory',failed['error']);self.assertFalse(failed['files'])

    async def test_progress_is_real_event(self):
        self.fake.delay=.4;r=await self.create();job=await r.json()
        for _ in range(40):
            current=self.client.app[STATE]['store'].job(job['id'])
            if current['progress']==25:break
            await asyncio.sleep(.05)
        self.assertEqual(current['progress'],25)

    async def test_restart_does_not_resubmit_running_job(self):
        self.fake.delay=.5;r=await self.create();job=await r.json()
        for _ in range(40):
            if self.client.app[STATE]['store'].job(job['id'])['prompt_id']:break
            await asyncio.sleep(.05)
        await self.client.close();await self.open_app();await self.wait_job(job['id']);self.assertEqual(self.fake.submissions,1)

    async def test_uncertain_submission_is_not_repeated(self):
        store=self.client.app[STATE]['store'];job=store.create(validate({'prompt':'test'}),str(uuid.uuid4()))
        store.update(job['id'],status='submitting');failed=await self.wait_job(job['id'],'failed')
        self.assertIn('Автоповтор',failed['error']);self.assertEqual(self.fake.submissions,0)

    async def test_authentication_and_csrf(self):
        for route in ('/','/api/jobs','/api/files/anything'):
            r=await self.client.get(route);self.assertEqual(r.status,401)
        r=await self.client.post('/api/jobs',json={'prompt':'test'},headers={'Authorization':AUTH['Authorization']});self.assertEqual(r.status,403)

    async def test_invalid_requests(self):
        for data in ({'prompt':''},{'prompt':'x','mode':'other'},{'prompt':'x','seed':-1},{'prompt':'x','mode':'image_video'},{'prompt':'x','seconds':True}):
            r=await self.create(data);self.assertEqual(r.status,400)

    async def test_image_upload_and_reject_fake_image(self):
        r=await self.client.post('/api/uploads',data=self.fake.png.getvalue(),headers={**AUTH,'Content-Type':'image/png'})
        self.assertEqual(r.status,201);file=await r.json()
        r=await self.client.get(file['url'],headers=AUTH);self.assertEqual(r.status,200)
        r=await self.client.post('/api/uploads',data=b'not an image',headers={**AUTH,'Content-Type':'image/png'});self.assertEqual(r.status,400)
        r=await self.client.post('/api/uploads',data=b'<svg/>',headers={**AUTH,'Content-Type':'image/svg+xml'});self.assertEqual(r.status,400)

    async def test_video_and_uploaded_frame_protocol(self):
        r=await self.client.post('/api/uploads',data=self.fake.png.getvalue(),headers={**AUTH,'Content-Type':'image/png'})
        source=await r.json()
        for mode in ('video','image_video'):
            r=await self.create({'prompt':'A person turns toward the camera','mode':mode,'source_id':source['id']})
            job=await r.json();done=await self.wait_job(job['id'])
            self.assertEqual(done['files'][0]['mime'],'video/mp4')
            r=await self.client.get('/api/files/'+done['files'][0]['id']+'?download=1',headers=AUTH)
            self.assertEqual(await r.read(),b'protocol-fixture-not-real-video')
        self.assertEqual(self.fake.uploads,1)

    async def test_vast_disabled_does_not_expose_a_secret(self):
        r=await self.client.get('/api/vast/config',headers=AUTH)
        self.assertEqual(await r.json(),{'enabled':False,'template_configured':False})
        r=await self.client.get('/api/vast/instances',headers=AUTH)
        self.assertEqual(r.status,503);self.assertNotIn('key',(await r.text()).lower())

    async def test_vast_list_search_and_explicit_confirmations(self):
        vast=FakeVast();self.client.app[STATE]['vast']=vast
        r=await self.client.get('/api/vast/instances',headers=AUTH)
        self.assertEqual((await r.json())['instances'][0]['id'],42)
        r=await self.client.post('/api/vast/offers',headers=AUTH,json={'gpu_names':['RTX 3090'],'max_price':.5,'disk':200})
        self.assertEqual((await r.json())['offers'][0]['id'],99)
        r=await self.client.post('/api/vast/rent',headers=AUTH,json={'offer_id':99,'disk':200,'confirm':'wrong'})
        self.assertEqual(r.status,400)
        r=await self.client.post('/api/vast/rent',headers=AUTH,json={'offer_id':99,'disk':200,'confirm':'RENT 99'})
        self.assertEqual(r.status,200)
        r=await self.client.post('/api/vast/instances/42/state',headers=AUTH,json={'state':'stopped'})
        self.assertEqual(r.status,200)
        r=await self.client.delete('/api/vast/instances/42',headers=AUTH,json={'confirm':'wrong'})
        self.assertEqual(r.status,400)
        r=await self.client.delete('/api/vast/instances/42',headers=AUTH,json={'confirm':'DESTROY 42'})
        self.assertEqual(r.status,200)
        self.assertIn(('rent',99,200),vast.actions);self.assertIn(('state',42,'stopped'),vast.actions);self.assertIn(('destroy',42),vast.actions)


class WorkflowTests(unittest.TestCase):
    def test_video_and_image_video_graphs(self):
        for seconds in (5,10):
            p=validate({'prompt':'test','mode':'video','seconds':seconds})
            g=build_graph(p,'id');self.assertEqual(g['6']['inputs']['length'],24*seconds+1)
            self.assertNotIn('start_image',g['6']['inputs'])
        p=validate({'prompt':'test','mode':'image_video','source_id':'a'*32})
        g=build_graph(p,'id','input.png');self.assertEqual(g['12']['inputs']['image'],'input.png')
        self.assertEqual(g['6']['inputs']['start_image'],['12',0])

    def test_reject_path_traversal(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(root)
            with self.assertRaises(ValueError):store.file_path({'name':'../../secrets'})
            store.db.close()


if __name__=='__main__':unittest.main()
