import datetime, html, hashlib, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = ROOT / 'scripts'
os.chdir(ROOT)
sys.path.insert(0, str(SCRIPTS_DIR))
from generate_html import build_display_context
from view_selectors import select_feed_events


def _load_cached_display_context():
    """复用 generate_html 刚落下的展示上下文（同一次运行里紧接着执行）。

    整站装配是渲染里最大的一块固定成本（本地单次约 25 秒，其中展示层判重是
    O(n²) 的主要来源），而 RSS 只需要其中三个字段。原先 RSS 自己再跑一遍
    build_display_context()，等于每次渲染付两遍。

    指纹 = data/events.json 的 size+mtime；不符就返回 None 由调用方重算，
    所以不存在「用了过期上下文」的窗口。缓存缺失时行为与改造前完全一致。
    """
    path = ROOT / 'data' / '.cache' / 'rss_context.json'
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            payload = json.load(handle)
        stat = os.stat(ROOT / 'data' / 'events.json')
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if payload.get('fingerprint') != f'{stat.st_size}:{int(stat.st_mtime)}':
        return None
    return payload


cached_context = _load_cached_display_context()
if cached_context is not None:
    print('  ♻️  RSS 复用 HTML 落下的展示上下文（跳过重复装配）')
    feed_date = cached_context.get('main_date')
    today_events = cached_context.get('today_events') or []
    all_events_for_list = cached_context.get('all_events_for_list') or []
else:
    context = build_display_context()
    feed_date = context['main_date']
    today_events = context['today_events']
    all_events_for_list = context.get('all_events_for_list', [])

SITE_URL = 'https://bxs1024.github.io/weekly-report/'


def text_value(value):
    return str(value or '').strip()


def first_text(*values):
    for value in values:
        text = text_value(value)
        if text:
            return text
    return ''


def display_source(ev):
    source = first_text(ev.get('display_source'), ev.get('source_detail'), ev.get('publisher'), ev.get('source'), '未知来源')
    raw_source = text_value(ev.get('source'))
    if raw_source and raw_source != source:
        return f'{source}（{raw_source}）'
    return source


def entry_title(ev):
    return first_text(ev.get('display_title'), ev.get('summary_short'), ev.get('reason'), ev.get('title'), '无标题')


def xml_attr(value):
    return html.escape(text_value(value), quote=True)


def xml_text(value):
    return html.escape(text_value(value), quote=False)


feed_events, fallback_feed_date = select_feed_events(
    today_events,
    all_events_for_list,
    limit=None,
)
if fallback_feed_date:
    feed_date = fallback_feed_date


def entry_summary(ev):
    parts = []
    reason = text_value(ev.get('reason'))
    overview = text_value(ev.get('front_overview') or ev.get('content_overview') or ev.get('summary_short'))
    original_title = text_value(ev.get('original_title') or ev.get('title'))
    title = entry_title(ev)

    if original_title and original_title != title:
        parts.append(f'<p><strong>原题：</strong>{html.escape(original_title)}</p>')
    if overview and overview != title:
        parts.append(f'<p><strong>内容概要：</strong>{html.escape(overview)}</p>')
    if reason:
        parts.append(f'<p><strong>点评：</strong>{html.escape(reason)}</p>')

    meta = []
    if ev.get('insight_label'):
        meta.append(f'标签：{html.escape(text_value(ev.get("insight_label")))}')
    if ev.get('event_types'):
        meta.append(f'类型：{", ".join(html.escape(str(t)) for t in ev["event_types"])}')
    if ev.get('region'):
        meta.append(f'地区：{html.escape(text_value(ev.get("region")))}')
    if ev.get('company_name'):
        meta.append(f'公司：{html.escape(text_value(ev.get("company_name")))}')
    meta.append(f'来源：{html.escape(display_source(ev))}')
    parts.append(f'<p>{" | ".join(meta)}</p>')
    return ''.join(parts)

now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
feed_id = 'tag:weekly-report,2026:main'

feed = f'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>全球互联网百晓生 · 事件流</title>
  <subtitle>高价值事件推送，日报日期：{feed_date or '暂无'}</subtitle>
  <id>{feed_id}</id>
  <updated>{now}</updated>
  <link href="{SITE_URL}" rel="alternate"/>
  <link href="feed.xml" rel="self" type="application/atom+xml"/>
  <author><name>Global Internet Intelligence Station</name></author>
  <rights>CC BY-NC 4.0</rights>
'''

for ev in feed_events:
    entry_date = (ev.get('date') or feed_date or '')[:10]
    uid_base = ev.get('url', ev.get('title', '')) + entry_date
    uid_hash = hashlib.sha1(uid_base.encode('utf-8')).hexdigest()[:8]
    entry_id = f'tag:weekly-report,2026:event-{uid_hash}'
    updated = entry_date + 'T00:00:00Z'
    title = xml_text(entry_title(ev))
    url = xml_attr(ev.get('url', ''))
    summary = html.escape(entry_summary(ev), quote=False)

    entry = f'''
  <entry>
    <title>{title}</title>
    <link href="{url}"/>
    <id>{xml_text(entry_id)}</id>
    <published>{updated}</published>
    <updated>{updated}</updated>
    <summary type="html">{summary}</summary>
  </entry>'''
    feed += entry

# AIHOT 全球AI视野：当前快照 Top5 汇总为一条独立 entry（与站内事件流分开）
try:
    with open(ROOT / 'data' / 'aihot_hot.json', 'r', encoding='utf-8') as f:
        aihot_data = json.load(f)
except (OSError, json.JSONDecodeError):
    aihot_data = None

if aihot_data and aihot_data.get('items'):
    top_items = aihot_data['items'][:5]
    lines = []
    for i, item in enumerate(top_items, 1):
        title = text_value(item.get('title') or item.get('list_title') or '')
        heat = item.get('heat')
        url = ''
        if item.get('original_links'):
            url = item['original_links'][0].get('url') or ''
        if not url:
            url = item.get('story_url') or ''
        heat_part = f"（热度 {heat}）" if heat else ''
        if url:
            lines.append(f'<li><a href="{xml_attr(url)}">{html.escape(title)}{html.escape(heat_part)}</a></li>')
        else:
            lines.append(f'<li>{html.escape(title)}{html.escape(heat_part)}</li>')
    aihot_updated = text_value(aihot_data.get('generated_at'))[:16]
    aihot_summary = f'<p>全球 AI 视野 · 数据截至 {html.escape(aihot_updated)}</p><ol>' + ''.join(lines) + '</ol>'
    aihot_date = text_value(aihot_data.get('fetched_date'))[:10] or feed_date or ''
    aihot_uid = hashlib.sha1(('aihot-top5-' + aihot_date).encode('utf-8')).hexdigest()[:8]
    aihot_entry = f'''
  <entry>
    <title>全球AI视野 Top5 · {aihot_date}</title>
    <link href="https://aihot.virxact.com/hot"/>
    <id>tag:weekly-report,2026:aihot-{aihot_uid}</id>
    <published>{aihot_date}T00:00:00Z</published>
    <updated>{aihot_date}T00:00:00Z</updated>
    <summary type="html">{html.escape(aihot_summary, quote=False)}</summary>
  </entry>'''
    feed += aihot_entry

feed += '\n</feed>\n'

feed_path = ROOT / 'docs' / 'feed.xml'
os.makedirs(feed_path.parent, exist_ok=True)
with open(feed_path, 'w', encoding='utf-8') as f:
    f.write(feed)
print(f'Feed generated: {feed_path}')
