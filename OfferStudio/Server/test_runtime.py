import asyncio,unittest
from unittest.mock import patch,AsyncMock
import core
try:
 import worker
except ImportError:
 worker=None

@unittest.skipIf(worker is None,'Playwright runtime tests run on server')
class RuntimeTests(unittest.TestCase):
 def test_javascript_redirect_updates_document_http_status(self):
  from types import SimpleNamespace as Obj
  class Page:
   main_frame=object()
   count=0
   handler=None
   def on(self,event,handler):self.handler=handler
   def remove_listener(self,event,handler):self.handler=None
   async def goto(self,*args,**kwargs):return Obj(status=403)
   async def evaluate(self,*args):
    self.count+=1
    if self.count>1:self.handler(Obj(status=200,frame=self.main_frame,request=Obj(is_navigation_request=lambda:True)))
    return dict(url='https://lzt.market/123/',heading='Account LoL' if self.count>1 else '',card='Купить',buy=self.count>1)
  page=Page()
  async def run():
   with patch.object(worker.asyncio,'sleep',AsyncMock()):return await worker.inspect(page,{'url':'https://lzt.market/123/','id':'123'})
  self.assertEqual(asyncio.run(run())[0],'available')
  self.assertIsNone(page.handler)
 def test_oldest_offers_first_prevents_starvation(self):
  rows=[dict(id='new',checked_at=300,monitoring=1,archived_at=0,status='available'),dict(id='old',checked_at=100,monitoring=1,archived_at=0,status='available'),dict(id='never',checked_at=None,monitoring=1,archived_at=0,status='checking'),dict(id='removed',checked_at=0,monitoring=1,archived_at=0,status='removed')]
  self.assertEqual([x['id'] for x in worker.ordered_checks(rows)],['never','old','new'])
 def test_source_timeout_does_not_disable_or_repeat_alert(self):
  core.init()
  with core.db() as c:
   c.execute('DELETE FROM offers');c.execute('DELETE FROM outbox')
  core.setting('lzt_incident','0')
  row=core.save_offer({'url':'https://lzt.market/123/','monitoring':True},lambda _:None)
  async def run():
   with patch.object(worker,'inspect',AsyncMock(side_effect=TimeoutError())),patch.object(worker,'disable') as disable:
    page=type('Page',(),{'evaluate':AsyncMock()})()
    self.assertEqual(await worker.check(page,row),'error')
    self.assertEqual(await worker.check(page,core.get_offer('123')),'error')
    disable.assert_not_called()
  asyncio.run(run())
  self.assertEqual(core.get_offer('123')['source_state'],'unknown')
  with core.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],1)
 def test_failed_funpay_action_keeps_confirmed_sale(self):
  core.init()
  with core.db() as c:
   c.execute('DELETE FROM offers');c.execute('DELETE FROM outbox')
  core.setting('paused','0')
  row=core.save_offer({'url':'https://lzt.market/123/','funpay_url':'https://funpay.com/lots/offer?id=111','monitoring':True,'auto_disable':True},lambda _:{'price':900,'active':True})
  async def run():
   with patch.object(worker,'inspect',AsyncMock(return_value=('sold','Confirmed'))),patch.object(worker.asyncio,'sleep',AsyncMock()),patch.object(worker,'disable',side_effect=TimeoutError()):
    await worker.check(None,row);await worker.check(None,core.get_offer('123'))
  asyncio.run(run())
  self.assertEqual(core.get_offer('123')['source_state'],'sold')
  with core.db() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox WHERE dedup='action-error:123'").fetchone()[0],1)
