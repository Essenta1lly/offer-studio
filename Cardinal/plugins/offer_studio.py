"""FunPayCardinal adapter: ownership verification, disable-only actions, Telegram UI."""
import html, hmac, json, logging, os, sys, threading, time, re
from decimal import Decimal, InvalidOperation
from pathlib import Path

NAME='Offer Studio'
VERSION='2.0.0'
DESCRIPTION='Мониторинг LZT, синхронизация macOS, снятие связанных лотов и уведомления'
CREDITS='Offer Studio'
UUID='bd02c5c4-414c-49a1-a660-cd43a2eff1cc'
SETTINGS_PAGE=False
BIND_TO_DELETE=None
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'storage'/'offer_studio'/'code'))
import core
import operations
log=logging.getLogger('OfferStudio')
_cardinal=None
_mirror=None
FP_LOCK=threading.RLock()
NAMES={'available':'в наличии','checking':'проверяется','removed':'снято с продажи'}

def create_offer(payload):
    import creation
    with core.LOCK,FP_LOCK:
        result=creation.create(_cardinal.account,payload)
        if result.get('existing'):return result
        row=core.save_offer(dict(payload,funpay_url=f'https://funpay.com/lots/offer?id={result["funpay_id"]}',monitoring=True,auto_disable=True),verify_lot)
        core.event(row['id'],'success',f'Создан и привязан FunPay #{row["funpay_id"]}, цена закупки ×2,5')
        core.notify('created:'+row['id'],f'✅ Создан FunPay #{row["funpay_id"]} для LZT #{row["id"]}. Цена {result["price"]} RUB. Автовыдача выключена.')
        return {'ok':True,'offer':row}

def verify_lot(id):
    with FP_LOCK:
        fields=_cardinal.account.get_lot_fields(id)
        if fields.lot_id!=id or str(fields.fields.get('offer_id',''))!=str(id):
            raise ValueError('Не удалось подтвердить владение этим лотом')
        sub=fields.subcategory
        if not sub or 'league of legends' not in sub.category.name.lower() or not any(x in sub.name.lower() for x in ('аккаунт','account')):
            raise ValueError('Поддерживаются только ваши лоты аккаунтов League of Legends')
        return {'id':id,'title':fields.title_ru,'price':fields.price,'currency':getattr(fields.currency,'name',str(fields.currency)), 'active':fields.active}

def disable_offer(id,manual=False):
    with core.LOCK,FP_LOCK:
        row=core.get_offer(id)
        if not row or row['archived_at'] or not row['funpay_id'] or not row['verified']:
            raise ValueError('Нет проверенной связи с вашим лотом FunPay')
        if not manual:
            if core.setting('paused')=='1' or not row['monitoring'] or not row['auto_disable'] or row['source_state']!='sold':
                raise ValueError('Автоснятие не разрешено настройками или продажа не подтверждена')
        fid=row['funpay_id']
        verify_lot(fid)
        fields=_cardinal.account.get_lot_fields(fid)
        # Preserve every original field including delivery stock; change ONLY active.
        if fields.fields.get('active')=='on':
            fields.active=False
            fields.fields['active']=''
            _cardinal.account.save_lot(fields)
        confirmed=_cardinal.account.get_lot_fields(fid)
        if confirmed.fields.get('active')=='on':
            core.event(id,'error','FunPay не подтвердил снятие лота')
            raise ValueError('Лот FunPay остался активным')
        with core.db() as c:
            c.execute('UPDATE offers SET status="removed",funpay_active=0,removed_at=?,updated_at=?,reason=? WHERE id=?',
                      (core.now(),core.now(),'Снятие подтверждено на FunPay',id))
        core.event(id,'success',f'Лот FunPay #{fid} снят; галочка «Активное» проверена')
        core.notify('removed:'+id+':'+str(fid),f'✅ Лот снят с продажи\nLZT #{id}\nFunPay #{fid}\nЗакупка: {row["buy_price"] or "—"}\nПродажа: {row["sell_price"] or "—"} {row["currency"]}\nПричина: '+('команда владельца' if manual else 'аккаунт продан на LZT'))
        return {'ok':True,'offer':core.get_offer(id)}

def money(value):
    return Decimal(re.sub(r'[^0-9,.\-]','',str(value)).replace(',','.'))

def purchase_for_order(order,offers):
    # Match actual price AND both inventory counts; never match by price alone.
    desc=order['description'] or ''
    champs=re.search(r'чемпионы\s+(\d+)',desc,re.I)
    skins=re.search(r'скины\s+(\d+)',desc,re.I)
    if not champs or not skins or order['currency']!='RUB':return None
    matches=[]
    for offer in offers:
        title=offer['title'] or ''
        c=re.search(r'(\d+)\s+ЧЕМПИОН',title,re.I)
        sk=re.search(r'(\d+)\s+СКИН',title,re.I)
        if not c or not sk or c[1]!=champs[1] or sk[1]!=skins[1]:continue
        try:
            if money(offer['sell_price'])==money(order['price']):matches.append(offer)
        except InvalidOperation:pass
    if len(matches)!=1:return None
    try:return money(matches[0]['buy_price'])
    except InvalidOperation:return None

def stats_text():
    return operations.finance_text()

def core_order_exists(id):
    with core.db() as db:return bool(db.execute('SELECT 1 FROM orders WHERE id=?',(id,)).fetchone())

def sales_sync_loop():
    while True:
        try:
            with core.db() as db:linked={r[0] for r in db.execute('SELECT order_id FROM order_links')}
            with FP_LOCK:
                _cardinal.account.get()
                operations.health('funpay',True,'Вход подтверждён запросом FunPay')
                result=_cardinal.account.get_sales(section='lot-85')
            for order in result[1]:
                on_order(_cardinal,type('Event',(),{'order':order})())
                if order.id not in linked and core_order_exists(order.id):
                    with FP_LOCK:full=_cardinal.account.get_order(order.id)
                    operations.capture_order(full)
            with core.db() as db:known=[r[0] for r in db.execute('SELECT id FROM orders') if r[0] not in {o.id for o in result[1]}]
            for start in range(0,len(known),10):
                with FP_LOCK:details=_cardinal.account.get_orders_by_ids(*known[start:start+10])
                for full in details.values():on_order(_cardinal,type('Event',(),{'order':full})())
            operations.health('orders',True,'Последние и ранее известные заказы сверены')
            core.setting('sales_synced',str(core.now()))
        except Exception as ex:
            operations.health('orders',False,type(ex).__name__)
            if 'Unauthorized' in type(ex).__name__:operations.health('funpay',False,'Требуется вход FunPay')
            core.event(None,'warning','Синхронизация заказов FunPay: '+type(ex).__name__)
            time.sleep(30)
            continue
        time.sleep(300)

def authorized(uid):return bool((_cardinal.telegram and uid in _cardinal.telegram.authorized_users) or uid in mirror_ids())

def mirror_ids():
    try:return json.loads((core.DATA/'mirror_chats.json').read_text())
    except (ValueError,FileNotFoundError):return []

def reply(bot,chat_id,text,mirror=True):
    remaining=text
    chunks=[]
    while remaining:
        cut=len(remaining) if len(remaining)<=3500 else remaining.rfind('\n',0,3500)
        if cut<=0:cut=3500
        chunks.append(remaining[:cut]);remaining=remaining[cut:].lstrip('\n')
    for chunk in chunks:
        bot.send_message(chat_id,chunk,parse_mode=None,disable_web_page_preview=True)
        if mirror and _mirror and bot is not _mirror:
            for uid in mirror_ids():
                if authorized(uid):
                    try:_mirror.send_message(uid,chunk,parse_mode=None,disable_web_page_preview=True)
                    except Exception:core.event(None,'warning','Не удалось продублировать ответ в новый бот')

def handle(bot,message):
    if message.chat.type!='private':return
    text=(message.text or '').strip();parts=text.split();cmd=parts[0].split('@')[0] if parts else ''
    if bot is _mirror and cmd=='/start' and len(parts)==2:
        pairing=core.DATA/'mirror_pairing'
        if pairing.exists() and hmac.compare_digest(parts[1],pairing.read_text().strip()):
            ids=mirror_ids()
            if message.chat.id not in ids:ids.append(message.chat.id)
            (core.DATA/'mirror_chats.json').write_text(json.dumps(ids))
            pairing.unlink()
            core.event(None,'success','Новый Telegram-бот привязан владельцем по одноразовому коду')
    if not authorized(message.from_user.id):
        if bot is _mirror and cmd=='/start':reply(bot,message.chat.id,'Для привязки используйте одноразовую ссылку из приложения/чата настройки.',mirror=False)
        return
    try:
        if cmd=='/os_order' and len(parts)==3:
            with FP_LOCK:order=_cardinal.account.get_order(parts[1].lstrip('#'))
            if order.seller_id!=_cardinal.account.id:raise ValueError('Заказ не принадлежит вам')
            operations.link_order(order.id,parts[2].lstrip('#'))
            answer='✅ Прямая связь и закупочная цена зафиксированы'
        elif cmd=='/os_refund' and len(parts)==4:
            refund=operations.amount(parts[2]);recovered=operations.amount(parts[3])
            with core.db() as db:
                order=db.execute('SELECT price FROM orders WHERE id=?',(parts[1],)).fetchone()
                if not order:raise ValueError('Заказ не найден')
                if refund>operations.amount(order['price']):raise ValueError('Возврат больше суммы заказа')
                link=db.execute('SELECT buy_price FROM order_links WHERE order_id=?',(parts[1],)).fetchone()
                if recovered and (not link or recovered>operations.amount(link['buy_price'])):raise ValueError('Возврат закупочных затрат больше известной закупки')
                db.execute('INSERT INTO order_adjustments VALUES (?,?,?) ON CONFLICT(order_id) DO UPDATE SET refund=excluded.refund,cost_recovered=excluded.cost_recovered',(parts[1],str(refund),str(recovered)))
            answer='✅ Сумма возврата и возврат закупочных затрат сохранены'
        elif cmd.startswith('/ai_'):
            import ai_assistant
            answer=ai_assistant.control(parts)
        elif cmd=='/start' and bot is _mirror:
            ids=mirror_ids()
            if message.chat.id not in ids:ids.append(message.chat.id)
            (core.DATA/'mirror_chats.json').write_text(json.dumps(ids))
            answer='✅ Дублирование Offer Studio подключено. Команды: /os_status /os_lots /os_stats /os_logs /os_check /os_pause /os_resume'
        elif cmd in ('/os','/os_help'):
            answer=('Offer Studio\n/os_status — состояние сервера\n/os_lots — все связи и статусы\n/os_stats — закупка и продажа\n/os_logs — журнал\n/os_check — проверить сейчас\n/os_pause — пауза автодействий\n/os_resume — включить проверки\n/os_off LZT_ID — снять связанный лот FunPay\n/os_monitor LZT_ID on|off — включить/выключить проверку\n/os_link LZT_URL FUNPAY_URL — связать и включить автоснятие\n/os_price LZT_ID ЗАКУПКА ПРОДАЖА — цены в RUB\n/os_remove LZT_ID — убрать из мониторинга\n/os_restore LZT_ID — восстановить из архива\n/os_health — состояние всех служб\n/os_order ЗАКАЗ LZT_ID — точная связь заказа\n/os_refund ЗАКАЗ СУММА ВОЗВРАТ_ЗАКУПКИ — суммы возвратов\n/ai_status — управление помощником')
        elif cmd=='/os_status':
            s=core.status_data();answer=f'Сервер: {"работает" if s["worker_online"] else "нет связи с браузерным процессом"}\nРежим: {"пауза" if s["paused"] else "проверки каждые 5 минут"}\nБраузер: {s["browser_status"]}\nСвязей: {len(s["offers"])}'
        elif cmd=='/os_health':
            answer='\n'.join(r['label']+': '+r['state']+' · '+r['detail'] for r in operations.health_data())
        elif cmd=='/os_lots':
            answer='\n\n'.join(f'#{r["id"]} → FunPay #{r["funpay_id"] or "—"}\n{NAMES[r["status"]]} · {r["reason"]}\nЗакупка {r["buy_price"] or "—"} / продажа {r["sell_price"] or "—"} {r["currency"]}' for r in core.offers()) or 'Связей пока нет'
        elif cmd=='/os_stats':answer=stats_text()
        elif cmd=='/os_logs':
            with core.db() as c:rows=c.execute('SELECT * FROM events ORDER BY id DESC LIMIT 15').fetchall()
            answer='\n'.join(time.strftime('%d.%m %H:%M UTC',time.gmtime(r['ts']))+f' [{r["level"]}] #{r["offer_id"] or "—"}: {r["message"]}' for r in rows) or 'Журнал пуст'
        elif cmd in ('/os_pause','/os_resume','/os_check'):
            if cmd!='/os_check':core.setting('paused','1' if cmd=='/os_pause' else '0')
            core.setting('check_now','1');core.event(None,'info',cmd)
            answer='⏸ Автоматические действия приостановлены' if cmd=='/os_pause' else '✅ Команда принята. Результат появится в журнале и уведомлениях.'
        elif cmd=='/os_remove' and len(parts)==2:
            core.archive_offer(parts[1]);answer='✅ Убрано из мониторинга. Сам лот FunPay не изменялся.'
        elif cmd=='/os_restore' and len(parts)==2:
            with core.db() as c:c.execute('UPDATE offers SET archived_at=0,monitoring=0,auto_disable=0,updated_at=? WHERE id=?',(core.now(),parts[1]))
            answer='✅ Восстановлено из архива. Включите мониторинг и автоснятие в приложении.'
        elif cmd=='/os_off' and len(parts)==2:
            disable_offer(parts[1],manual=True);answer='✅ Снятие подтверждено на FunPay'
        elif cmd=='/os_monitor' and len(parts)==3 and parts[2] in ('on','off'):
            if not core.get_offer(parts[1]):raise ValueError('Связь не найдена')
            with core.db() as c:c.execute('UPDATE offers SET monitoring=?,updated_at=? WHERE id=?',(int(parts[2]=='on'),core.now(),parts[1]))
            core.setting('check_now','1');answer='✅ Настройка мониторинга сохранена'
        elif cmd=='/os_link' and len(parts)==3:
            r=core.save_offer({'url':parts[1],'funpay_url':parts[2],'monitoring':True,'auto_disable':True},verify_lot)
            answer=f'✅ Связано: LZT #{r["id"]} → FunPay #{r["funpay_id"]}. Автоснятие включено.'
        elif cmd=='/os_price' and len(parts)==4:
            if not core.get_offer(parts[1]):raise ValueError('Связь не найдена')
            buy=Decimal(parts[2].replace(',','.'));sell=Decimal(parts[3].replace(',','.'))
            if buy<0 or sell<0 or not buy.is_finite() or not sell.is_finite():raise ValueError('Цены должны быть положительными числами')
            with core.db() as c:c.execute('UPDATE offers SET buy_price=?,sell_price=?,currency="RUB",updated_at=? WHERE id=?',(str(buy),str(sell),core.now(),parts[1]))
            answer='✅ Плановые цены сохранены. Цена на FunPay этой командой не меняется.'
        else:answer='Неизвестная команда. /os_help — список команд.'
    except (ValueError,InvalidOperation) as e:answer='⚠️ '+str(e)[:400]
    except Exception:
        core.event(None,'error','Команда не выполнена: ошибка FunPay или соединения')
        answer='⚠️ Действие не подтверждено. Проверьте /os_logs и авторизацию FunPay.'
        log.exception('Offer Studio command failed')
    reply(bot,message.chat.id,answer)

def assistant_buttons(dedup):
    if not dedup.startswith('ai-handoff:'):return None
    from telebot.types import InlineKeyboardMarkup,InlineKeyboardButton
    chat=dedup.split(':')[1]
    kb=InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton('Ответить самому',callback_data='osa:pause:'+chat),InlineKeyboardButton('Включить помощника',callback_data='osa:resume:'+chat))
    kb.row(InlineKeyboardButton('Открыть чат',url='https://funpay.com/chat/?node='+chat))
    with core.db() as db:ctx=db.execute('SELECT * FROM ai_context WHERE chat=?',(chat,)).fetchone()
    if ctx:
        if ctx['order_id']:kb.row(InlineKeyboardButton('Открыть заказ',url='https://funpay.com/orders/'+ctx['order_id']+'/'))
        if ctx['lot_id']:kb.row(InlineKeyboardButton('Открыть лот',url='https://funpay.com/lots/offer?id='+str(ctx['lot_id'])))
    return kb

def assistant_callback(bot,call):
    if call.message.chat.type!='private' or not authorized(call.from_user.id):return
    import ai_assistant
    _,action,chat=call.data.split(':',2)
    answer=ai_assistant.control(['/ai_'+('resume' if action=='resume' else 'pause'),chat])
    bot.answer_callback_query(call.id,answer)

def outbox_loop():
    with core.db() as c:c.execute('''CREATE TABLE IF NOT EXISTS deliveries (
       event_id INTEGER,channel TEXT,chat_id INTEGER,PRIMARY KEY(event_id,channel,chat_id))''')
    while True:
        try:
            bots=[('cardinal',_cardinal.telegram.bot,list(_cardinal.telegram.authorized_users))] if _cardinal.telegram else []
            if _mirror:bots.append(('mirror',_mirror,mirror_ids()))
            with core.db() as c:messages=c.execute('SELECT * FROM outbox WHERE next_try<=? AND sent=0 ORDER BY id LIMIT 15',(core.now(),)).fetchall()
            for msg in messages:
                success=True;recipients=0
                for channel,bot,ids in bots:
                    for uid in ids:
                        if not authorized(uid):continue
                        recipients+=1
                        with core.db() as c:done=c.execute('SELECT 1 FROM deliveries WHERE event_id=? AND channel=? AND chat_id=?',(msg['id'],channel,uid)).fetchone()
                        if done:continue
                        try:
                            bot.send_message(uid,msg['text'],parse_mode=None,disable_web_page_preview=True,reply_markup=assistant_buttons(msg['dedup']))
                            with core.db() as c:c.execute('INSERT OR IGNORE INTO deliveries VALUES (?,?,?)',(msg['id'],channel,uid))
                        except Exception:
                            success=False
                            operations.health('telegram',False,'Ошибка доставки, повтор запланирован')
                if success and recipients>0:operations.health('telegram',True,'Доставлено всем получателям')
                with core.db() as c:c.execute('UPDATE outbox SET sent=?,attempts=attempts+1,next_try=? WHERE id=?',(int(success and recipients>0),core.now()+min(3600,30*(msg['attempts']+1)),msg['id']))
        except Exception:log.exception('Offer Studio outbox error')
        time.sleep(3)

def on_order(c,e,*args):
    try:
        o=e.order;sub=getattr(o,'subcategory',None)
        if not sub or 'league of legends' not in sub.category.name.lower() or not any(x in sub.name.lower() for x in ('аккаунт','account')):return
        status=getattr(o.status,'name',str(o.status))
        price=getattr(o,'price',getattr(o,'sum',None))
        description=getattr(o,'description',None) or getattr(o,'short_description','') or ''
        with core.db() as con:
            previous=con.execute('SELECT status FROM orders WHERE id=?',(o.id,)).fetchone()
            fresh=con.execute('INSERT OR IGNORE INTO orders VALUES (?,?,?,?,?,?)',(o.id,core.now(),str(price),getattr(o.currency,'name',str(o.currency)),status,description[:1000])).rowcount
            con.execute('UPDATE orders SET status=? WHERE id=?',(status,o.id))
        operations.capture_order(o)
        if previous and previous['status']!=status:
            core.notify('order-status:'+o.id+':'+status,f'📦 Заказ #{o.id}: {status}\nhttps://funpay.com/orders/{o.id}/')
        if fresh:
            core.event(None,'success',f'Новый заказ LoL #{o.id}')
            core.notify('order:'+o.id,f'🛒 Новый заказ аккаунта LoL #{o.id}\n{description[:500]}\nСумма: {price} {getattr(o.currency,"name","")}\nhttps://funpay.com/orders/{o.id}/')
    except Exception:log.exception('Offer Studio order handler failed')

def init(c,*args):
    global _cardinal,_mirror
    _cardinal=c;core.init();operations.init()
    import ai_assistant
    ai_assistant.init(c,FP_LOCK)
    threading.Thread(target=core.serve,args=(verify_lot,disable_offer,create_offer),daemon=True).start()
    if c.telegram:
        c.telegram.bot.register_callback_query_handler(lambda call:assistant_callback(c.telegram.bot,call),func=lambda call:bool(call.data and call.data.startswith('osa:')))
        commands=[('os_help','Offer Studio: помощь',True),('os_status','Мониторинг: состояние',True),('os_lots','Связанные лоты',True),('os_stats','Цены и статистика',True),('os_logs','Журнал мониторинга',True),('os_check','Проверить сейчас',True),('os_pause','Приостановить автодействия',True),('os_resume','Возобновить автодействия',True),('os_off','Снять связанный лот',False),('os_monitor','Проверки одного лота',False),('os_link','Связать LZT и FunPay',False),('os_price','Задать плановые цены',False),('os_remove','Убрать из мониторинга',False),('os_restore','Восстановить из архива',False)]
        commands += [('os_health','Состояние всех служб',True),('os_order','Связать заказ и аккаунт',False),('os_refund','Возврат: ЗАКАЗ СУММА ВОЗВРАТ_ЗАКУПКИ',False)]
        commands += [(x,y,True) for x,y in [('ai_status','Нейропомощник: состояние'),('ai_on','Включить нейропомощника'),('ai_off','Выключить нейропомощника'),('ai_resume','Возобновить диалог'),('ai_pause','Приостановить диалог')]]
        c.add_telegram_commands(UUID,commands)
        c.telegram.msg_handler(lambda m:handle(c.telegram.bot,m),commands=['os']+[x[0] for x in commands])
        tokenfile=core.DATA/'mirror_token'
        if tokenfile.exists():
            import telebot
            _mirror=telebot.TeleBot(tokenfile.read_text().strip(),parse_mode=None)
            _mirror.register_callback_query_handler(lambda call:assistant_callback(_mirror,call),func=lambda call:bool(call.data and call.data.startswith('osa:')))
            _mirror.register_message_handler(lambda m:handle(_mirror,m),commands=['start','os']+[x[0] for x in commands])
            threading.Thread(target=lambda:_mirror.infinity_polling(timeout=20,long_polling_timeout=20,skip_pending=False),daemon=True).start()
        threading.Thread(target=outbox_loop,daemon=True).start()
    threading.Thread(target=sales_sync_loop,daemon=True).start()
    __import__('batch').start(create_offer)
    core.event(None,'info','Offer Studio запущен внутри Cardinal')

BIND_TO_PRE_INIT=[init]
BIND_TO_NEW_ORDER=[on_order]
BIND_TO_ORDER_STATUS_CHANGED=[on_order]

def on_message(c,e,*args):
    import ai_assistant
    ai_assistant.on_message(c,e,*args)

BIND_TO_NEW_MESSAGE=[on_message]
