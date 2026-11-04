"""Durable sequential creation queue; interrupted jobs require explicit review."""
import json,threading,time
import core

def init():
    with core.db() as db:db.execute('CREATE TABLE IF NOT EXISTS creation_queue(source_id TEXT PRIMARY KEY,payload TEXT NOT NULL,state TEXT NOT NULL,detail TEXT NOT NULL,updated_at INTEGER NOT NULL)')

def jobs():
    init()
    with core.db() as db:return [dict(r) for r in db.execute('SELECT source_id,state,detail,updated_at FROM creation_queue ORDER BY updated_at DESC')]

def enqueue(items):
    init()
    if not isinstance(items,list) or not 1<=len(items)<=100:raise ValueError('От 1 до 100 лотов в очереди')
    valid=[]
    for item in items:
        id,_=core.canonical_lzt(item.get('url',''))
        if not item.get('title') or not item.get('description') or not item.get('buy_price'):raise ValueError('Не заполнены тексты или закупка для '+id)
        valid.append((id,json.dumps(item,ensure_ascii=False)))
    with core.db() as db:
        for id,payload in valid:
            row=core.get_offer(id)
            if row and row['funpay_id']:continue
            db.execute('INSERT OR IGNORE INTO creation_queue VALUES (?,?,"queued","Ожидает создания",?)',(id,payload,core.now()))
    return {'ok':True,'jobs':jobs()}

def cancel():
    init()
    with core.db() as db:db.execute('UPDATE creation_queue SET state="cancelled",detail="Отменено владельцем",updated_at=? WHERE state="queued"',(core.now(),))
    return {'ok':True}

def run_one(create):
    with core.db() as db:
        row=db.execute('SELECT * FROM creation_queue WHERE state="queued" ORDER BY updated_at LIMIT 1').fetchone()
        if not row:return False
        db.execute('UPDATE creation_queue SET state="running",updated_at=? WHERE source_id=?',(core.now(),row['source_id']))
    state='created';detail='Лот создан и привязан'
    try:create(json.loads(row['payload']))
    except Exception as ex:
        state='review';detail='Создание не подтверждено: '+type(ex).__name__+'. Проверьте журнал и лоты; автоматического повтора нет.'
    with core.db() as db:db.execute('UPDATE creation_queue SET state=?,detail=?,updated_at=? WHERE source_id=?',(state,detail,core.now(),row['source_id']))
    return True

def start(create):
    init()
    with core.db() as db:db.execute('UPDATE creation_queue SET state="review",detail="Прервано перезапуском: проверьте FunPay перед повтором" WHERE state="running"')
    def loop():
        while True:
            try:run_one(create)
            except Exception:core.event(None,'warning','Ошибка очереди создания')
            time.sleep(5)
    threading.Thread(target=loop,daemon=True,name='OfferStudio-Batch').start()

def retry(source_id):
    init()
    with core.db() as db:
        row=db.execute('SELECT state FROM creation_queue WHERE source_id=?',(source_id,)).fetchone()
        if not row or row['state'] not in ('review','cancelled'):raise ValueError('Этот элемент не ожидает ручного повтора')
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='creations'").fetchone()
        attempt=db.execute('SELECT stage FROM creations WHERE source_id=?',(source_id,)).fetchone() if exists else None
        if attempt and attempt['stage']=='pending':raise ValueError('FunPay мог создать лот. Сначала проверьте лоты и привяжите существующий: повтор заблокирован')
        db.execute('UPDATE creation_queue SET state="queued",detail="Повтор запрошен владельцем",updated_at=? WHERE source_id=?',(core.now(),source_id))
    return {'ok':True}
