"""存量事件跨天重复清理（一次性维护脚本，可重复运行直到无新合并）。

背景（2026-08-28 诊断）：AI 指纹去重代码 08-24 写好后 08-27 19:37 才推送上线，
中间所有云端班次跑旧逻辑，积累了三类跨天重复：
  1. 同一篇文章跨天重复入库（URL 相同，Jack Ma 案）；
  2. 同一财报故事隔周多篇报道（旧规则 3 天窗口外，Adyen 案）；
  3. AI 判型漂移导致新旧两条路径都漏并（funding vs strategy，MELI 案）。

合并规则（保守，按优先级；阈值均来自 08-28 对全库 58 个候选集群的实测校准）：
  A  URL 完全相同（不限日期）→ 必然同一事件
  B  规范标题相同（不限日期，排除每周固定栏目——同一栏目每周一期不是重复）
  C  同主类型（funding/ma/earnings）+ 非栏目模板 + 日期差 <= 7 天，且
       - 标题相似度 >= 0.75（隔数天的同故事改写稿），或
       - 日期差 <= 1 天且相似度 >= 0.55 且标题首词一致（同日/次日转载；
         首词一致防"同模板不同公司"误并，如 Delhivery/BlackBuck 财报模板）
  D  主类型不同（AI 判型漂移）+ 非栏目模板 + 相似度 >= 0.7 + 日期差 <= 3 天

保留信息最完整的一条（_event_info_score 高者，平局保留日期靠前者），
被并入事件的 URL 记入保留事件的 merged_from，空缺指纹字段回填。

用法：
  python scripts/cleanup_duplicate_events.py           # dry-run，只打印不写
  python scripts/cleanup_duplicate_events.py --apply   # 应用并覆写 data/events.json
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_news import _event_info_score, _primary_event_type  # noqa: E402
from generate_html import _normalized_title_key, _title_similarity  # noqa: E402

MERGE_WINDOW_SAME_TYPE_DAYS = 7
SAME_TYPE_SIM = 0.75
SAME_DAY_SIM = 0.55
MERGE_WINDOW_TYPE_DRIFT_DAYS = 3
TYPE_DRIFT_SIM = 0.7
_ANCHOR_TYPES = {'funding', 'ma', 'earnings'}
# 每周固定栏目/系列模板（同一栏目每期是不同内容，不是重复）：weekly round-up、
# UK tech funding roundup、DATA VANTAGE、Biggest Funding Rounds in X、Series SEA: 等
_RECURRING_COLUMN = re.compile(
    r"round.?up|biggest funding|data vantage|series [a-z]{2,12}\s*:|tracker", re.I)


def _parse_day(day):
    try:
        return datetime.strptime(day[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


def _precompute(day, event):
    url = event.get('url') or ''
    title = event.get('title') or ''
    return {
        'day': day,
        'date': _parse_day(day),
        'event': event,
        'url': url,
        'title_key': _normalized_title_key(title) if title else '',
        'type': _primary_event_type(event),
        'info': _event_info_score(event),
        'recurring': bool(_RECURRING_COLUMN.search(title)),
    }


def _lead_token(title):
    words = re.findall(r'[a-z]{3,}', (title or '').lower())
    return words[0] if words else ''


def _window_reason(a, b, gap):
    """规则 C/D（带日期窗口）；gap 为 None 或不满足时返回 None。"""
    if gap is None or a['recurring'] or b['recurring']:
        return None
    sim = _title_similarity(a['event'].get('title') or '', b['event'].get('title') or '')
    if a['type'] == b['type'] and a['type'] in _ANCHOR_TYPES:
        if sim >= SAME_TYPE_SIM:
            return f'同类型({a["type"]}) 相似{sim:.2f} 差{gap}天'
        if gap <= 1 and sim >= SAME_DAY_SIM:
            lead_a, lead_b = _lead_token(a['event'].get('title')), _lead_token(b['event'].get('title'))
            if lead_a and lead_a == lead_b:
                return f'同日转载({a["type"]}) 相似{sim:.2f} 首词[{lead_a}]'
    elif a['type'] != b['type'] and sim >= TYPE_DRIFT_SIM:
        return f'判型漂移({a["type"]}/{b["type"]}) 相似{sim:.2f} 差{gap}天'
    return None


def _merge_into(survivor, dup):
    survivor.setdefault('merged_from', [])
    if dup.get('url') and dup['url'] not in survivor['merged_from']:
        survivor['merged_from'].append(dup['url'])
    for field in ('canonical_company', 'canonical_key'):
        if not survivor.get(field) and dup.get(field):
            survivor[field] = dup[field]


def main():
    parser = argparse.ArgumentParser(description='跨天重复事件清理')
    parser.add_argument('--apply', action='store_true', help='应用清理（默认 dry-run）')
    args = parser.parse_args()

    path = Path('data/events.json')
    with open(path, encoding='utf-8') as f:
        all_events = json.load(f)

    items = [_precompute(day, ev) for day in sorted(all_events.keys()) for ev in all_events[day]]
    url_index = {}    # url -> item（规则 A，不限日期）
    tkey_index = {}   # 规范标题 -> item（规则 B，不限日期）
    kept = []          # 已保留事件，日期升序
    removed_events = set()  # 被并入事件的 id(event)，按对象记录
    merge_log = []
    for item in items:
        hit = reason = None
        if item['url'] and item['url'] in url_index:
            hit, reason = url_index[item['url']], 'URL相同'
        elif (item['title_key'] and item['title_key'] in tkey_index
              and not item['recurring'] and not tkey_index[item['title_key']]['recurring']):
            hit, reason = tkey_index[item['title_key']], '标题相同'
        else:
            for candidate in reversed(kept):
                gap = None
                if item['date'] and candidate['date']:
                    gap = abs((item['date'] - candidate['date']).days)
                    if gap > MERGE_WINDOW_SAME_TYPE_DAYS:
                        break  # kept 按日期升序，再往前只会更远
                    if candidate['type'] != item['type'] and gap > MERGE_WINDOW_TYPE_DRIFT_DAYS:
                        continue  # 类型不同且超出判型漂移窗口；A/B 已由索引排除
                reason = _window_reason(item, candidate, gap)
                if reason:
                    hit = candidate
                    break
        if hit is None:
            kept.append(item)
            if item['url']:
                url_index.setdefault(item['url'], item)
            if item['title_key']:
                tkey_index.setdefault(item['title_key'], item)
            continue

        if item['info'] > hit['info']:
            survivor, dup = item, hit
            kept = [k for k in kept if k['event'] is not hit['event']]
            kept.append(item)
        else:
            survivor, dup = hit, item
        _merge_into(survivor['event'], dup['event'])
        # 被并入事件让出索引位，保留方接管（ survivor 换了 url/title 时也一并登记）
        for idx, key in ((url_index, dup['url']), (tkey_index, dup['title_key'])):
            if key and idx.get(key) is dup:
                del idx[key]
        if survivor['url']:
            url_index.setdefault(survivor['url'], survivor)
        if survivor['title_key']:
            tkey_index.setdefault(survivor['title_key'], survivor)
        removed_events.add(id(dup['event']))
        merge_log.append(reason)
        print(f'  [{reason}]')
        print(f'      保留「{(survivor["event"].get("title") or "")[:56]}」 @{survivor["day"]}')
        print(f'      并入「{(dup["event"].get("title") or "")[:56]}」 @{dup["day"]}')

    for day in all_events:
        all_events[day] = [e for e in all_events[day] if id(e) not in removed_events]

    total = sum(len(v) for v in all_events.values())
    print(f'\n共合并 {len(removed_events)} 条重复事件，清理后剩 {total} 条')
    if args.apply:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(all_events, f, ensure_ascii=False, indent=2)
        print(f'已写入 {path}')
    else:
        print('dry-run 未写入，加 --apply 生效')


if __name__ == '__main__':
    main()
