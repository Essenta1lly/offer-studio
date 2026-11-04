import os,tempfile,threading,unittest,json
from unittest.mock import patch,Mock
from types import SimpleNamespace as O
os.environ.setdefault('OFFERSTUDIO_DATA',tempfile.mkdtemp(prefix='ai-test-'))
import core,ai_assistant as ai
class Tests(unittest.TestCase):
 def setUp(self):
  core.init()
  resolver=patch.object(ai,"resolve_context",return_value={"title":"LoL","price":"100"})
  resolver.start();self.addCleanup(resolver.stop)
  self.account=O(id=1,send_message=Mock(),get_lot_fields=Mock(return_value=O(fields={'fields[summary][ru]':'LoL','fields[desc][ru]':'Гарантия 30 дней','secrets':'SECRET_DELIVERY','csrf_token':'SECRET_CSRF'},price=100,currency=O(name='RUB'),active=True)))
  with patch.object(threading.Thread,'start'):ai.init(O(account=self.account),threading.RLock())
  with core.db() as db:
   for t in ['ai_chats','ai_messages','ai_usage','offers','outbox']:db.execute('DELETE FROM '+t)
  while not ai.Q.empty():ai.Q.get_nowait();ai.Q.task_done()
  core.setting('ai_enabled','1')
  core.save_offer({'url':'https://lzt.market/123/','funpay_url':'https://funpay.com/lots/offer?id=111'},lambda _:dict(price=100,active=True))
 def msg(self,id=10,author=2,text='Какая гарантия?',bot=False):
  return O(message=O(chat_id=22,id=id,author_id=author,type=O(name='NON_SYSTEM'),by_bot=bot,text=text,chat_name='Buyer',interlocutor_id=2,buyer_viewing=O(lot_id=111)))
 def test_manual_reply_stops_and_resume(self):
  ai.on_message(O(account=self.account),self.msg(author=1));ai.on_message(O(account=self.account),self.msg())
  self.assertTrue(ai.Q.empty());ai.control(['/ai_resume','22']);ai.on_message(O(account=self.account),self.msg());self.assertFalse(ai.Q.empty())
 def test_literal_chat_supported(self):
  e=self.msg();e.message.chat_id="users-1-2"
  ai.on_message(O(account=self.account),e)
  self.assertEqual(ai.Q.qsize(),1)
  self.assertEqual(ai.control(["/ai_pause","users-1-2"]),"Диалог приостановлен")
 def test_duplicate_ignored(self):
  ai.on_message(O(account=self.account),self.msg());ai.on_message(O(account=self.account),self.msg());self.assertEqual(ai.Q.qsize(),1)
 def test_delivery_and_cost_excluded_and_handoff(self):
  ai.on_message(O(account=self.account),self.msg())
  def gen(messages):
   text=json.dumps(messages);self.assertNotIn('SECRET',text);self.assertNotIn('buy_price',text)
   return dict(action='handoff',text='Продавец обязательно в ближайшее время разберётся.')
  with patch.object(ai,'generate',side_effect=gen):ai.process(ai.Q.get_nowait())
  self.account.send_message.assert_called_once()
  with core.db() as db:self.assertEqual(db.execute('SELECT paused FROM ai_chats').fetchone()[0],1);self.assertEqual(db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],1)
 def test_manual_reply_during_generation_prevents_send(self):
  ai.on_message(O(account=self.account),self.msg())
  def gen(messages):ai.on_message(O(account=self.account),self.msg(id=11,author=1));return dict(action='reply',text='Ответ')
  with patch.object(ai,'generate',side_effect=gen):ai.process(ai.Q.get_nowait())
  self.account.send_message.assert_not_called()
 def test_offtopic_no_reply(self):
  ai.on_message(O(account=self.account),self.msg())
  with patch.object(ai,'generate',return_value=dict(action='ignore',text='')):ai.process(ai.Q.get_nowait())
  self.account.send_message.assert_not_called()
if __name__=='__main__':unittest.main()
