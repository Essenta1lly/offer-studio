"""Operational health and explicit order-to-source accounting."""
import re
from urllib.parse import urlparse,parse_qs,unquote
from decimal import Decimal,InvalidOperation
import core

def init():
    with core.db() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS service_health(name TEXT PRIMARY KEY,state TEXT NOT NULL,success_at INTEGER NOT NULL DEFAULT 0,checked_at INTEGER NOT NULL,detail TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS order_links(order_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,funpay_id INTEGER NOT NULL,buy_price TEXT NOT NULL,currency TEXT NOT NULL,method TEXT NOT NULL,linked_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS source_identities(source_id TEXT PRIMARY KEY,identity TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS order_adjustments(order_id TEXT PRIMARY KEY,refund TEXT NOT NULL DEFAULT '0',cost_recovered TEXT NOT NULL DEFAULT '0');''')

def health(name,ok,detail=''):
    init()
    with core.db() as c:
        old=c.execute('SELECT * FROM service_health WHERE name=?',(name,)).fetchone()
        c.execute('INSERT INTO service_health VALUES (?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET state=excluded.state,success_at=CASE WHEN excluded.state="ok" THEN excluded.success_at ELSE service_health.success_at END,checked_at=excluded.checked_at,detail=excluded.detail',(name,'ok' if ok else 'error',core.now() if ok else 0,core.now(),detail))
    if old and old['state']!=('ok' if ok else 'error') and name in ('funpay','orders'):
        core.notify('health:'+name+':'+str(core.now()),('✅ Восстановлено: ' if ok else '⚠️ Сбой: ')+name+' · '+detail)

def health_data():
    init()
    labels={'lzt':'LZT','funpay':'Авторизация FunPay','orders':'Получение заказов','telegram':'Доставка Telegram','ai':'Нейропомощник'}
    limits={'lzt':900,'funpay':660,'orders':660,'telegram':86400,'ai':86400}
    with core.db() as c:
        states={r['name']:dict(r) for r in c.execute('SELECT * FROM service_health')}
        pending=c.execute('SELECT count(*),min(next_try) FROM outbox WHERE sent=0').fetchone()[0]
    rows=[]
    for name,label in labels.items():
        row=states.get(name,dict(state='unknown',success_at=0,checked_at=0,detail='Успешная операция ещё не зарегистрирована'))
        row.update(name=name,label=label)
        if row['state']=='ok' and core.now()-row['checked_at']>limits[name]:row['state']='stale'
        if name=='lzt':
            hb=int(core.setting('worker_heartbeat') or 0)
            row['state']='error' if core.now()-hb>90 else ('error' if core.setting('lzt_incident')=='1' else row['state'])
            if row['state']=='error':row['detail']=('Нет связи с браузерным процессом' if core.now()-hb>90 else 'Часть страниц не проверена; '+core.setting('browser_status'))
        if name=='telegram' and pending:row['detail']+=f' · в очереди: {pending}'
        if name=='ai' and core.setting('ai_enabled')!='1':row.update(state='paused',detail='Выключен владельцем')
        rows.append(row)
    return rows

def amount(raw):
    v=Decimal(re.sub(r'[^\d,.\-]','',str(raw)).replace(',','.'))
    if not v.is_finite() or v<0:raise ValueError('Некорректная сумма')
    return v

def link_order(order_id,source_id,method='owner'):
    init();offer=core.get_offer(source_id)
    if not offer or not offer['verified'] or not offer['funpay_id']:raise ValueError('Нужен проверенный лот FunPay')
    cost=amount(offer['buy_price'])
    with core.db() as c:
        if not c.execute('SELECT 1 FROM orders WHERE id=?',(order_id,)).fetchone():raise ValueError('Заказ не найден')
        old=c.execute('SELECT * FROM order_links WHERE order_id=?',(order_id,)).fetchone()
        if old and old['source_id']!=str(source_id):raise ValueError('Заказ уже привязан к другому аккаунту')
        c.execute('INSERT OR IGNORE INTO order_links VALUES (?,?,?,?,?,?,?)',(order_id,str(source_id),offer['funpay_id'],str(cost),'RUB',method,core.now()))
    core.event(str(source_id),'info',f'Заказ #{order_id} связан с FunPay #{offer["funpay_id"]}; способ: {method}')

def identity(text):
    for url in re.findall(r'https?://[^\s<>"\\]+',text):
        u=urlparse(url)
        if u.hostname not in ('www.op.gg','op.gg'):continue
        q=parse_qs(u.query)
        if q.get('q') and q.get('region'):return q['region'][0].lower()+':'+q['q'][0].lower()
        m=re.search(r'/summoners/([^/]+)/([^/?]+)',unquote(u.path))
        if m:return m[1].lower()+':'+m[2].rsplit('-',1)[0].lower()+'#'+m[2].rsplit('-',1)[-1].lower()
    return None

def capture_order(order):
    # A source reference is embedded in new listings and survives into the order.
    text=(getattr(order,'full_description','') or '')+' '+(getattr(order,'description','') or '')
    ids=set(re.findall(r'OS-(\d{5,})\b',text))
    if len(ids)==1:
        link_order(order.id,ids.pop(),'source_reference');return
    ident=identity(text)
    if ident:
        with core.db() as db:matches=db.execute('SELECT source_id FROM source_identities WHERE identity=?',(ident,)).fetchall()
        if len(matches)==1:link_order(order.id,matches[0][0],'opgg_identity')

def finance_data():
    init()
    with core.db() as c:
        rows=[dict(r) for r in c.execute('SELECT o.*,l.source_id,l.funpay_id,l.buy_price cost,l.method,a.refund,a.cost_recovered FROM orders o LEFT JOIN order_links l ON o.id=l.order_id LEFT JOIN order_adjustments a ON o.id=a.order_id ORDER BY o.ts DESC')]
    groups={s:dict(count=0,revenue=Decimal(0),cost=Decimal(0),profit=Decimal(0),unknown=0) for s in ('CLOSED','PAID','REFUNDED','PARTIALLY_REFUNDED')}
    for row in rows:
        g=groups.get(row['status'])
        if g is None:continue
        g['count']+=1
        if row['currency']!='RUB':g['unknown']+=1;continue
        sale=amount(row['price']);refund=amount(row['refund'] or 0)
        if row['status']=='REFUNDED':refund=sale
        if row['status']=='PARTIALLY_REFUNDED' and row['refund'] is None:
            g['unknown']+=1;continue
        g['revenue']+=sale-refund
        if row['cost'] is None:g['unknown']+=1;continue
        cost=amount(row['cost'])-amount(row['cost_recovered'] or 0)
        g['cost']+=cost;g['profit']+=sale-refund-cost
    return {'groups':{s:{k:str(v) if isinstance(v,Decimal) else v for k,v in g.items()} for s,g in groups.items()},'orders':rows}

def finance_text():
    data=finance_data();text='📊 Продажи и возвраты LoL\nБез вычета комиссии\n'
    titles={'CLOSED':'Завершённые','PAID':'Ожидаемые (оплачены)','REFUNDED':'Полные возвраты','PARTIALLY_REFUNDED':'Частичные возвраты'}
    for s,g in data['groups'].items():
        text+=f'\n{titles[s]}: {g["count"]}\nВыручка после возвратов: {g["revenue"]} ₽\nЗакупка: {g["cost"]} ₽\nПрибыль: {g["profit"]} ₽\n'
        if g['unknown']:text+=f'⚠️ Без полного расчёта: {g["unknown"]}. Нужна связь/сумма возврата.\n'
    for o in data['orders']:
        text+=f'\n#{o["id"]} · {o["status"]} · {o["price"]} {o["currency"]}\n'
        if o['source_id']:
            text+=f'LZT #{o["source_id"]} → FunPay #{o["funpay_id"]} · закупка {o["cost"]} ₽\n'
            if o['currency']=='RUB' and o['status'] in ('PAID','CLOSED'):
                text+=f'Прибыль: {amount(o["price"])-amount(o["cost"]):.2f} ₽\n'
        else:text+='Связь не подтверждена: /os_order ЗАКАЗ LZT_ID\n'
    return text
