"""覆盖面角标（首页事件卡 + 公司索引）与热度取数口径测试。

背景：本站去重层比归组层先跑，同一事件的多家报道在入库时就被合并成一个事件，
只剩 merged_from（URL）与 merged_sources（信源名）。所以「谁发的稿」要按
publisher or source 取——source 常是聚合器 "Google News"，真实媒体在 publisher；
而 origin_source_id 是被报道的**公司**（Adyen 这类），不是信源，绝不能算进去。
这一层很容易写错且错了不报错（只会让热度虚高），所以单独锁住。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import event_grouping
from publication.display import build_company_cards, enrich_frontend_fields, heat_label


# ---------- 角标文案 ----------

def test_label_empty_when_single_source():
    assert heat_label({}) == ''
    assert heat_label({'event_source_count': 1, 'event_report_count': 1}) == ''


def test_label_prefers_source_count_over_report_count():
    assert heat_label({'event_source_count': 3, 'event_report_count': 9}) == '3 家来源'


def test_label_falls_back_to_report_count():
    """存量数据没有 merged_sources，信源名已丢失，只能说「N 篇报道」。"""
    assert heat_label({'event_source_count': 0, 'event_report_count': 4}) == '4 篇报道'


def test_label_survives_dirty_values():
    assert heat_label({'event_source_count': 'x', 'event_report_count': None}) == ''
    assert heat_label({'event_source_count': None, 'event_report_count': '2'}) == '2 篇报道'


# ---------- 信源取数口径 ----------

def test_sources_prefer_publisher_over_aggregator():
    """source='Google News' 是聚合器，真实媒体在 publisher——只算一家。"""
    assert event_grouping._event_sources(
        {'source': 'Google News', 'publisher': 'Reuters'}) == ['reuters']


def test_sources_exclude_origin_source_id():
    """origin_source_id 是被报道的公司，不是信源；算进来会让每条公司新闻虚增一家。"""
    sources = event_grouping._event_sources(
        {'source': 'TechCrunch', 'origin_source_id': 'Adyen'})
    assert sources == ['techcrunch']


def test_sources_include_merged_siblings_once():
    sources = event_grouping._event_sources({
        'source': 'TechCrunch',
        'merged_sources': ['TechCrunch', 'Reuters', '  ', 'reuters'],
    })
    assert sources == ['techcrunch', 'reuters']


# ---------- 热度计算 ----------

def _event(**overrides):
    base = {
        'event_id': 'e1', 'date': '2026-09-02', 'source': 'TechCrunch',
        'group_id': 'g1', 'event_types': ['strategy'],
    }
    base.update(overrides)
    return base


def test_heat_counts_merged_siblings_as_sources():
    events = [_event(merged_sources=['Reuters', 'The Verge'])]
    result = event_grouping.compute_heat(events, now=event_grouping._parse_date('2026-09-02'))
    assert result['g1']['sources'] == 3, result
    assert result['g1']['heat'] == 3.0, result
    assert events[0]['event_source_count'] == 3


def test_heat_same_source_across_reports_counted_once():
    """同一家媒体发两篇（或同日合并进来）只算一次来源，但报道篇数要算两次。"""
    events = [_event(merged_from=['u1', 'u2'], merged_sources=['TechCrunch'])]
    result = event_grouping.compute_heat(events, now=event_grouping._parse_date('2026-09-02'))
    assert result['g1']['sources'] == 1, result
    assert result['g1']['reports'] == 3, result
    assert events[0]['event_report_count'] == 3


def test_heat_zero_outside_window_but_reports_still_counted():
    """48h 窗口外热度归零，但报道篇数（存量数据唯一可用的覆盖面证据）照常给出。"""
    events = [_event(date='2026-08-01', merged_from=['u1'])]
    result = event_grouping.compute_heat(events, now=event_grouping._parse_date('2026-09-02'))
    assert result['g1']['heat'] == 0, result
    assert result['g1']['sources'] == 0, result
    assert events[0]['event_report_count'] == 2


def test_heat_decays_between_24h_and_48h():
    events = [
        _event(event_id='a', date='2026-09-02', source='TechCrunch'),
        _event(event_id='b', date='2026-08-31', source='Reuters'),
    ]
    for e in events:
        e['group_id'] = 'g1'
    result = event_grouping.compute_heat(events, now=event_grouping._parse_date('2026-09-02'))
    # 当天 1.0，超过 24h 的减半
    assert result['g1']['heat'] == 1.5, result
    assert result['g1']['sources'] == 2


# ---------- 展示层接线 ----------

def test_enrich_frontend_fields_sets_label():
    events = [{'title': 'T', 'event_report_count': 3}]
    enrich_frontend_fields(events)
    assert events[0]['heat_label'] == '3 篇报道'


def test_company_card_takes_widest_coverage_label():
    company_list = [{
        'name': 'Acme', 'region': '亚太', 'count': 2, 'portfolio_tier': 'watch',
        'events': [
            {'date': '2026-09-02', 'title': 'A', 'event_report_count': 2},
            {'date': '2026-09-01', 'title': 'B', 'event_source_count': 4},
        ],
    }]
    cards = build_company_cards(company_list, '2026-09-02')
    assert cards[0]['heat_label'] == '4 家来源', cards[0]['heat_label']


def test_company_card_has_no_label_without_coverage():
    company_list = [{
        'name': 'Acme', 'region': '亚太', 'count': 1, 'portfolio_tier': 'watch',
        'events': [{'date': '2026-09-02', 'title': 'A'}],
    }]
    cards = build_company_cards(company_list, '2026-09-02')
    assert cards[0]['heat_label'] == ''


def _run_all():
    for name, func in sorted(globals().items()):
        if name.startswith('test_') and callable(func):
            func()
    print('display heat tests passed')


if __name__ == '__main__':
    _run_all()
