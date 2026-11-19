import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
import app, adviser, engine, storage
from privacy_engine import PrivacyEngine
from test_core import FakeAnalyzer

class RetirementTests(unittest.TestCase):
    def test_placeholder_routes_and_origin_guard(self):
        with TestClient(app.app) as client:
            for route in ('actions','projects','projects/example','role','status','history-preview'):
                response=client.get('/api/memory/'+route)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['status'],'NOT_IMPLEMENTED')
            for method,route in [('POST','actions'),('PATCH','actions/example'),('DELETE','actions/example'),('PATCH','projects/example'),('PUT','role'),('PUT','settings'),('POST','refresh')]:
                response=client.request(method,'/api/memory/'+route,json={})
                self.assertEqual(response.status_code,501)
                self.assertEqual(response.json()['detail']['code'],'NOT_IMPLEMENTED')
            self.assertEqual(client.post('/api/memory/refresh',headers={'origin':'https://other.invalid'}).status_code,403)
            html=client.get('/memory').text
            self.assertEqual(html.count('Not yet implemented'),3)
            self.assertNotIn('/static/memory.js',html)
            for title in ('Action Register','Project Register','My Role','Adviser chat','Settings'):
                self.assertIn(title,html)
            self.assertNotIn('Memory automation',client.get('/settings').text)
            self.assertIn('Persona Library',client.get('/adviser').text)
            self.assertFalse(hasattr(app.app.state,'memory_scheduler'))
            self.assertFalse((storage.DATA/'memory').exists())
            self.assertFalse((storage.DATA/'memory_schedule').exists())

    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        root=Path(t.name);(root/'terms.txt').write_text('CLIENT: Acme')
        @asynccontextmanager
        async def telegram(*args):yield
        for p in (patch.object(storage,'DATA',root/'data'),patch.object(storage,'TERMS',root/'terms.txt'),patch.object(storage,'CONFIG',root/'config.json'),patch.object(app,'load_dotenv'),patch.object(app,'telegram_lifespan',telegram),patch.object(app,'PrivacyEngine',return_value=PrivacyEngine(FakeAnalyzer())),patch.object(engine.httpx,'AsyncClient',side_effect=AssertionError('External request forbidden'))):
            p.start();self.addCleanup(p.stop)

    def test_meetings_settings_and_cross_channel_without_memory(self):
        current=storage.new_session('telegram');current.update(session_type='meeting',mode='meeting_ready',transcript='Original full transcript',intelligence='Accepted meeting output',messages=[{'role':'user','content':'Original full transcript'}])
        storage.save_session(current)
        original=storage.session_path(current['id']).read_bytes()
        with TestClient(app.app) as client:
            self.assertEqual(client.get('/api/sessions').status_code,200)
            self.assertEqual(client.get('/api/sessions/'+current['id']).json()['transcript'],'Original full transcript')
            self.assertEqual(storage.session_path(current['id']).read_bytes(),original)
            settings=client.get('/api/settings').json()
            response=client.put('/api/settings',json={'model':'test/model','sensitive_terms':'CLIENT: Acme','chat_diagnosis':True,'monthly_cost_ceiling_usd':'2.00'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(client.get('/api/settings').json()['model'],'test/model')
            calls=[]
            async def provider(messages,*args,**kwargs):
                calls.append(messages);return 'Synthetic result'
            with patch.object(engine,'complete',side_effect=provider):
                session=storage.new_session()
                asyncio.run(engine.analyze_meeting(session,'Acme raw transcript with full details',app.app.state.privacy))
            self.assertTrue(calls)
            self.assertNotIn('ROLE BACKGROUND',json.dumps(calls))
            self.assertNotIn('Acme',json.dumps(calls))
            self.assertFalse((storage.DATA/'memory').exists())
            self.assertEqual(adviser.retrieve('anything')['items'],[])
            storage.delete_session(current['id'])
            self.assertFalse(storage.session_path(current['id']).exists())

if __name__=='__main__':unittest.main()
