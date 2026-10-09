import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
from PIL import Image

OUT = Path(r'C:\Users\ADMIN\Desktop\GBT小土豆V9\state\preview')

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={'width': 1440, 'height': 1000})
        await pg.goto('http://127.0.0.1:8800/cloud', wait_until='domcontentloaded', timeout=60000)
        await pg.wait_for_timeout(7000)
        a = pg.locator("xpath=//*[@id='ctrow']/ancestor::div[1]")
        c = pg.locator("xpath=//*[@id='cmsum']/ancestor::div[1]")
        await a.scroll_into_view_if_needed()
        await a.screenshot(path=str(OUT / 'cloud_terminal.png'))
        await c.scroll_into_view_if_needed()
        await c.screenshot(path=str(OUT / 'cloud_models.png'))
        txt = await pg.evaluate('''() => { const q=document.getElementById('cmquota'), s=document.getElementById('cmsum');
          return {额度:q?q.innerText:'(无)', 汇总:s?s.innerText:'(无)'}; }''')
        print('页面真实显示 -> 汇总:', txt['汇总'])
        print('页面真实显示 -> 额度:', txt['额度'][:240].replace(chr(10), ' '))
        await b.close()
    ims = [Image.open(OUT / 'cloud_terminal.png'), Image.open(OUT / 'cloud_models.png')]
    w = max(i.width for i in ims); h = sum(i.height for i in ims) + 12
    m = Image.new('RGB', (w, h), (8, 10, 16)); y = 0
    for i in ims:
        m.paste(i, (0, y)); y += i.height + 12
    m.save(OUT / 'cloud_blocks.png')
    print('拼图:', m.size)
    return 0

raise SystemExit(asyncio.run(main()))
