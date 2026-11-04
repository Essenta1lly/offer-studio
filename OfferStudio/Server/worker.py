"""Five-minute browser checks. No CAPTCHA solving or evasion; asks owner to intervene."""
import asyncio, json, os, signal, socket, sys, time, urllib.request
from pathlib import Path
import core
from playwright.async_api import async_playwright

SNAPSHOT = r'''() => {
 const h=document.querySelector('h1');
 let card=''; let buy=false;
 if (h) {
   const range=document.createRange(); range.setStartBefore(h);
   const end=Array.from(document.querySelectorAll('h2,h3,h4')).find(x=>Boolean(h.compareDocumentPosition(x)&Node.DOCUMENT_POSITION_FOLLOWING));
   if(end) range.setEndBefore(end); else range.setEndAfter(h.parentElement);
   const box=document.createElement('div');box.appendChild(range.cloneContents());
   const walker=document.createTreeWalker(box,NodeFilter.SHOW_TEXT);const pieces=[];
   while(walker.nextNode()) pieces.push(walker.currentNode.textContent);
   card=pieces.join(' ');
   buy=Array.from(box.querySelectorAll('a,button,input[type=submit]')).some(x=>/^(купить|buy)$/i.test((x.textContent||x.value||'').trim()) && !x.disabled);
 }
 const body=(document.body.innerText||'').slice(0,5000);
 const challenge=!h && (/captcha|капча|checking your browser|проверка браузера|verify you are human|доступ ограничен/i.test(body) || !!document.querySelector('iframe[src*="captcha"],.g-recaptcha,.h-captcha'));
 return {url:location.href,heading:h?.innerText||'',card:card.slice(0,8000),buy,challenge};
}'''

def disable(id):
    token=(core.DATA/'api_token').read_text().strip()
    request=urllib.request.Request('http://127.0.0.1:8765/internal/disable',data=json.dumps({'id':id}).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=90) as response:return json.load(response)

async def inspect(page,row):
    status={'code':200}
    def document_response(response):
        try:
            if response.request.is_navigation_request() and response.frame==page.main_frame:status['code']=response.status
        except Exception:pass
    page.on('response',document_response)
    try:
        response=await page.goto(row['url'],wait_until='domcontentloaded',timeout=45000)
        if response:status['code']=response.status
        for _ in range(8):
            snapshot=await page.evaluate(SNAPSHOT)
            snapshot['http_status']=status['code']
            state,reason=core.classify(snapshot,row['id'])
            if status['code']>=500 or status['code']==404:return 'unknown',f'Ошибка HTTP {status["code"]}'
            if state in ('available','sold','challenge'):return state,reason
            # Let the site's ordinary JavaScript redirects complete; track the new document status.
            await asyncio.sleep(2)
        if status['code']>=400:return 'unknown',f'Ошибка HTTP {status["code"]}'
        return state,reason
    finally:
        page.remove_listener('response',document_response)

async def check(page,row):
    if row['status']=='removed':return 'ok'
    core.observe(row['id'],'checking','Начата проверка текущего оффера')
    try:
        state,reason=await asyncio.wait_for(inspect(page,row),timeout=65)
        if state=='sold':
            await asyncio.sleep(10)
            second,_=await asyncio.wait_for(inspect(page,row),timeout=65)
            if second!='sold':state,reason='unknown','Продажа не подтвердилась при повторной загрузке'
            else:reason+='; подтверждено второй загрузкой'
    except Exception as error:
        state,reason='unknown','Страница LZT не проверена: '+type(error).__name__
        try:await asyncio.wait_for(page.evaluate('window.stop()'),timeout=5)
        except Exception:pass
    current=core.get_offer(row['id'])
    if not current or current['archived_at'] or not current['monitoring']:return 'ok'
    core.observe(row['id'],state,reason,alert=state=='challenge')
    if state=='challenge':
        core.setting('browser_status','Нужна ручная проверка LZT; откройте авторизацию в приложении')
        return 'challenge'
    if state=='unknown':
        core.site_problem(reason)
        return 'error'
    __import__('operations').health('lzt',True,'Проверена карточка LZT #'+row['id'])
    if state=='sold':
        if current['auto_disable'] and current['verified'] and core.setting('paused')!='1':
            try:
                result=await asyncio.to_thread(disable,row['id'])
                if not result.get('ok'):raise RuntimeError('Не подтверждено снятие')
            except Exception as error:
                core.observe(row['id'],'sold','LZT: продажа подтверждена; снятие FunPay не подтверждено: '+type(error).__name__,alert=False)
                core.notify('action-error:'+row['id'],f'❗️ LZT #{row["id"]} продан, но снятие FunPay не подтверждено. Проверьте Cardinal и связанный лот. Повторные попытки продолжатся автоматически.')
        elif core.setting('paused')!='1':
            core.notify('sold-unlinked:'+row['id'],f'⚠️ LZT #{row["id"]} продан. Автоснятие выключено или нет проверенной связи с FunPay.')
    return 'ok'

def state_if_sold(id):
    row=core.get_offer(id)
    return row and row['source_state']=='sold'

def service_notify(message):
    address=os.environ.get('NOTIFY_SOCKET')
    if not address:return
    if address.startswith('@'):address='\0'+address[1:]
    try:
        with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as channel:
            channel.connect(address);channel.sendall(message.encode())
    except OSError:pass

def ordered_checks(rows):
    # Oldest first: a failure at the beginning of a cycle must not starve other lots.
    return sorted((r for r in rows if r['monitoring'] and not r['archived_at'] and r['status']!='removed'),key=lambda r:(r['checked_at'] or 0,r['id']))

async def heartbeat():
    while True:
        core.setting('worker_heartbeat',core.now())
        service_notify('WATCHDOG=1')
        await asyncio.sleep(15)

async def run():
    core.init()
    stopping=asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,stopping.set)
    asyncio.get_running_loop().add_signal_handler(signal.SIGINT,stopping.set)
    service_notify('READY=1')
    asyncio.create_task(heartbeat())
    async with async_playwright() as p:
        context=await p.chromium.launch_persistent_context(str(core.DATA/'browser_profile'),headless=False,
            viewport={'width':1280,'height':900},locale='ru-RU',timezone_id='Europe/Moscow',
            args=['--disable-dev-shm-usage','--renderer-process-limit=2'],accept_downloads=False)
        page=context.pages[0] if context.pages else await asyncio.wait_for(context.new_page(),timeout=20)
        for extra in context.pages[1:]:await extra.close()
        core.setting('browser_status','Браузер запущен; войдите в LZT через удалённое окно')
        try:await page.goto('https://lzt.market/',wait_until='domcontentloaded',timeout=30000)
        except Exception:pass
        try:
            last=0;blocked_until=0;failure_streak=0
            while not stopping.is_set():
                if core.setting('auth_requested')=='1':
                    core.setting('auth_requested','0')
                    if page.is_closed():page=await asyncio.wait_for(context.new_page(),timeout=20)
                    try:await page.goto('https://lzt.market/',wait_until='domcontentloaded',timeout=30000)
                    except Exception:pass
                    core.setting('browser_status','Ручная авторизация: проверки на паузе. После входа нажмите «Запустить мониторинг».')
                explicit_check=core.setting('check_now')=='1'
                if core.setting('paused')=='1' and not explicit_check:
                    await asyncio.sleep(2);continue
                due=time.monotonic()-last>=300 or core.setting('check_now')=='1'
                if due and time.monotonic()>=blocked_until:
                    core.setting('check_now','0');last=time.monotonic()
                    rows=ordered_checks(core.offers())
                    core.setting('browser_status','Идёт проверка LZT')
                    failures=0;consecutive=0;completed=0
                    for row in rows:
                        if stopping.is_set() or (core.setting('paused')=='1' and not explicit_check):break
                        latest=core.get_offer(row['id'])
                        if not latest or not latest['monitoring'] or latest['archived_at']:continue
                        if page.is_closed():page=await asyncio.wait_for(context.new_page(),timeout=20)
                        try:
                            result=await check(page,latest)
                        finally:
                            # Replace the page to dispose Chromium documents AND Playwright network handles.
                            replacement=await asyncio.wait_for(context.new_page(),timeout=20)
                            try:await asyncio.wait_for(page.close(),timeout=10)
                            except Exception:pass
                            page=replacement
                        completed+=1
                        if result=='challenge':
                            failures+=1;blocked_until=time.monotonic()+600;break
                        if result=='error':failures+=1;consecutive+=1
                        else:consecutive=0
                        if consecutive>=3:
                            failure_streak+=1
                            delay=min(1800,300*(2**min(failure_streak,3)))
                            blocked_until=time.monotonic()+delay
                            core.setting('browser_status',f'LZT недоступен: повтор через {delay//60} мин. Подробности в журнале.')
                            break
                        await asyncio.sleep(2)
                    if rows and completed==len(rows) and failures==0:
                        failure_streak=0;core.site_recovered()
                        core.setting('browser_status','Цикл завершён; следующая проверка через 5 минут')
                    elif failures and time.monotonic()>=blocked_until:
                        core.setting('browser_status',f'Цикл завершён с ошибками: {failures}. Повтор через 5 минут.')
                    core.setting('last_cycle',core.now())
                    __import__('operations').health('lzt',failures==0 and completed==len(rows),'Цикл проверки завершён')
                    core.setting('cycle_completed',completed)
                    core.setting('cycle_total',len(rows))
                    core.setting('cycle_failures',failures)
                await asyncio.sleep(2)
        finally:
            await asyncio.wait_for(context.close(),timeout=20)

if __name__=='__main__':asyncio.run(run())
