import os, tempfile, unittest
os.environ['OFFERSTUDIO_DATA']=tempfile.mkdtemp(prefix='offerstudio-test-')
import core

class CoreTests(unittest.TestCase):
    def setUp(self):
        core.init()
        with core.db() as c:
            for table in ('offers','events','orders','outbox'):c.execute('DELETE FROM '+table)
    def snapshot(self,**args):
        base={'url':'https://lzt.market/123/','heading':'Аккаунт LoL','card':'Аккаунт продан','buy':False,'http_status':200}
        base.update(args);return base
    def test_sold_requires_current_card_and_no_buy(self):
        self.assertEqual(core.classify(self.snapshot(),'123')[0],'sold')
        self.assertEqual(core.classify(self.snapshot(buy=True),'123')[0],'unknown')
        self.assertEqual(core.classify(self.snapshot(heading=''),'123')[0],'unknown')
        self.assertEqual(core.classify(self.snapshot(url='https://lzt.market/124/'),'123')[0],'unknown')
        self.assertEqual(core.classify(self.snapshot(card='Ссылка на отзыв: продан'),'123')[0],'unknown')
        self.assertEqual(core.classify(self.snapshot(challenge=True),'123')[0],'challenge')
        self.assertEqual(core.classify(self.snapshot(http_status=403),'123')[0],'unknown')
    def test_available_and_unknown(self):
        self.assertEqual(core.classify(self.snapshot(card='Купить',buy=True),'123')[0],'available')
        self.assertEqual(core.classify(self.snapshot(card='Страница недоступна'),'123')[0],'unknown')
    def test_link_ownership_and_uniqueness(self):
        calls=[]
        def verify(fid):calls.append(fid);return {'price':900,'active':True}
        payload={'url':'https://lzt.market/123/','funpay_url':'https://funpay.com/lots/offer?id=111','monitoring':True,'auto_disable':True}
        r=core.save_offer(payload,verify)
        self.assertEqual(calls,[111]);self.assertEqual(r['verified'],1);self.assertEqual(r['sell_price'],'900.00')
        payload['url']='https://lzt.market/124/'
        with self.assertRaises(ValueError):core.save_offer(payload,verify)
    def test_failed_ownership_has_no_side_effect(self):
        def fail(fid):raise ValueError('Не ваш лот')
        with self.assertRaises(ValueError):core.save_offer({'url':'https://lzt.market/123/','funpay_url':'https://funpay.com/lots/offer?id=111'},fail)
        self.assertEqual(core.offers(),[])
    def test_reject_external_and_missing_links(self):
        for raw in ['http://lzt.market/123/','https://evil.com/123/','https://lzt.market/login/']:
            with self.assertRaises(ValueError):core.canonical_lzt(raw)
        with self.assertRaises(ValueError):core.save_offer({'url':'https://lzt.market/123/','auto_disable':True},lambda _:None)
    def test_outage_alert_once_until_healthy_cycle(self):
        core.setting('lzt_incident','0')
        core.site_problem('TimeoutError');core.site_problem('HTTP 502')
        with core.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],1)
        core.site_recovered();core.site_recovered()
        with core.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],2)
    def test_repeated_offer_error_survives_check_start(self):
        core.save_offer({'url':'https://lzt.market/123/'},lambda _:None)
        core.setting('issue:123','')
        core.observe('123','unknown','TimeoutError')
        core.observe('123','checking','Checking')
        core.observe('123','unknown','TimeoutError')
        with core.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],1)
    def test_archive_stops_monitor_and_does_not_reimport(self):
        core.save_offer({'url':'https://lzt.market/123/','monitoring':True},lambda _:None)
        core.archive_offer('123')
        self.assertEqual(core.offers(),[])
        self.assertEqual(core.get_offer('123')['monitoring'],0)
        self.assertEqual(core.get_offer('123')['auto_disable'],0)
        self.assertIn('https://lzt.market/123/',core.status_data()['archived_urls'])
        with self.assertRaises(ValueError):core.save_offer({'url':'https://lzt.market/123/'},lambda _:None)
    def test_notifications_dedup_and_removed_persists(self):
        core.save_offer({'url':'https://lzt.market/123/'},lambda _:None)
        core.notify('same','one');core.notify('same','two')
        with core.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],1)
            c.execute('UPDATE offers SET status="removed" WHERE id="123"')
        core.observe('123','available','Проверено')
        self.assertEqual(core.get_offer('123')['status'],'removed')

if __name__=='__main__':unittest.main()
