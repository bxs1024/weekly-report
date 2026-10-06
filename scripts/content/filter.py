"""智能过滤与存储策略：控制每天总条数、按日去重、决定事件留存/归档。

评分只排序不过滤（架构契约），这一层负责的是「留多少、留哪些」。"""

try:
    from content.classify import (
        _get_company_aliases, _is_low_signal_company_title, _is_official_company_source,
        _title_mentions_aliases,
    )
    from content.dedupe import _is_same_event
except ImportError:
    from scripts.content.classify import (
        _get_company_aliases, _is_low_signal_company_title, _is_official_company_source,
        _title_mentions_aliases,
    )
    from scripts.content.dedupe import _is_same_event


MAX_DAILY = 40      # 每天最多保留 40 条

MAX_PER_REGION = 12  # 每个区域最多保留多少条

def smart_filter(items):
    """
    策略：
    1. 所有融资/并购/财报事件全部保留
    2. 官方/IR 公司事件全部保留，Google News 公司 other 只有限补漏
    3. 其他事件按 priority 排序，每天最多 40 条（通用部分）
    """
    # 信号事件（全部保留）
    signal = [it for it in items if it['event_types'][0] != 'other']
    company = [
        it for it in items
        if it.get('is_company') and it['event_types'][0] == 'other' and _is_official_company_source(it)
    ]
    # 非信号、非公司事件（按 priority 排序，取剩余名额）
    others = [it for it in items if it['event_types'][0] == 'other' and not it.get('is_company')]
    others.sort(key=lambda x: x.get('priority', 1), reverse=True)

    result = []
    used_urls = set()
    seen_items = []

    def _add_unique(it):
        if it['url'] in used_urls:
            return False
        if any(_is_same_event(it, existing) for existing in seen_items):
            return False
        result.append(it)
        seen_items.append(it)
        if it['url']:
            used_urls.add(it['url'])
        return True

    # 1. 官方/IR 公司事件（高可信，低频保留）
    company_sorted = sorted(
        company,
        key=lambda x: (
            0 if x['event_types'][0] != 'other' else 1,
            -x.get('priority', 1),
            x.get('company_name', ''),
        )
    )
    company_counts = {}
    company_other_counts = {}
    for it in company_sorted:
        cname = it.get('company_name', '')
        if cname:
            if company_counts.get(cname, 0) >= 3:
                continue
            if it['event_types'][0] == 'other' and company_other_counts.get(cname, 0) >= 1:
                continue
        _add_unique(it)
        if cname:
            company_counts[cname] = company_counts.get(cname, 0) + 1
            if it['event_types'][0] == 'other':
                company_other_counts[cname] = company_other_counts.get(cname, 0) + 1

    # 2. 全部信号事件
    for it in signal:
        _add_unique(it)

    # 3. 非信号事件补足到 MAX_DAILY，每个区域最多 MAX_PER_REGION 条
    regions = list(dict.fromkeys(it['region'] for it in items))  # 保持原始顺序
    for region in regions:
        remaining = MAX_DAILY - len(result)
        if remaining <= 0: break
        region_others = [it for it in others if it['region'] == region and it['url'] not in used_urls]
        signal_in_region = sum(1 for it in result if it['region'] == region)
        max_other_for_region = max(0, MAX_PER_REGION - signal_in_region)
        for it in region_others[:max_other_for_region]:
            _add_unique(it)
            if len(result) >= MAX_DAILY: break

    return result

def dedupe_events_by_day(all_events):
    """清理历史 events.json 中同一天的重复/低信号事件，保持原始顺序。"""
    cleaned = {}
    removed = 0
    reasons = {
        'missing_company_alias': 0,
        'low_signal_company_title': 0,
        'same_day_duplicate': 0,
        'company_daily_cap': 0,
    }
    for date_key, events in all_events.items():
        kept = []
        company_counts = {}
        for event in events:
            event.setdefault('date', date_key)
            if event.get('is_company') and not _title_mentions_aliases(event.get('title', ''), _get_company_aliases(event.get('company_name', ''))):
                removed += 1
                reasons['missing_company_alias'] += 1
                continue
            if event.get('is_company') and _is_low_signal_company_title(event.get('title', '')):
                removed += 1
                reasons['low_signal_company_title'] += 1
                continue
            if any(_is_same_event(event, existing) for existing in kept):
                # 记录被合并来源，保留可追溯性（原 URL 不丢失）
                match = next(existing for existing in kept if _is_same_event(event, existing))
                match.setdefault('merged_from', [])
                if event.get('url') and event['url'] not in match['merged_from']:
                    match['merged_from'].append(event['url'])
                removed += 1
                reasons['same_day_duplicate'] += 1
                continue
            company_name = event.get('company_name', '')
            if event.get('is_company') and company_name:
                if company_counts.get(company_name, 0) >= 3:
                    removed += 1
                    reasons['company_daily_cap'] += 1
                    continue
                company_counts[company_name] = company_counts.get(company_name, 0) + 1
            kept.append(event)
        cleaned[date_key] = kept
    return cleaned, removed, reasons

def apply_event_storage_policy(all_events):
    """Keep the complete event archive; presentation applies its own windows."""
    return all_events
