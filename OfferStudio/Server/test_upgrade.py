import os,tempfile,unittest,threading
from unittest.mock import Mock,patch
from types import SimpleNamespace as O
os.environ.setdefault('OFFERSTUDIO_DATA',tempfile.mkdtemp())
import core,batch,operations,ai_assistant as ai
class Tests(unittest.TestCase):
 def setUp(self):
  core.init();operations.init();batch.init()
  with core.db() as db:
   for t in ('creation_queue','offers','order_links','orders'):db.execute('DELETE FROM '+t)
  with patch.object(threading.Thread,'start'):ai.init(O(account=Mock()),threading.RLock())
 def test_queue_dedup_and_ambiguous_failure(self):
  item=dict(url='https://lzt.market/123456/',title='Title',description='Desc',buy_price='400')
  batch.enqueue([item,item]);self.assertEqual(len(batch.jobs()),1)
  create=Mock(side_effect=TimeoutError());self.assertTrue(batch.run_one(create));self.assertFalse(batch.run_one(create))
  create.assert_called_once();self.assertEqual(batch.jobs()[0]['state'],'review')
 def test_cancel_queued_only(self):
  batch.enqueue([dict(url='https://lzt.market/123456/',title='a',description='b',buy_price='4')]);batch.cancel()
  create=Mock();self.assertFalse(batch.run_one(create));create.assert_not_called()
 def test_retry_pending_is_blocked(self):
  item=dict(url='https://lzt.market/123456/',title='Title',description='Desc',buy_price='400')
  batch.enqueue([item]);batch.run_one(Mock(side_effect=TimeoutError()))
  with core.db() as db:
   db.execute('CREATE TABLE IF NOT EXISTS creations(source_id TEXT PRIMARY KEY,stage TEXT,funpay_id INTEGER)')
   db.execute('INSERT OR REPLACE INTO creations VALUES ("123456","pending",NULL)')
  with self.assertRaises(ValueError):batch.retry('123456')
 def test_authorized_callback_and_buttons(self):
  import offer_studio_plugin as p
  p._cardinal=O(telegram=O(authorized_users={7}))
  p._mirror=None
  with core.db() as db:
   db.execute('INSERT OR REPLACE INTO ai_chats(chat,paused) VALUES ("22",0)')
   db.execute('INSERT OR REPLACE INTO ai_context VALUES ("22","ABCDEFGH",99,"жалоба")')
  kb=p.assistant_buttons('ai-handoff:22:1').to_dict()
  self.assertIn('Открыть заказ',str(kb));self.assertIn('Открыть лот',str(kb))
  bot=Mock();call=O(message=O(chat=O(type='private')),from_user=O(id=999),data='osa:pause:22',id='1')
  p.assistant_callback(bot,call);bot.answer_callback_query.assert_not_called()
  call.from_user.id=7;p.assistant_callback(bot,call)
  with core.db() as db:self.assertEqual(db.execute('SELECT paused FROM ai_chats WHERE chat="22"').fetchone()[0],1)
 def test_order_ownership(self):
  ai.C.account.id=1;ai.C.account.get_chat.return_value=O(looking_link=None);ai.C.account.get_sales.return_value=(None,[],None,{})
  ai.C.account.get_order.return_value=O(seller_id=1,buyer_id=999)
  with self.assertRaises(ValueError):ai.resolve_context('22','Buyer',2,'#ABCDEFGH')
 def test_ambiguous_order_asks_and_bare_reference_works(self):
  a=ai.C.account;a.id=1;a.get_chat.return_value=O(looking_link=None);a.get_sales.return_value=(None,[O(id='ABCDEFGH'),O(id='BBBBBBBB')])
  self.assertTrue(ai.resolve_context('22','Buyer',2,'вопрос')['clarification_required'])
  a.get_order.return_value=O(id='ABCDEFGH',seller_id=1,buyer_id=2,status=O(name='PAID'),sum=100,currency=O(name='RUB'),short_description='Title',full_description='Desc',subcategory=None)
  self.assertEqual(ai.resolve_context('22','Buyer',2,'ABCDEFGH')['order_id'],'ABCDEFGH')
if __name__=='__main__':unittest.main()
