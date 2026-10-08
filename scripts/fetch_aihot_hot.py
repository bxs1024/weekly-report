#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取 AIHOT 热点榜 → data/aihot_hot.json

用法:
    python scripts/fetch_aihot_hot.py

输出结构:
    data/aihot_hot.json
    - generated_at / fetched_date / source / items[]
      items 每条含 rank / title / list_title / heat_change / sources
      / summary / original_links[{url,domain}] / story_url

2026-10 源站改版适配说明（第一性原理：抓 HTML 必然随源站改版失效，所以做三件事）
    1. 编码：源站 header 不再声明 charset，requests 默认猜 ISO-8859-1 → 中文乱码。
       页面 <meta charSet="utf-8">，故显式按 utf-8 解码。
    2. 结构：新版前 3 名是 <article class="card">，第 4-10 名是 <ol><li>；
       旧版的 hot-rank-row / hot-rank-link / dup-tooltip-item 与 JSON-LD ItemList 均已移除。
       热度也从「142 热度值」改成「较 6 小时前 ↑46%」的相对涨跌。
    3. 兜底：选择器未命中时，把页面可见文本交给 AI 提取（抗改版）；
       两者都失败则**非零退出且不覆盖旧数据**——让失败可见，见 docs/ARCHITECTURE.md 规则 B。
"""

import json
import os
import re
import sys
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

HOT_URL = "https://aihot.virxact.com/hot"
STORY_URL = "https://aihot.virxact.com/story/{}"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
}

MAX_ITEMS = 10           # 最多抓取的热点条数
STORY_FETCH_INTERVAL = 0.4   # 进详情页的间隔秒数（防反爬）
MIN_EXPECTED_ITEMS = 5   # 低于此条数视为异常，打印警告（但不失败）

_STORY_HREF = re.compile(r'^/story/')


def _session():
    s = requests.Session()
    s.trust_env = False
    return s


def _decode_response(resp):
    """按页面真实编码解码。

    源站 2026-10 起 content-type 只写 text/html、不再带 charset，
    requests 对无 charset 的 text/* 默认按 ISO-8859-1 解码 → 中文全乱码。
    页面 meta 声明 utf-8，这里显式用 utf-8；有 charset 时仍尊重 header。
    """
    ctype = (resp.headers.get('content-type') or '').lower()
    if 'charset=' in ctype:
        return resp.text
    return resp.content.decode('utf-8', errors='replace')


def _fetch(session, url):
    resp = session.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return _decode_response(resp)


def _story_id(href):
    return href.rstrip('/').split('/')[-1]


def _extract_heat_change(container):
    """新版热度是「较 6 小时前 ↑46%」的相对涨跌（旧版是「142 热度值」绝对值）。"""
    for span in container.find_all('span', attrs={'title': True}):
        title = span.get('title') or ''
        text = span.get_text(' ', strip=True)
        if '较' in title and '%' in text:
            return re.sub(r'\s+', ' ', text).strip()
    return ''


def _extract_summary(container):
    para = container.find('p')
    return para.get_text(' ', strip=True) if para else ''


_RANK_ARIA = re.compile(r'热度排名第\s*(\d+)\s*位')


def _extract_rank(container, fallback):
    """名次：4-10 名用 aria-label="热度排名第 N 位"，前 3 名用 text-rank-N 类。"""
    el = container.find('span', attrs={'aria-label': _RANK_ARIA})
    if el:
        m = _RANK_ARIA.search(el.get('aria-label') or '')
        if m:
            return int(m.group(1))
    el = container.find('span', class_=re.compile(r'text-rank'))
    if el:
        m = re.search(r'(\d+)', el.get_text())
        if m:
            return int(m.group(1))
    return fallback


def _row_from_container(container, fallback_rank):
    """从单个 article/li 容器提取一条热点；不含 /story/ 链接的容器返回 None。"""
    a = container.find('a', href=_STORY_HREF)
    if not a:
        return None
    href = a.get('href', '')
    if not href:
        return None
    # 标题只取链接自身文本：状态标签（"发酵中"/"爆"/"新"）是链接的兄弟节点，
    # 取 h3 全文会把它们粘进标题。
    title = a.get_text(' ', strip=True)
    if not title:
        return None
    return {
        "rank": _extract_rank(container, fallback_rank),
        "title": title,
        "list_title": title,
        "heat": None,                       # 新版不再提供绝对热度值
        "heat_change": _extract_heat_change(container),
        "sources": [],                      # 新版不再展示信源标注
        "summary": _extract_summary(container),
        "original_links": [],
        "story_url": STORY_URL.format(_story_id(href)),
    }


def _parse_items(soup):
    """适配 2026-10 改版。

    新版把榜单拆成两块 DOM：前 3 名是 <article class="card">，第 4-10 名是 <ol><li>。
    两块在文档里的先后顺序与榜单名次**并不一致**（实测第 3 名的 article 排在
    第 4 名的 li 之后），所以必须分别解析、各自取名次，再按 rank 排序——
    不能依赖 find_all 的文档顺序。

    只认「含 /story/ 链接」的容器、不认具体 class 名（新版改用 Tailwind 工具类），
    这样源站再次调整样式类名时仍能命中。
    """
    rows = []
    for container in soup.find_all('article'):
        row = _row_from_container(container, len(rows) + 1)
        if row:
            rows.append(row)
    base = len(rows)
    for i, container in enumerate(soup.find_all('li')):
        row = _row_from_container(container, base + i + 1)
        if row:
            rows.append(row)

    # 同一 story 只留首次出现，再按名次排序并重编号
    seen, unique = set(), []
    for row in rows:
        if row['story_url'] in seen:
            continue
        seen.add(row['story_url'])
        unique.append(row)
    unique.sort(key=lambda r: r['rank'])
    for idx, row in enumerate(unique, 1):
        row['rank'] = idx
    return unique


def _extract_newsarticle(html):
    """从 story 详情页 JSON-LD 提取 NewsArticle：headline/description/isBasedOn。"""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") == "NewsArticle":
            based_on = data.get("isBasedOn") or []
            if isinstance(based_on, str):
                based_on = [based_on]
            return {
                "headline": data.get("headline", ""),
                "description": data.get("description", ""),
                "original_links": based_on,
            }
    return {"headline": "", "description": "", "original_links": []}


def _link_domain(url):
    from urllib.parse import urlparse
    try:
        return urlparse(url).netloc or ""
    except Exception:
        return ""


def _dedupe_links(links):
    """去重原始链接：同域名只保留第一条。"""
    seen_url, seen_domain, out = set(), set(), []
    for u in links:
        if not u or u in seen_url:
            continue
        domain = _link_domain(u)
        if domain in seen_domain:
            continue
        seen_url.add(u)
        seen_domain.add(domain)
        out.append({"url": u, "domain": domain})
    return out


def _visible_text(soup):
    """提取页面可见文本（去 script/style），供 AI 兜底使用。"""
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [ln.strip() for ln in soup.get_text("\n").split("\n")]
    return "\n".join(ln for ln in lines if ln)


_AI_PROMPT = """下面是 AIHOT 网站「热点榜」页面的可见文本。请提取榜单条目，输出 JSON。

要求：
- 只输出 JSON，不要解释、不要 markdown 代码块
- 结构：{{"items": [{{"rank": 1, "title": "...", "summary": "...", "heat_change": "↑46%"}}]}}
- title 是事件标题（不是站点导航/按钮文字）
- summary 是事件摘要，没有就给空字符串
- heat_change 形如 "↑46%" / "↓16%"，没有就给空字符串
- 最多 10 条，按页面顺序

页面文本：
{text}
"""


def _ai_extract(text):
    """选择器失效时的兜底：把页面可见文本交给 AI 提取结构化榜单。

    这是「抗改版」的核心——只要内容还在页面上，样式类名变了也能读出来。
    拿不到 AI 通道（未配 key 或总闸关闭）时返回空列表，由调用方走失败路径。
    """
    if not text:
        return []
    try:
        try:
            from providers import llm as _llm
        except ImportError:
            from scripts.providers import llm as _llm
    except ImportError:
        print("  ⚠️ AI 兜底不可用：无法导入 providers.llm")
        return []
    apis = _llm._chat_api_candidates()
    if not apis:
        print("  ⚠️ AI 兜底不可用：无可用通道（未配 key 或 AI_CALLS_ENABLED 关闭）")
        return []
    prompt = _AI_PROMPT.format(text=text[:12000])
    for api in apis:
        try:
            resp = _llm._post_chat(api, prompt, max_tokens=2000, temperature=0, timeout=(10, 60))
            if resp.status_code != 200:
                print(f"  ⚠️ AI 兜底 {api['name']} 返回 {resp.status_code}，尝试下一个")
                continue
            raw = resp.json()['choices'][0]['message']['content'].strip()
            raw = re.sub(r'^```(?:json)?\s*', '', raw).strip().rstrip('`').strip()
            data = json.loads(raw)
            out = []
            for it in (data.get('items') or [])[:MAX_ITEMS]:
                title = (it.get('title') or '').strip()
                if not title:
                    continue
                out.append({
                    "rank": len(out) + 1,
                    "title": title,
                    "list_title": title,
                    "heat": None,
                    "heat_change": (it.get('heat_change') or '').strip(),
                    "sources": [],
                    "summary": (it.get('summary') or '').strip(),
                    "original_links": [],
                    "story_url": "",
                })
            if out:
                print(f"  🤖 AI 兜底提取 {len(out)} 条（{api['name']}）")
                return out
        except Exception as exc:
            print(f"  ⚠️ AI 兜底 {api.get('name')} 失败: {exc}")
            continue
    return []


def main():
    s = _session()
    hot_html = _fetch(s, HOT_URL)
    soup = BeautifulSoup(hot_html, "html.parser")

    items = _parse_items(soup)
    if not items:
        print("⚠️ 主选择器未命中（源站可能再次改版），尝试 AI 兜底")
        items = _ai_extract(_visible_text(soup))

    if not items:
        print("ERROR | 未提取到任何热点（选择器与 AI 兜底均失败）。"
              "保留旧数据不覆盖，退出码 2。", file=sys.stderr)
        sys.exit(2)

    if len(items) < MIN_EXPECTED_ITEMS:
        print(f"⚠️ 仅提取到 {len(items)} 条（预期约 {MAX_ITEMS} 条），源站结构可能部分变化")

    # 详情页不再抓取：源站改版后 story 详情页的 JSON-LD 只剩 BreadcrumbList，
    # NewsArticle / isBasedOn 均已移除（原始来源链接不再对外提供）。
    # original_links 保持为空，展示层会自动回退到 story_url（跳 AIHOT 详情页）。

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    payload = {
        "generated_at": now,
        "fetched_date": now[:10],
        "source": {
            "name": "AIHOT 热点榜",
            "url": HOT_URL,
            "note": "过去 48 小时最热的 AI 事件，按精选报道与讨论热度实时排序。本页为全球 AI 视野补充，点击跳转原始来源。",
        },
        "items": items,
    }

    base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

    out_path = os.path.join(base_dir, "aihot_hot.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    archive_dir = os.path.join(base_dir, "aihot_hot")
    os.makedirs(archive_dir, exist_ok=True)
    archive_path = os.path.join(archive_dir, f"{payload['fetched_date']}.json")
    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"OK | AIHOT 热点 {len(items)} 条 | 有原始链接 {sum(1 for i in items if i['original_links'])} 条 | {out_path}")
    print(f"OK | 已归档 {archive_path}")


if __name__ == "__main__":
    main()
