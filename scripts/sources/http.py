"""HTTP 抓取底座：同步重试、并发抓取、响应缓存。

采集层（fetch_rss / fetch_company_news / fetch_html）共用这一层。响应缓存只
服务单次运行内的重复 URL 去重，不跨天保留。"""

import hashlib
import time
from pathlib import Path

import requests

try:
    from constants import HEADERS, REQUEST_DELAY, REQUEST_TIMEOUT
    from repo_paths import data_path
except ImportError:
    from scripts.constants import HEADERS, REQUEST_DELAY, REQUEST_TIMEOUT
    from scripts.repo_paths import data_path

# aiohttp 并行抓取底座：缺依赖时自动安装（原 fetch_news 顶部行为，原样搬来）。
try:
    import aiohttp
    import asyncio
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False
    print("安装 aiohttp（并行采集）...")
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "aiohttp", "-q"])
    import aiohttp
    import asyncio
    HAS_AIOHTTP = True


# --- 缓存（仅用于单次运行内去重，不跨天保留）---
CACHE_DIR = Path(data_path('.cache'))

CACHE_TTL = 60 * 60 * 24  # 24小时

def _cache_key(url):
    return hashlib.md5(url.encode()).hexdigest()

def _cache_get(url):
    """返回 (body, age_seconds)，无缓存或过期返回 (None, None)"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = CACHE_DIR / _cache_key(url)
    if not f.exists(): return None, None
    age = time.time() - f.stat().st_mtime
    if age > CACHE_TTL:
        f.unlink()
        return None, None
    return f.read_text(encoding='utf-8', errors='ignore'), age

def _cache_set(url, body):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = CACHE_DIR / _cache_key(url)
    f.write_text(body, encoding='utf-8')

def _clear_old_cache():
    """每次运行前清理旧缓存，确保抓取最新内容"""
    import shutil
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  🗑  已清理历史缓存（{CACHE_DIR}）")

def fetch_url(url, retries=1):
    """
    快速失败策略：
    - 只重试1次（之前重试3次无意义，失败通常是网络/CF，超时后立即失败更好）
    - 超时8s（之前20s太长，RSS本身5s内必返回）
    - 优先读缓存，缓存命中则跳过网络请求
    """
    # 1. 缓存命中
    body, age = _cache_get(url)
    if body:
        print(f"  [CACHE] {url[:50]}... ({age:.0f}s old)")
        return body  # 返回文本，调用方用同样方式解析

    # 2. 网络请求（最多重试1次）
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if r.status_code in (403, 429):
                if i < retries:
                    time.sleep(2 * (i + 1)); continue
                return None
            r.raise_for_status()
            body = r.text
            _cache_set(url, body)  # 写缓存
            return body
        except Exception:
            if i < retries:
                time.sleep(2 ** i); continue
            return None
    return None

async def fetch_url_async(session, url, semaphore):
    """异步单 URL 抓取（带信号量控制并发）"""
    async with semaphore:
        # 检查缓存
        body, age = _cache_get(url)
        if body:
            return url, body, age, True  # cache_hit

        try:
            async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as r:
                if r.status in (403, 429):
                    return url, None, 0, False
                body = await r.text()
                _cache_set(url, body)
                return url, body, 0, False
        except Exception as e:
            return url, None, 0, False

async def fetch_all_parallel(urls):
    """
    并行抓取所有 URL。
    返回 {url: (body_or_None, from_cache)}
    """
    semaphore = asyncio.Semaphore(8)  # 最多8个并发
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_url_async(session, url, semaphore) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    out = {}
    for item in results:
        if isinstance(item, Exception):
            continue
        url, body, age, cached = item
        out[url] = (body, cached)
    return out
