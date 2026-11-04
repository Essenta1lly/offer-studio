import copy, os, tempfile, unittest
from types import SimpleNamespace as Obj
os.environ.setdefault('OFFERSTUDIO_DATA',tempfile.mkdtemp(prefix='offerstudio-plugin-test-'))
import core
import offer_studio_plugin as plugin

class FakeAccount:
    def __init__(self):
        self.fields={'offer_id':'111','active':'on','secrets':'keep-delivery-data','price':'900','fields[desc][ru]':'keep-description'}
        self.writes=0;self.ignore_save=False
    def get_lot_fields(self,id):
        return Obj(lot_id=id,fields=copy.deepcopy(self.fields),active=self.fields['active']=='on',title_ru='LoL account',price=900,currency=Obj(name='RUB'),subcategory=Obj(name='Аккаунты',category=Obj(name='League of Legends')))
    def save_lot(self,fields):
        self.writes+=1
        if not self.ignore_save:
            self.fields=fields.fields;self.fields['active']='on' if fields.active else ''

class PluginTests(unittest.TestCase):
    def setUp(self):
        core.init()
        with core.db() as c:
            for t in ('offers','events','outbox','orders'):c.execute('DELETE FROM '+t)
        self.account=FakeAccount();plugin._cardinal=Obj(account=self.account,telegram=None)
        core.save_offer({'url':'https://lzt.market/123/','funpay_url':'https://funpay.com/lots/offer?id=111','monitoring':True,'auto_disable':True},plugin.verify_lot)
        core.setting('paused','0')
    def test_no_disable_without_sold_or_while_paused(self):
        with self.assertRaises(ValueError):plugin.disable_offer('123')
        core.observe('123','sold','double checked');core.setting('paused','1')
        with self.assertRaises(ValueError):plugin.disable_offer('123')
        self.assertEqual(self.account.writes,0)
    def test_disable_preserves_stock_and_description(self):
        core.observe('123','sold','double checked')
        self.assertTrue(plugin.disable_offer('123')['ok'])
        self.assertEqual(self.account.fields['active'],'')
        self.assertEqual(self.account.fields['secrets'],'keep-delivery-data')
        self.assertEqual(self.account.fields['fields[desc][ru]'],'keep-description')
        self.assertEqual(core.get_offer('123')['status'],'removed')
        plugin.disable_offer('123')
        self.assertEqual(self.account.writes,1)
        with core.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],1)
    def test_new_lol_order_notified_once(self):
        order=Obj(id='TESTORDER',subcategory=Obj(name='Аккаунты',category=Obj(name='League of Legends')),status=Obj(name='PAID'),price=1500,currency=Obj(name='RUB'),description='LoL account')
        plugin.on_order(None,Obj(order=order));plugin.on_order(None,Obj(order=order))
        with core.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM orders').fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM outbox WHERE dedup='order:TESTORDER'").fetchone()[0],1)
        order.id='NOTLOL';order.subcategory.category.name='Steam'
        plugin.on_order(None,Obj(order=order))
        with core.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM orders').fetchone()[0],1)
    def test_success_not_reported_when_funpay_stays_active(self):
        self.account.ignore_save=True;core.observe('123','sold','double checked')
        with self.assertRaises(ValueError):plugin.disable_offer('123')
        self.assertNotEqual(core.get_offer('123')['status'],'removed')

if __name__=='__main__':unittest.main()
