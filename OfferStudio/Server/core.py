"""Offer Studio shared database and authenticated loopback API. No marketplace API keys."""
import contextlib, hashlib, hmac, json, os, re, sqlite3, threading, time
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

DATA = Path(os.environ.get('OFFERSTUDIO_DATA', '/home/fpc/FunPayCardinal/storage/offer_studio'))
LOCK = threading.RLock()

def now(): return int(time.time())
@contextlib.contextmanager
def db():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    con = sqlite3.connect(DATA/'state.db', timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL'); con.execute('PRAGMA busy_timeout=30000')
    try:
        with con:yield con
    finally:con.close()

def init():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS offers (
          id TEXT PRIMARY KEY, local_id TEXT NOT NULL DEFAULT '', url TEXT NOT NULL,
          funpay_id INTEGER UNIQUE, funpay_url TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
          buy_price TEXT NOT NULL DEFAULT '', sell_price TEXT NOT NULL DEFAULT '', currency TEXT NOT NULL DEFAULT 'RUB',
          monitoring INTEGER NOT NULL DEFAULT 0, auto_disable INTEGER NOT NULL DEFAULT 0,
          verified INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'checking',
          source_state TEXT NOT NULL DEFAULT 'unknown', reason TEXT NOT NULL DEFAULT '',
          checked_at INTEGER, removed_at INTEGER, updated_at INTEGER NOT NULL,
          funpay_active INTEGER, last_notified TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, offer_id TEXT,
          level TEXT NOT NULL, message TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS orders (
          id TEXT PRIMARY KEY, ts INTEGER NOT NULL, price TEXT, currency TEXT, status TEXT, description TEXT);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY AUTOINCREMENT,
          dedup TEXT UNIQUE NOT NULL, text TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0,
          attempts INTEGER NOT NULL DEFAULT 0, next_try INTEGER NOT NULL DEFAULT 0);
        ''')
        columns={r[1] for r in c.execute('PRAGMA table_info(offers)')}
        if 'archived_at' not in columns:c.execute('ALTER TABLE offers ADD COLUMN archived_at INTEGER NOT NULL DEFAULT 0')
        c.execute('CREATE INDEX IF NOT EXISTS events_ts ON events(ts)')
        c.execute('CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(sent,next_try,id)')
        c.execute('CREATE INDEX IF NOT EXISTS offers_monitor ON offers(monitoring,archived_at,checked_at)')
        for key,val in [('paused','1'),('check_now','0'),('worker_heartbeat','0'),('browser_status','not_started'),('bot_enabled','0')]:
            c.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',(key,val))
        c.execute('UPDATE offers SET status="checking",reason="Проверка прервана перезапуском" WHERE status="checking" AND monitoring=1')
    tokenfile = DATA/'api_token'
    if not tokenfile.exists():
        import secrets
        fd=os.open(tokenfile,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:f.write(secrets.token_urlsafe(40))

def setting(key, value=None):
    with db() as c:
        if value is not None: c.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value WHERE settings.value<>excluded.value',(key,str(value)))
        row=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return row[0] if row else ''

def event(id, level, message):
    with db() as c: c.execute('INSERT INTO events(ts,offer_id,level,message) VALUES (?,?,?,?)',(now(),id,level,message[:1000]))

def notify(key, text):
    with db() as c:c.execute('INSERT OR IGNORE INTO outbox(dedup,text) VALUES (?,?)',(key,text[:3800]))

def offers():
    with db() as c:return [dict(r) for r in c.execute('SELECT * FROM offers WHERE archived_at=0 ORDER BY updated_at DESC')]
def get_offer(id):
    with db() as c:
        r=c.execute('SELECT * FROM offers WHERE id=?',(str(id),)).fetchone()
        return dict(r) if r else None

def canonical_lzt(raw):
    u=urlparse(raw)
    if u.scheme!='https' or u.hostname not in ('lzt.market','www.lzt.market') or not re.fullmatch(r'/\d+/?',u.path):
        raise ValueError('Нужна ссылка https://lzt.market/123456789/')
    id=u.path.strip('/')
    return id, f'https://lzt.market/{id}/'

def funpay_id(raw):
    u=urlparse(raw)
    if u.scheme!='https' or u.hostname!='funpay.com' or u.path not in ('/lots/offer','/lots/offerEdit'):
        raise ValueError('Нужна ссылка вашего лота https://funpay.com/lots/offer?id=123')
    value=(parse_qs(u.query).get('id') or parse_qs(u.query).get('offer') or [''])[0]
    if not value.isdigit() or int(value)<=0:raise ValueError('Не найден номер оффера FunPay')
    return int(value)

def save_offer(payload, verifier):
    id,url=canonical_lzt(payload.get('url',''))
    fp=payload.get('funpay_url','').strip()
    enable=payload.get('monitoring',False) is True
    auto=payload.get('auto_disable',False) is True
    fid=funpay_id(fp) if fp else None
    if auto and not fid:raise ValueError('Для автоснятия укажите ваш лот FunPay')
    existing=get_offer(id)
    if existing and existing['archived_at']:
        raise ValueError('Оффер находится в архиве. Сначала восстановите его командой /os_restore '+id)
    info=None
    if fid and (not existing or existing['funpay_id']!=fid or not existing['verified']):
        info=verifier(fid)  # Editable form proves ownership. No publishing occurs.
    verified=bool(fid and (info or (existing and existing['verified'])))
    with LOCK,db() as c:
        other=c.execute('SELECT id FROM offers WHERE funpay_id=? AND id<>?',(fid,id)).fetchone() if fid else None
        if other:raise ValueError('Этот FunPay-оффер уже связан с другим LZT-аккаунтом')
        c.execute('''INSERT INTO offers(id,url,updated_at) VALUES (?,?,?)
                     ON CONFLICT(id) DO NOTHING''',(id,url,now()))
        c.execute('''UPDATE offers SET local_id=?,funpay_id=?,funpay_url=?,title=?,buy_price=?,sell_price=?,
          monitoring=?,auto_disable=?,verified=?,updated_at=? WHERE id=?''',
          (str(payload.get('local_id',''))[:100],fid,f'https://funpay.com/lots/offer?id={fid}' if fid else '',
           str(payload.get('title',''))[:500],str(payload.get('buy_price',''))[:60],
           str(Decimal(str(info['price'])).quantize(Decimal('0.01'))) if info and info.get('price') is not None else str(payload.get('sell_price',''))[:60],
           int(enable),int(auto),int(verified),now(),id))
        if info:c.execute('UPDATE offers SET funpay_active=?,currency=? WHERE id=?',(int(info['active']),info.get('currency','RUB'),id))
    event(id,'info','Связь и настройки мониторинга сохранены; владение лотом проверено' if verified else 'Оффер добавлен без связи с FunPay')
    setting('check_now','1')
    return get_offer(id)

def classify(snapshot, expected_id):
    """Only the current offer card is evidence; reviews/sidebar are excluded by DOM extraction."""
    try:id,_=canonical_lzt(snapshot.get('url',''))
    except ValueError:return 'unknown','Страница ушла с адреса оффера'
    if id!=str(expected_id):return 'unknown','Открыт другой оффер'
    if snapshot.get('http_status',200) not in (200,):return 'unknown','Сайт вернул ошибку HTTP'
    if snapshot.get('challenge'):return 'challenge','Нужна ручная проверка сайта/CAPTCHA'
    if not snapshot.get('heading'):return 'unknown','Не найден заголовок текущего оффера'
    card=' '.join(snapshot.get('card','').lower().split())
    sold=bool(re.search(r'\bаккаунт\s+продан\b|\baccount\s+(?:is\s+)?sold\b',card))
    if sold and not snapshot.get('buy'):return 'sold','Пометка «Аккаунт продан» в карточке; покупка недоступна'
    if snapshot.get('buy') and not sold:return 'available','В текущей карточке доступна покупка'
    return 'unknown','Нет достаточных признаков наличия или продажи'

def observe(id,state,reason,alert=True):
    with db() as c:
        row=c.execute('SELECT status FROM offers WHERE id=?',(id,)).fetchone()
        status='removed' if row and row[0]=='removed' else ('available' if state=='available' else 'checking')
        c.execute('UPDATE offers SET status=?,source_state=?,reason=?,checked_at=?,updated_at=? WHERE id=?',
                  (status,state,reason,now(),now(),id))
    event(id,'info' if state in ('available','sold','checking') else 'warning',reason)
    key='issue:'+str(id)
    if state in ('unknown','challenge') and alert:
        signature=state+':'+reason
        if setting(key)!=signature:
            setting(key,signature)
            notify(f'problem:{id}:{now()}:{hashlib.sha256(signature.encode()).hexdigest()[:12]}',f'⚠️ LZT #{id}: {reason}. Автоснятие не выполнялось.')
    elif state=='available':setting(key,'')


def site_problem(reason):
    if setting('lzt_incident')!='1':
        setting('lzt_incident','1')
        setting('lzt_incident_start',now())
        notify('lzt-outage:'+str(now()),'⚠️ Часть страниц LZT не удаётся проверить: '+reason+'. Проверки продолжатся автоматически. Ошибки не считаются продажей. Подробности — в журнале; повторных сообщений по каждому лоту не будет.')

def site_recovered():
    if setting('lzt_incident')=='1':
        setting('lzt_incident','0')
        notify('lzt-recovered:'+str(now()),'✅ Проверка LZT восстановлена: последний цикл завершён без ошибок.')

def archive_offer(id):
    with LOCK,db() as c:
        row=c.execute('SELECT id FROM offers WHERE id=?',(id,)).fetchone()
        if row:c.execute('UPDATE offers SET archived_at=?,monitoring=0,auto_disable=0,updated_at=? WHERE id=?',(now(),now(),id))
    event(id,'info','Оффер убран из мониторинга и перенесён в архив; FunPay не изменялся')
    return {'ok':True}

def status_data():
    rows=offers()
    with db() as c:
        events=[dict(r) for r in c.execute('SELECT * FROM events ORDER BY id DESC LIMIT 100')]
        archived_urls=[r[0] for r in c.execute('SELECT url FROM offers WHERE archived_at>0')]
    hb=int(setting('worker_heartbeat') or 0)
    return {'offers':rows,'events':events,'archived_urls':archived_urls,'paused':setting('paused')=='1',
            'worker_online':now()-hb<90,'browser_status':setting('browser_status'),
            'interval_seconds':300,'server_time':now(),
            'creation_jobs':__import__('batch').jobs(),'services':__import__('operations').health_data(),'finance':__import__('operations').finance_data()}

class API(BaseHTTPRequestHandler):
    server_version='OfferStudio/2'
    def setup(self):
        super().setup()
        self.connection.settimeout(20)
    def log_message(self,*args):pass
    def send(self,code,body):
        raw=json.dumps(body,ensure_ascii=False).encode()
        self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store')
        self.end_headers();self.wfile.write(raw)
    def authorized(self):
        actual=self.headers.get('Authorization','')
        expected='Bearer '+(DATA/'api_token').read_text().strip()
        if not hmac.compare_digest(actual,expected):self.send(401,{'error':'Нет доступа'});return False
        return True
    def do_GET(self):
        if not self.authorized():return
        if self.path=='/v1/status':self.send(200,status_data())
        else:self.send(404,{'error':'Не найдено'})
    def do_POST(self):
        if not self.authorized():return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length<2 or length>2000000:raise ValueError('Неверный размер запроса')
            p=json.loads(self.rfile.read(length))
            if not isinstance(p,dict):raise ValueError('Ожидается объект JSON')
            if self.path=='/v1/batch':
                self.send(200,__import__('batch').enqueue(p.get('items')))
            elif self.path=='/v1/batch/retry':
                self.send(200,__import__('batch').retry(str(p.get('source_id',''))))
            elif self.path=='/v1/batch/cancel':
                self.send(200,__import__('batch').cancel())
            elif self.path=='/v1/offers':
                with LOCK:r=save_offer(p,self.server.verify_lot)
                self.send(200,r)
            elif self.path=='/v1/create':
                self.send(200,self.server.create_offer(p))
            elif self.path=='/v1/archive':
                id,_=canonical_lzt(p.get('url',''))
                self.send(200,archive_offer(id))
            elif self.path=='/v1/restore':
                id,_=canonical_lzt(p.get('url',''))
                with LOCK,db() as c:c.execute('UPDATE offers SET archived_at=0,monitoring=1,auto_disable=0,status="checking",source_state="unknown",updated_at=? WHERE id=?',(now(),id))
                event(id,'info','Оффер восстановлен из архива; автоснятие выключено до настройки')
                setting('check_now','1');self.send(200,{'ok':True})
            elif self.path=='/v1/control':
                action=p.get('action')
                if action not in ('pause','resume','check','auth'):raise ValueError('Неизвестная команда')
                if action=='auth':
                    setting('paused','1');setting('auth_requested','1')
                elif action!='check':setting('paused','1' if action=='pause' else '0')
                setting('check_now','0' if action=='auth' else '1');event(None,'info','Команда: '+action);self.send(200,{'ok':True})
            elif self.path=='/internal/disable':
                self.send(200,self.server.disable_offer(str(p['id'])))
            else:self.send(404,{'error':'Не найдено'})
        except (ValueError,KeyError,sqlite3.IntegrityError) as e:self.send(400,{'error':str(e)[:300]})
        except Exception:
            event(None,'error','Ошибка обращения к FunPay; проверьте сессию и журналы Cardinal')
            self.send(503,{'error':'FunPay не подтвердил действие. Проверьте сессию и журналы сервера.'})

def serve(verify,disable,create=None):
    server=ThreadingHTTPServer(('127.0.0.1',8765),API)
    server.create_offer=create
    server.verify_lot=verify;server.disable_offer=disable
    server.serve_forever()
