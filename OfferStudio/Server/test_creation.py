import unittest
from unittest.mock import patch
import core
try:
 import creation
except ImportError:
 creation=None

@unittest.skipIf(creation is None,'bs4 is installed on server')
class CreationTests(unittest.TestCase):
 def setUp(self):
  core.init()
  with core.db() as c:
   c.execute('DELETE FROM offers');c.execute('CREATE TABLE IF NOT EXISTS creations(source_id TEXT PRIMARY KEY,stage TEXT,funpay_id INTEGER)');c.execute('DELETE FROM creations')
 def test_title_limit_and_english_inventory(self):
  title='🔶【Первый длинный скин, Второй длинный скин, Третий скин】🔶 【12 СКИНОВ | 29 ЧЕМПИОНОВ 50К СЭ】🎮🟠【АВТОВЫДАЧА】🟠'
  self.assertLessEqual(len(creation.fit_title(title)),100)
  text=creation.english_description({},'Чемпионы(2): Мастер Йи, Эш\n✅ гарантия на аккаунт 30 дней')
  self.assertIn('Champions(2)',text);self.assertIn('30 days',text)
  import re
  self.assertIsNone(re.search('[а-яА-ЯёЁ]',text))
 def test_creation_price_fields_and_duplicate_protection(self):
  from types import SimpleNamespace as O
  sent=[]
  def method(verb,path,headers,data,**kwargs):
   if verb=='get':return O(content=b'<form class="form-offer-editor"><input name="csrf_token" value="test"></form>')
   sent.append(data);return O(json=lambda:{'offer_id':1234})
  p={'url':'https://lzt.market/123/','title':'Test','description':'Description','buy_price':'480 ₽','server':'EUNE','rank':'Unranked','level':'51','championCount':'37','skinCount':'8'}
  account=O(method=method,get_lot_fields=lambda _:O(title_ru=sent[-1]['fields[summary][ru]'],description_ru=sent[-1]['fields[desc][ru]'],price=1200,active=True,fields=sent[-1]))
  r=creation.create(account,p)
  self.assertEqual(r['funpay_id'],1234);self.assertEqual(sent[0]['price'],'1200.00')
  self.assertEqual(sent[0]['auto_delivery'],'');self.assertEqual(sent[0]['fields[hero]'],'37')
  self.assertEqual(creation.create(account,p)['funpay_id'],1234)
  self.assertEqual(len(sent),1)
