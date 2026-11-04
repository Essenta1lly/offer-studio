import os,tempfile,unittest
os.environ.setdefault('OFFERSTUDIO_DATA',tempfile.mkdtemp())
import core,operations as op
class Tests(unittest.TestCase):
 def setUp(self):
  core.init();op.init()
  with core.db() as c:
   for t in ('orders','offers','order_links','order_adjustments','service_health'):c.execute('DELETE FROM '+t)
  core.save_offer({'url':'https://lzt.market/123456/','buy_price':'400 ₽','funpay_url':'https://funpay.com/lots/offer?id=99'},lambda _:dict(price=1000,active=True))
 def order(self,id,state):
  with core.db() as c:c.execute('INSERT INTO orders VALUES (?,?,?,?,?,?)',(id,core.now(),'1000','RUB',state,'LoL'))
  op.link_order(id,'123456')
 def test_profit_separated_and_refund_keeps_cost(self):
  self.order('closed','CLOSED');self.order('paid','PAID');self.order('returned','REFUNDED')
  data=op.finance_data()['groups']
  self.assertEqual(data['CLOSED']['profit'],'600');self.assertEqual(data['PAID']['profit'],'600');self.assertEqual(data['REFUNDED']['profit'],'-400')
 def test_unknown_partial_not_assumed_zero(self):
  self.order('partial','PARTIALLY_REFUNDED')
  self.assertEqual(op.finance_data()['groups']['PARTIALLY_REFUNDED']['unknown'],1)
 def test_cost_snapshot_stable_and_unique(self):
  self.order('closed','CLOSED')
  with core.db() as c:c.execute('UPDATE offers SET buy_price="900"')
  self.assertEqual(op.finance_data()['groups']['CLOSED']['profit'],'600')
 def test_no_heuristic_link(self):
  with core.db() as c:c.execute('INSERT INTO orders VALUES (?,?,?,?,?,?)',('unknown',core.now(),'1000','RUB','CLOSED','same title'))
  self.assertEqual(op.finance_data()['groups']['CLOSED']['unknown'],1)
 def test_source_reference_captures_direct_link(self):
  from types import SimpleNamespace as O
  with core.db() as c:c.execute('INSERT INTO orders VALUES (?,?,?,?,?,?)',('new',core.now(),'1000','RUB','CLOSED','LoL'))
  op.capture_order(O(id='new',full_description='Код товара: OS-123456'))
  self.assertEqual(op.finance_data()['orders'][0]['source_id'],'123456')
 def test_failure_retains_last_success(self):
  op.health('funpay',True,'ok');op.health('funpay',False,'expired')
  row=next(r for r in op.health_data() if r['name']=='funpay')
  self.assertEqual(row['state'],'error');self.assertGreater(row['success_at'],0)
if __name__=='__main__':unittest.main()
