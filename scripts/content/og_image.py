"""og:image 补抓：用官方 OG 图替换 Google News 跳转页的占位图。"""

try:
    from constants import HEADERS
    from sources.http import aiohttp
except ImportError:
    from scripts.constants import HEADERS
    from scripts.sources.http import aiohttp


def fill_event_images(events):
    """并发获取事件文章的 og:image，只处理没有 image_url 的事件"""
    batch = [e for e in events if not e.get('image_url') and e.get('url') and not e['url'].startswith('https://news.google.com')]
    if not batch:
        return
    print(f"  🖼️  补抓 og:image（{len(batch)} 条无图片）...")
    import asyncio
    async def fetch_one(session, ev):
        try:
            async with session.get(ev['url'], timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status != 200:
                    return
                html = await resp.text()
                for m in ["og:image", "twitter:image"]:
                    for pattern in [f'<meta property="{m}" content="', f'<meta name="{m}" content="']:
                        idx = html.find(pattern)
                        if idx >= 0:
                            start = idx + len(pattern)
                            end = html.find('"', start)
                            if end > start:
                                url = html[start:end]
                                if url.startswith('http'):
                                    ev['image_url'] = url
                                    return
        except Exception:
            pass
    async def run():
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            tasks = [fetch_one(session, ev) for ev in batch]
            await asyncio.gather(*tasks, return_exceptions=True)
    try:
        asyncio.run(run())
    except Exception:
        pass
    filled = sum(1 for e in batch if e.get('image_url'))
    print(f"    → 成功获取 {filled}/{len(batch)} 张")
