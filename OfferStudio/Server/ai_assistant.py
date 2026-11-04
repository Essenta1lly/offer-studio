"""Bounded, persistent FunPay customer assistant. No account credentials enter prompts."""
import json, queue, re, threading, time
from pathlib import Path
import requests
import core
import operations
Q=queue.Queue(maxsize=100)
C=None
FP=None
LOCK=threading.RLock()

def init(cardinal,fp_lock):
    global C,FP
    C=cardinal;FP=fp_lock
    with core.db() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS ai_chats(chat TEXT PRIMARY KEY,paused INTEGER DEFAULT 0,lot INTEGER,last_id INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS ai_messages(chat TEXT,id INTEGER,role TEXT,text TEXT,ts INTEGER,PRIMARY KEY(chat,id));
        CREATE TABLE IF NOT EXISTS ai_context(chat TEXT PRIMARY KEY,order_id TEXT,lot_id INTEGER,reason TEXT);
        CREATE TABLE IF NOT EXISTS ai_usage(day TEXT PRIMARY KEY,n INTEGER DEFAULT 0);''')
    threading.Thread(target=loop,daemon=True,name='OfferStudio-AI').start()

def clean(text):
    text=re.sub(r'(?im)^.*(?:пароль|password|логин|login|verification code|код подтверждения)\s*[:=].*$', '[данные доступа скрыты]', text or '')
    text=re.sub(r'https?://\S+','[ссылка]',text or '')
    text=re.sub(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}','[почта скрыта]',text)
    text=re.sub(r'(?i)(пароль|password|token|api.key|код)\s*[:=]\s*\S+',r'\1: [скрыто]',text)
    return text[:2400]

def control(parts):
    cmd=parts[0].split('@')[0]
    if cmd=='/ai_status':
        with core.db() as db:n=db.execute('SELECT count(*) FROM ai_chats WHERE paused=1').fetchone()[0]
        return f'Нейропомощник: {"включён" if core.setting("ai_enabled")=="1" else "выключен"}\nПриостановлено диалогов: {n}\n/ai_on /ai_off /ai_resume CHAT_ID /ai_pause CHAT_ID'
    if cmd in ('/ai_on','/ai_off'):
        core.setting('ai_enabled','1' if cmd=='/ai_on' else '0');return 'Настройка нейропомощника сохранена'
    if cmd in ('/ai_resume','/ai_pause') and len(parts)==2 and bool(re.fullmatch(r'(?:[1-9]\d*|users-\d+-\d+)',parts[1])):
        with core.db() as db:db.execute('INSERT INTO ai_chats(chat,paused) VALUES (?,?) ON CONFLICT(chat) DO UPDATE SET paused=excluded.paused',(parts[1],int(cmd=='/ai_pause')))
        return 'Диалог включён' if cmd=='/ai_resume' else 'Диалог приостановлен'
    return 'Команды: /ai_status /ai_on /ai_off /ai_resume CHAT_ID /ai_pause CHAT_ID'

def on_message(c,e,*args):
    with LOCK:_on_message(c,e,*args)

def _on_message(c,e,*args):
    try:
        m=e.message;chat=str(m.chat_id)
        if not re.fullmatch(r'(?:[1-9]\d*|users-\d+-\d+)',chat) or not getattr(m,'author_id',None) or getattr(getattr(m,'type',None),'name','')!='NON_SYSTEM':return
        own=m.author_id==c.account.id
        with core.db() as db:
            db.execute('INSERT OR IGNORE INTO ai_chats(chat) VALUES (?)',(chat,))
            if own:
                if not m.by_bot:db.execute('UPDATE ai_chats SET paused=1 WHERE chat=?',(chat,))
                return
            if core.setting('ai_enabled')!='1':return
            if db.execute('SELECT paused FROM ai_chats WHERE chat=?',(chat,)).fetchone()[0]:return
            if not m.text:return
            inserted=db.execute('INSERT OR IGNORE INTO ai_messages VALUES (?,?,?,?,?)',(chat,m.id,'user',clean(m.text),core.now())).rowcount
            if not inserted:return
            db.execute('UPDATE ai_chats SET last_id=? WHERE chat=?',(m.id,chat))
            view=getattr(m,'buyer_viewing',None)
            fid=getattr(view,'lot_id',None)
            if fid and str(fid).isdigit():db.execute('UPDATE ai_chats SET lot=? WHERE chat=?',(int(fid),chat))
        try:Q.put_nowait((chat,m.id,m.chat_name,m.interlocutor_id or m.author_id))
        except queue.Full:handoff(chat,'Очередь помощника переполнена')
    except Exception:core.event(None,'error','AI: ошибка при обработке события (без содержимого переписки)')

def handoff(chat,reason):
    with core.db() as db:db.execute('INSERT INTO ai_context(chat,reason) VALUES (?,?) ON CONFLICT(chat) DO UPDATE SET reason=excluded.reason',(chat,reason))
    with core.db() as db:db.execute('UPDATE ai_chats SET paused=1 WHERE chat=?',(chat,))
    core.notify('ai-handoff:'+chat+':'+str(core.now()//3600),f'💬 Требуется ваш ответ FunPay\nhttps://funpay.com/chat/?node={chat}\n{reason}\nБот приостановлен. /ai_resume {chat}')

def generate(messages):
    cfg=json.loads((core.DATA/'ai_secrets.json').read_text())
    failures=[]
    # At most two providers per request; no key rotation to circumvent rate limits.
    for provider in cfg['providers'][:2]:
        try:
            key=provider['keys'][0]
            if provider['type']=='gemini':
                r=requests.post('https://generativelanguage.googleapis.com/v1beta/models/'+provider['model']+':generateContent',headers={'x-goog-api-key':key},json={'systemInstruction':{'parts':[{'text':messages[0]['content']}]},'contents':[{'role':'model' if m['role']=='assistant' else 'user','parts':[{'text':m['content']}]} for m in messages[1:]],'generationConfig':{'temperature':0.2,'maxOutputTokens':700,'responseMimeType':'application/json'}},timeout=(8,35))
                r.raise_for_status();text=r.json()['candidates'][0]['content']['parts'][0]['text']
            else:
                r=requests.post('https://openrouter.ai/api/v1/chat/completions',headers={'Authorization':'Bearer '+key},json={'model':provider['model'],'messages':messages,'temperature':0.2,'max_tokens':700,'response_format':{'type':'json_object'}},timeout=(8,35))
                r.raise_for_status();text=r.json()['choices'][0]['message']['content']
            result=json.loads(text)
            if result.get('action') not in ('reply','handoff','ignore'):raise ValueError()
            answer=result.get('text','')
            if not isinstance(answer,str) or len(answer)>1800:raise ValueError()
            if re.search(r'https?://|[\w.+-]+@[\w.-]+\.|sk-or-|AQ\.|(?i:пароль\s*[:=])',answer):raise ValueError()
            operations.health('ai',True,'Ответ модели получен: '+provider['type'])
            return result
        except Exception as ex:failures.append(type(ex).__name__)
    operations.health('ai',False,'Провайдеры недоступны')
    raise RuntimeError('Providers unavailable: '+','.join(failures))

def resolve_context(chat,name,buyer,latest):
    with FP:
        obj=C.account.get_chat(int(chat) if chat.isdigit() else chat,with_history=False)
        sales=C.account.get_sales(buyer=name)[1] if name else []
    # Explicit order references still require authenticated ownership verification.
    refs=re.findall(r'(?:#|orders/)([A-Z0-9]{8})\b',latest,re.I)
    if not refs and re.fullmatch(r'[A-Z0-9]{8}',latest.strip(),re.I):refs=[latest.strip()]
    with core.db() as db:previous=db.execute('SELECT order_id FROM ai_context WHERE chat=?',(chat,)).fetchone()
    candidates=[o.id for o in sales]
    chosen=refs[0].upper() if len(set(refs))==1 else (previous['order_id'] if not refs and previous and previous['order_id'] in candidates else (candidates[0] if len(candidates)==1 and not refs else None))
    if chosen:
        with FP:order=C.account.get_order(chosen)
        if order.seller_id!=C.account.id or order.buyer_id!=buyer:raise ValueError('Order ownership mismatch')
        with core.db() as db:db.execute('INSERT INTO ai_context(chat,order_id) VALUES (?,?) ON CONFLICT(chat) DO UPDATE SET order_id=excluded.order_id,lot_id=NULL',(chat,order.id))
        operations.capture_order(order)
        with core.db() as db:
            linked=db.execute('SELECT funpay_id FROM order_links WHERE order_id=?',(order.id,)).fetchone()
            if linked:db.execute('UPDATE ai_context SET lot_id=? WHERE chat=?',(linked[0],chat))
        return {'order_id':order.id,'status':order.status.name,'price':str(order.sum),
                'currency':order.currency.name,'title':clean(order.short_description),
                'description':clean(order.full_description),
                'category':order.subcategory.name if order.subcategory else '',
                'game':order.subcategory.category.name if order.subcategory else ''}
    if candidates:
        return {'clarification_required':True,'instruction':'Попроси уточнить номер заказа. Не выбирай заказ самостоятельно.'}
    link=getattr(obj,'looking_link','') or ''
    match=re.search(r'/lots/offer\?(?:id|offer)=(\d+)',link)
    if not match:
        return {'clarification_required':True,'instruction':'Попроси номер заказа или уточнение товара. Данных о товаре пока нет.'}
    with FP:fields=C.account.get_lot_fields(int(match[1]))
    with core.db() as db:db.execute('INSERT INTO ai_context(chat,lot_id) VALUES (?,?) ON CONFLICT(chat) DO UPDATE SET lot_id=excluded.lot_id',(chat,int(match[1])))
    safe={k:clean(fields.fields.get(k,'')) for k in ('fields[summary][ru]','fields[summary][en]','fields[desc][ru]','fields[desc][en]')}
    safe.update(price=str(fields.price),currency=fields.currency.name,active=bool(fields.active))
    return safe

def process(item):
    chat,mid,name,interlocutor=item
    with core.db() as db:
        state=db.execute('SELECT * FROM ai_chats WHERE chat=?',(chat,)).fetchone()
        if not state or state['paused'] or state['last_id']!=mid or core.setting('ai_enabled')!='1':return
        fid=state['lot']
    with core.db() as db:
        latest=db.execute("SELECT text FROM ai_messages WHERE chat=? AND id=?",(chat,mid)).fetchone()[0]
    safe=resolve_context(chat,name,interlocutor,latest)
    day=time.strftime('%Y-%m-%d',time.gmtime())
    with core.db() as db:
        db.execute('INSERT OR IGNORE INTO ai_usage(day) VALUES (?)',(day,))
        if db.execute('SELECT n FROM ai_usage WHERE day=?',(day,)).fetchone()[0]>=200:raise RuntimeError('Daily limit')
        db.execute('UPDATE ai_usage SET n=n+1 WHERE day=?',(day,))
        history=list(db.execute('SELECT role,text FROM ai_messages WHERE chat=? ORDER BY ts DESC,rowid DESC LIMIT 10',(chat,)))[::-1]
    instructions=(Path(__file__).with_name('ai_instructions.txt')).read_text()
    messages=[{'role':'system','content':instructions+'\nДАННЫЕ СИСТЕМЫ: '+json.dumps(safe,ensure_ascii=False)}]+[{'role':r['role'],'content':r['text']} for r in history]
    result=generate(messages)
    with LOCK:
        with core.db() as db:state=db.execute('SELECT * FROM ai_chats WHERE chat=?',(chat,)).fetchone()
        if state['paused'] or state['last_id']!=mid or core.setting('ai_enabled')!='1':return
        if result['action']=='ignore':return
        if result['text']:
            # Mark attempt before sending: ambiguous network failures must never auto-resend.
            with core.db() as db:db.execute('INSERT OR IGNORE INTO ai_messages VALUES (?,?,?,?,?)',(chat,-mid,'assistant',result['text'],core.now()))
            with FP:C.account.send_message(int(chat) if chat.isdigit() else chat,result['text'],chat_name=name,interlocutor_id=interlocutor)
            core.event(None,'info',f'AI: ответ отправлен в чат {chat}')
        if result['action']=='handoff':handoff(chat,clean(str(result.get('reason') or 'Жалоба, спор или требуется уточнение по товару'))[:300])

def loop():
    while True:
        item=Q.get()
        try:
            time.sleep(3)
            process(item)
        except Exception as ex:
            core.event(None,'warning','AI: ответ не подтверждён: '+type(ex).__name__)
            handoff(item[0],'Ошибка нейросети или отправки. Требуется ручной ответ.')
        finally:
            Q.task_done()
            with core.db() as db:db.execute('DELETE FROM ai_messages WHERE ts<?',(core.now()-7*86400,))
