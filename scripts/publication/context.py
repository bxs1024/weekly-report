"""展示上下文：整站渲染所需的全部视图数据装配。"""

try:
    from view_selectors import select_company_quality_events, select_homepage_events, select_mature_main_date, signal_sort_key
    from content.util import _cn_today
    from publication.display import enrich_frontend_fields
    from publication.display_dedupe import dedupe_display_events
    from publication.entity import _portfolio_by_entity, build_entity_event_timelines, load_entity_pool
    from publication.loaders import load_entity_observation_ledger
    from publication.review import _quality_main_events
    from publication.summary import load_events, split_company_events
except ImportError:
    from scripts.view_selectors import select_company_quality_events, select_homepage_events, select_mature_main_date, signal_sort_key
    from scripts.content.util import _cn_today
    from scripts.publication.display import enrich_frontend_fields
    from scripts.publication.display_dedupe import dedupe_display_events
    from scripts.publication.entity import _portfolio_by_entity, build_entity_event_timelines, load_entity_pool
    from scripts.publication.loaders import load_entity_observation_ledger
    from scripts.publication.review import _quality_main_events
    from scripts.publication.summary import load_events, split_company_events

def build_display_context():
    """Return the same final event model used by the HTML dashboard and RSS feed."""
    events = load_events()
    sorted_dates = sorted(events.keys(), reverse=True)

    # 主tab：最近一次有内容的采集批次（回退到昨天兜底）
    # 历史tab：除主tab批次之外的所有日期
    today_str = _cn_today()
    main_date = None
    main_events = []

    # 找最近一个有内容的批次
    for d in sorted_dates:
        evs = events.get(d, [])
        if evs:
            main_date = d
            main_events = evs
            break

    # 今天批次为空 → 回退到昨天
    if main_date == today_str and not main_events:
        for d in sorted_dates:
            if d != today_str:
                evs = events.get(d, [])
                if evs:
                    main_date = d
                    main_events = evs
                    break

    all_feed = _quality_main_events(main_events)

    # 公司动态单独处理
    company_events, generic_events = split_company_events(events)

    # 收集每家公司所有事件（时间窗口内，不过滤数量上限）
    company_by_company = {}
    qualified_company_events = select_company_quality_events(company_events)
    for e in qualified_company_events:
        name = e.get('company_name', '其他')
        company_by_company.setdefault(name, []).append(e)

    # 按事件数量排序，有事件的排前面
    preset_company_list = []
    entity_pool = load_entity_pool()
    portfolio = _portfolio_by_entity(entity_pool)
    entity_timelines = build_entity_event_timelines(events, entity_pool.get('entities') or [])
    for entity in entity_pool.get('entities') or []:
        company_name = entity.get('name') or ''
        evs = entity_timelines.get(entity.get('id') or company_name, [])
        preset_company_list.append({
            'entity_id': entity.get('id') or '',
            'name': company_name,
            'region': entity.get('region') or '全球',
            'sector': entity.get('sector') or '',
            'priority': entity.get('priority') or 'watch',
            **portfolio.get(entity.get('id'), {'portfolio_tier': 'experiment', 'decision_use': ''}),
            'count': len(evs),
            'events': evs,
        })

    # 按事件数量排序，有事件的排前面
    preset_company_list.sort(key=lambda x: x['count'], reverse=True)

    # 阶段2：实体池拆分 Watchlist / Mention。
    # Watchlist = entity_pool（人工关注对象）；Mention = 监控雷达（COMPANY_SOURCES）
    # 中未纳入 Watchlist 的公司 + 事件中自动发现的公司。07-31 实体池重构把 14 家
    # 被监控公司（Zalando/Allegro/Trendyol/Kaspi.kz/中资7家等）从索引里丢掉，
    # 此处让"在监控"的公司持久出现：有近 7 天合格事件就带事件，没有就显示
    # "监控中"状态而非直接消失。
    try:
        from fetch_news import COMPANY_ALIASES as _RADAR_ALIASES
        from fetch_news import COMPANY_SOURCES as _RADAR_SOURCES
    except Exception:
        _RADAR_ALIASES = {}
        _RADAR_SOURCES = []
    watchlist_name_lower = {
        (entity.get('name') or '').lower()
        for entity in (entity_pool.get('entities') or [])
        if entity.get('name')
    }
    watchlist_alias_lower = {
        alias.lower()
        for entity in (entity_pool.get('entities') or [])
        for alias in (entity.get('aliases') or [])
        if alias
    }
    watch_all = watchlist_name_lower | watchlist_alias_lower

    def _mention_events_for(radar_name):
        """收集公司近 7 天合格事件：公司名 + 别名命中 company_by_company。"""
        names = [radar_name] + list(_RADAR_ALIASES.get(radar_name, []))
        found = []
        for n in names:
            evs = company_by_company.get(n)
            if evs:
                found.extend(evs)
        seen = set()
        dedup = []
        for e in found:
            key = e.get('url') or f"{e.get('date')}|{e.get('title')}"
            if key in seen:
                continue
            seen.add(key)
            dedup.append(e)
        return dedup

    mention_names = set()
    # 1) 监控雷达公司：持久卡片
    for _cfg in _RADAR_SOURCES:
        radar_name = _cfg.get('name') or ''
        if not radar_name or radar_name.lower() in watch_all:
            continue
        evs = _mention_events_for(radar_name)
        mention_names.add(radar_name.lower())
        preset_company_list.append({
            'entity_id': '',
            'name': radar_name,
            'region': _cfg.get('region') or '全球',
            'sector': '',
            'priority': 'mention',
            'portfolio_tier': 'mention',
            'decision_use': '监控中：公司雷达覆盖，未纳入人工观察清单',
            'count': len(evs),
            'events': evs,
        })
    # 2) 自动发现：事件中出现、既不在 Watchlist 也不在雷达配置的公司
    for name, evs in company_by_company.items():
        if not name or name == '其他':
            continue
        key = name.lower()
        if key in watch_all or key in mention_names:
            continue
        region_counts = {}
        for e in evs:
            r = e.get('region') or ''
            if r:
                region_counts[r] = region_counts.get(r, 0) + 1
        region = max(region_counts, key=region_counts.get) if region_counts else '全球'
        preset_company_list.append({
            'entity_id': '',
            'name': name,
            'region': region,
            'sector': '',
            'priority': 'mention',
            'portfolio_tier': 'mention',
            'decision_use': '自动发现：出现在公司源事件中，未纳入人工观察清单',
            'count': len(evs),
            'events': evs,
        })

    # 全部事件 = 通用热点 + 公司动态（筛选后），统一按时间排序
    company_events_filtered = [e for evs in company_by_company.values() for e in evs]
    all_events_for_list = list(generic_events) + company_events_filtered
    all_events_for_list.sort(key=signal_sort_key, reverse=True)
    enrich_frontend_fields(all_events_for_list)
    all_events_for_list = dedupe_display_events(all_events_for_list)
    mature_main_date, latest_data_date, latest_visible_count, batch_notice = select_mature_main_date(sorted_dates, all_events_for_list, events)
    period_reference_date = latest_data_date or main_date or today_str
    if mature_main_date:
        main_date = mature_main_date
        main_events = events.get(main_date, [])
        all_feed = _quality_main_events(main_events)

    # 今日要点 = what'll be displayed — 从 all_events_for_list 中取今天的可展示事件
    raw_today_events = [
        e for e in all_events_for_list
        if (e.get('date') or '')[:10] == main_date
    ]
    today_events = select_homepage_events(all_events_for_list, main_date, all_feed)

    return {
        'events': events,
        'sorted_dates': sorted_dates,
        'today_str': today_str,
        'main_date': main_date,
        'main_events': main_events,
        'all_feed': all_feed,
        'company_events': company_events,
        'generic_events': generic_events,
        'company_by_company': company_by_company,
        'company_events_filtered': company_events_filtered,
        'preset_company_list': preset_company_list,
        'all_events_for_list': all_events_for_list,
        'today_events': today_events,
        'raw_today_events': raw_today_events,
        'latest_data_date': latest_data_date,
        'latest_visible_count': latest_visible_count,
        'batch_notice': batch_notice,
        'period_reference_date': period_reference_date,
        'entity_observation_ledger': load_entity_observation_ledger(),
    }
