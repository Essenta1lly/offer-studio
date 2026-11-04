"""Deterministic DOM fixtures. Run with the server Playwright venv, no marketplace access."""
import asyncio
from playwright.async_api import async_playwright
import core
from worker import SNAPSHOT

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        page=await browser.new_page()
        cases=[
          ('<h1>Аккаунт LoL</h1><a href="/123/buy">Купить</a><h2>Отзывы</h2><div>Аккаунт продан</div>','available'),
          ('<h1>Аккаунт LoL</h1><div>Аккаунт продан</div><h2>Отзывы</h2>','sold'),
          ('<h1>Аккаунт LoL</h1><div>Сервер недоступен</div><h2>Отзывы</h2><div>Аккаунт продан</div>','unknown'),
          ('<div class="h-captcha">Verify you are human</div>','challenge')]
        for html,expected in cases:
            await page.set_content(html)
            shot=await page.evaluate(SNAPSHOT);shot['url']='https://lzt.market/123/'
            state,reason=core.classify(shot,'123')
            assert state==expected,(state,expected,reason,shot)
        await browser.close()
    print('PASS: browser DOM current-card isolation, sold, available, error, CAPTCHA')

if __name__=='__main__':asyncio.run(main())
