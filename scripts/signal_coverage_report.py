"""Report country x sector x source signal coverage.

Decision-driven (see 方案-情报站信源体系-执行.md step 3): produce the
numbers that decide which countries/sectors/sources need more supply, instead
of guessing. Read-only; never writes back to events.json.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict

try:
    from event_value import should_show_in_main_list
    from repo_paths import data_path
    from signal_geo import entity_country_map, tag_event_country
except ImportError:
    from scripts.event_value import should_show_in_main_list
    from scripts.repo_paths import data_path
    from scripts.signal_geo import entity_country_map, tag_event_country


def _safe_print(text):
    # 显式 UTF-8，保证重定向到文件时中文可读（不依赖终端编码）
    sys.stdout.buffer.write((text + '\n').encode('utf-8'))


def _load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _flatten_events(data):
    if isinstance(data, list):
        return data
    events = []
    for date_key, items in (data or {}).items():
        for event in items or []:
            if not event.get('date'):
                event = dict(event)
                event['date'] = date_key
            events.append(event)
    return events


def _event_types(event):
    return event.get('event_types') or [event.get('content_type')] if event.get('content_type') else (event.get('event_types') or ['other'])


def _primary_sector(event):
    """Sector for reporting: prefer content_type, else event_types[0]."""
    et = event.get('event_types') or ['other']
    return et[0] if et else 'other'


def build_report(events, entity_map):
    # country -> {stored, homepage, sources:set}
    country_stat = defaultdict(lambda: {'stored': 0, 'homepage': 0, 'sources': set()})
    # sector -> {stored, homepage, countries:set}
    sector_stat = defaultdict(lambda: {'stored': 0, 'homepage': 0, 'countries': set()})
    # source -> {stored, homepage, countries:set, sectors:set}
    source_stat = defaultdict(lambda: {'stored': 0, 'homepage': 0, 'countries': set(), 'sectors': set()})
    # country x sector cross for gap matrix
    cross = defaultdict(lambda: {'stored': 0, 'homepage': 0})

    for ev in events:
        tag = tag_event_country(ev, entity_map=entity_map)
        country = tag['primary_country']
        sector = _primary_sector(ev)
        source = ev.get('source') or ev.get('source_id') or 'unknown'
        on_home = bool(should_show_in_main_list(ev))

        if country:
            c = country_stat[country]
            c['stored'] += 1
            c['sources'].add(source)
            if on_home:
                c['homepage'] += 1

        s = sector_stat[sector]
        s['stored'] += 1
        if country:
            s['countries'].add(country)
        if on_home:
            s['homepage'] += 1

        src = source_stat[source]
        src['stored'] += 1
        if country:
            src['countries'].add(country)
        src['sectors'].add(sector)
        if on_home:
            src['homepage'] += 1

        if country:
            cross[(country, sector)]['stored'] += 1
            if on_home:
                cross[(country, sector)]['homepage'] += 1

    return country_stat, sector_stat, source_stat, cross


def main():
    ap = argparse.ArgumentParser(description='Country x sector x source signal coverage')
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--events')
    ap.add_argument('--pool')
    args = ap.parse_args()

    raw = _load_json(args.events or data_path('events.json'))
    events = _flatten_events(raw)
    pool = _load_json(args.pool or data_path('entity_pool.json'))
    entity_map = entity_country_map(pool)

    # filter to last N days
    dates = sorted({e.get('date') for e in events if e.get('date')})
    cutoff = None
    if len(dates) > args.days:
        cutoff = dates[-args.days]
    if cutoff:
        events = [e for e in events if e.get('date') >= cutoff]

    country_stat, sector_stat, source_stat, cross = build_report(events, entity_map)

    tagged = sum(1 for e in events if tag_event_country(e, entity_map=entity_map)['primary_country'])
    _safe_print('signal country coverage | days=%d events=%d tagged=%d (%.0f%%)' % (
        args.days, len(events), tagged, 100.0 * tagged / len(events) if events else 0))

    # 1) 国家榜：机会密度 = stored + homepage
    _safe_print('\ncountry | stored | homepage | hprate% | sources | ')
    for c, st in sorted(country_stat.items(), key=lambda kv: (-kv[1]['homepage'], -kv[1]['stored'])):
        hpr = 100.0 * st['homepage'] / st['stored'] if st['stored'] else 0
        _safe_print('%s | %d | %d | %.0f | %d' % (c, st['stored'], st['homepage'], hpr, len(st['sources'])))

    # 2) 赛道榜
    _safe_print('\nsector | stored | homepage | hprate% | countries |')
    for s, st in sorted(sector_stat.items(), key=lambda kv: (-kv[1]['homepage'], -kv[1]['stored'])):
        hpr = 100.0 * st['homepage'] / st['stored'] if st['stored'] else 0
        _safe_print('%s | %d | %d | %.0f | %d' % (s, st['stored'], st['homepage'], hpr, len(st['countries'])))

    # 3) 源贡献榜
    _safe_print('\nsource | stored | homepage | hprate% | countries | sectors')
    for s, st in sorted(source_stat.items(), key=lambda kv: -kv[1]['homepage'])[:40]:
        hpr = 100.0 * st['homepage'] / st['stored'] if st['stored'] else 0
        _safe_print('%s | %d | %d | %.0f | %d | %d' % (s, st['stored'], st['homepage'], hpr, len(st['countries']), len(st['sectors'])))

    # 4) 国家×赛道缺口矩阵（只显示有 stored 的格子）
    _safe_print('\ncountry x sector gap matrix (stored):')
    countries = sorted({c for c, _ in cross})
    sectors = sorted({s for _, s in cross})
    _safe_print('%-10s | %s' % ('country', ' | '.join(sectors)))
    for c in countries:
        row = []
        for s in sectors:
            cell = cross.get((c, s))
            row.append(str(cell['stored']) if cell else '-')
        _safe_print('%-10s | %s' % (c, ' | '.join(row)))


if __name__ == '__main__':
    main()
