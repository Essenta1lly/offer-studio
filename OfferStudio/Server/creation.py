"""Create exactly one FunPay lot per LZT source, using Cardinal's authenticated session."""
import json,re
from decimal import Decimal,ROUND_HALF_UP
from urllib.parse import urlparse,parse_qs
from bs4 import BeautifulSoup
import core

def fit_title(title,limit=100):
    while len(title)>limit:
        m=re.search(r'【([^】]+)】',title)
        if not m or ',' not in m[1]:break
        shorter=m[1].rsplit(',',1)[0]
        title=title[:m.start(1)]+shorter+title[m.end(1):]
    if len(title)>limit:title=title[:limit-1].rstrip()+'…'
    return title

def english_description(p,description):
    converted=description
    replacements={
      '💪 Аккаунт личный! был прокачан вручную':'💪 Personal account, leveled manually.',
      '⚙️ Аккаунт полностью переходит в ваше владение. вы являетесь ЕДИНСТВЕННЫМ владельцем.':'⚙️ Full account ownership is transferred to you.',
      '🔒 На данном аккаунте не привязана почта, вы можете привязать свою почту и сменить все данные':'🔒 No verified email is linked. You can link your email and change the account details.',
      '🔒Вы получаете данные в формате почта / пароль от аккаунта / пароль от почты, почту можно сменить':'🔒 Delivery includes email / account password / email password. The email can be changed.',
      '✅ Пожизненная гарантия на аккаунт':'✅ Lifetime account warranty.',
      '✅ гарантия на аккаунт 30 дней':'✅ Account warranty: 30 days.',
      '📘 Синяя Эссенция:':'📘 Blue essence:',
      '🎖️ Ранг в этом сезоне:':'🎖️ Current season rank:',
      '🎖️ Ранг в предыдущем сезоне:':'🎖️ Previous season rank:',
      'Чемпионы(':'Champions(', 'Скины(':'Skins(', '✍️ Статистика:':'✍️ Statistics:',
      '📩Если есть какие-то вопросы — спрашивайте 💬':'📩 Feel free to ask any questions.',
      '📩 Обязательно отвечу и помогу 💬':'📩 I will reply and help.',
      '📦 На все аккаунты включена автовыдача. Даже если меня нет на месте, данные от аккаунта придут вам сразу же после оплаты.':'📦 Automatic delivery: account details are sent after payment, even while I am away.',
      '❗️Возврат средств не осуществляется, если вы передумали, купили не тот аккаунт или решили, что он вам не нужен. Покупая аккаунт, вы соглашаетесь с условиями магазина. Внимательно прочитайте всё перед покупкой ❗️':'Please read all details before buying. No refunds for changing your mind or choosing the wrong account. By purchasing you accept the store terms.'}
    for a,b in replacements.items():converted=converted.replace(a,b)
    alphabet='абвгдеёжзийклмнопрстуфхцчшщъыьэюя'
    latin=['a','b','v','g','d','e','yo','zh','z','i','y','k','l','m','n','o','p','r','s','t','u','f','kh','ts','ch','sh','shch','','y','','e','yu','ya']
    table={}
    for letter,replacement in zip(alphabet,latin):table[ord(letter)]=replacement;table[ord(letter.upper())]=replacement.capitalize()
    return converted.translate(table)

def create(account,p):
    id,url=core.canonical_lzt(p.get('url',''))
    old=core.get_offer(id)
    if old and old['funpay_id']:return {'ok':True,'offer':old,'existing':True}
    title=str(p.get('title','')).strip();description=str(p.get('description','')).strip()
    if not title or not description:raise ValueError('Сначала считайте данные и проверьте готовые тексты')
    description=description+'\nКод товара: OS-'+id if 'OS-'+id not in description else description
    price_raw=str(p.get('buy_price','')).replace('\xa0','').replace(' ','').replace(',','.')
    if any(x in price_raw for x in ('$','€','USD','EUR')):raise ValueError('Для расчёта нужна закупочная цена в рублях')
    number=re.fullmatch(r'(\d+(?:\.\d{1,2})?)(?:₽|руб\.?|RUB)?',price_raw,re.I)
    if not number:raise ValueError('Не распознана закупочная цена в рублях')
    price=(Decimal(number[1])*Decimal('2.5')).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
    if price<=0:raise ValueError('Цена должна быть больше нуля')
    response=account.method('get','lots/offerEdit?node=85',{}, {},raise_not_200=True)
    form=BeautifulSoup(response.content,'lxml').select_one('form.form-offer-editor')
    if form is None:raise ValueError('Не открылась форма создания FunPay. Проверьте авторизацию')
    fields={e['name']:e.get('value','') for e in form.select('input[name]') if e.get('type') not in ('checkbox','radio')}
    fields.update({e['name']:e.text for e in form.select('textarea[name]')})
    for select in form.select('select[name]'):
        option=select.select_one('option[selected]') or select.select_one('option')
        fields[select['name']]=option.get('value','') if option else ''
    region=str(p.get('server','')).lower().strip()
    regions={'411':['россия','ru','russia'],'406':['eune','eun1','eu nord','европа (северная и восточная)','eu nordic & east'],'405':['euw','euw1','eu west','европа (западная)'],'413':['jp','jp1','japan','япония'],'404':['na','na1','north america','северная америка'],'410':['tr','tr1','turkey','турция'],'407':['lan','la1','latin america north'],'408':['las','la2','latin america south']}
    server=next((k for k,names in regions.items() if region in names),None)
    if not server:raise ValueError('Не распознан сервер LoL: '+region)
    rank=str(p.get('rank','')).lower()
    ranks={'iron':'Железо','bronze':'Бронза','silver':'Серебро','gold':'Золото','platinum':'Платина','emerald':'Изумруд','diamond':'Алмаз','grandmaster':'Грандмастер','challenger':'%Претендент-Challenger','master':'%Мастер-Master'}
    ranked=next((v for k,v in ranks.items() if k in rank),None)
    if ranked is None:
        ranked=next((v for v in ranks.values() if v.lower() in rank),'Нет ранга' if rank in ('нет','unranked','нет ранга') else None)
    if ranked is None:raise ValueError('Не распознан текущий ранг')
    for key in ('level','championCount','skinCount'):
        if not str(p.get(key,'')).isdigit():raise ValueError('Не заполнено поле '+key)
    limit=int((form.select_one('input[name="fields[summary][ru]"]') or {}).get('maxlength',100) or 100)
    title=fit_title(title,limit)
    english_limit=int((form.select_one('input[name="fields[summary][en]"]') or {}).get('maxlength',100) or 100)
    english_title=fit_title(f'League of Legends account for sale | {p["skinCount"]} skins | {p["championCount"]} champions | Level {p["level"]} | Full access and ownership transfer',english_limit)
    english=english_description(p,description)
    fields.update({'offer_id':'0','node_id':'85','server_id':server,'fields[type]':'Продажа','fields[level]':str(p['level']),'fields[hero]':str(p['championCount']),'fields[skin]':str(p['skinCount']),'fields[rank]':ranked,'fields[summary][ru]':title,'fields[summary][en]':english_title,'fields[desc][ru]':description,'fields[desc][en]':english,'price':str(price),'amount':'1','active':'on','auto_delivery':'','secrets':'','deleted':''})
    with core.db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS creations (source_id TEXT PRIMARY KEY,stage TEXT,funpay_id INTEGER)')
        attempt=c.execute('SELECT * FROM creations WHERE source_id=?',(id,)).fetchone()
        if attempt and attempt['stage']=='created':return {'id':id,'url':url,'funpay_id':attempt['funpay_id'],'price':str(price)}
        if attempt:raise ValueError('Создание уже запускалось. Проверьте лоты FunPay и журнал: повтор заблокирован для защиты от дублей')
        c.execute('INSERT INTO creations VALUES (?,"pending",NULL)',(id,))
    try:before={lot.id for lot in account.get_user(account.id).get_lots()}
    except Exception:before=None
    # On a timeout, retain pending: the site may already have accepted the request.
    result=account.method('post','lots/offerSave',{'accept':'application/json','x-requested-with':'XMLHttpRequest','content-type':'application/x-www-form-urlencoded; charset=UTF-8'},fields,raise_not_200=True).json()
    if result.get('error') or result.get('errors') or result.get('done') is False:
        with core.db() as c:c.execute('DELETE FROM creations WHERE source_id=?',(id,))
        raise ValueError('FunPay отклонил заполнение формы: '+str(result.get('msg') or result.get('errors') or 'проверьте обязательные поля')[:300])
    fid=result.get('offer_id') or result.get('id')
    if not fid:
        dest=str(result.get('url') or result.get('redirect') or result.get('location') or '')
        fid=(parse_qs(urlparse(dest).query).get('id') or parse_qs(urlparse(dest).query).get('offer') or [None])[0]
    if not str(fid).isdigit() and before is not None:
        try:
            candidates=[lot for lot in account.get_user(account.id).get_lots() if lot.id not in before]
            matches=[]
            for lot in candidates[:10]:
                check=account.get_lot_fields(lot.id)
                if check.title_ru==title and check.description_ru==description and Decimal(str(check.price))==price:matches.append(lot.id)
            if len(matches)==1:fid=matches[0]
        except Exception:pass
    if not str(fid).isdigit():
        core.event(id,'error','FunPay ответил на создание без ID. Повтор заблокирован; проверьте список лотов вручную')
        raise ValueError('FunPay не вернул номер лота. Проверьте его список; повторное создание заблокировано')
    fid=int(fid)
    with core.db() as c:c.execute('UPDATE creations SET funpay_id=? WHERE source_id=?',(fid,id))
    check=account.get_lot_fields(fid)
    if check.title_ru!=title or check.description_ru!=description or Decimal(str(check.price))!=price or not check.active or bool(check.fields.get('auto_delivery')):
        raise ValueError('FunPay-лот создан, но поля не подтверждены; проверьте его перед повтором')
    with core.db() as c:c.execute('UPDATE creations SET stage="created",funpay_id=? WHERE source_id=?',(fid,id))
    return {'id':id,'url':url,'funpay_id':fid,'price':str(price)}
