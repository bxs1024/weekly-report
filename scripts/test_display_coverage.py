"""覆盖面角标（首页事件卡 + 公司索引）取数口径测试。

背景：本站三道去重把同一事件的多家报道合并成一个事件，只留下 merged_from（被并
URL）与 merged_sources（被并信源名）。覆盖面是去重的**静态副产品**，展示层读取时
现算，不依赖任何归组 / 热度工位，也不落库。

「谁发的稿」要按 publisher or source 取——source 常是聚合器 "Google News"，
真实媒体在 publisher；origin_source_id 是被报道的**公司**，不是信源，绝不能算进去。
这一层很容易写错且错了不报错（只会让覆盖面虚高），所以单独锁住。
"""

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from publication.display import (  # noqa: E402
    _source_names, build_company_cards, coverage_counts, coverage_label,
    enrich_frontend_fields,
)


# ---------- 角标文案：存量已落库字段 ----------

def test_label_empty_when_single_source():
    assert coverage_label({}) == ''
    assert coverage_label({'event_source_count': 1, 'event_report_count': 1}) == ''


def test_label_prefers_source_count_over_report_count():
    assert coverage_label({'event_source_count': 3, 'event_report_count': 9}) == '3 家来源'


def test_label_falls_back_to_report_count():
    """存量数据没有 merged_sources，信源名已丢失，只能说「N 篇报道」。"""
    assert coverage_label({'event_source_count': 0, 'event_report_count': 4}) == '4 篇报道'


def test_label_survives_dirty_values():
    assert coverage_label({'event_source_count': 'x', 'event_report_count': None}) == ''
    assert coverage_label({'event_source_count': None, 'event_report_count': '2'}) == '2 篇报道'


# ---------- 信源取数口径 ----------

def test_sources_prefer_publisher_over_aggregator():
    """source='Google News' 是聚合器，真实媒体在 publisher——只算一家。"""
    assert _source_names({'source': 'Google News', 'publisher': 'Reuters'}) == ['reuters']


def test_sources_exclude_origin_source_id():
    """origin_source_id 是被报道的公司，不是信源；算进来会让每条公司新闻虚增一家。"""
    assert _source_names({'source': 'TechCrunch', 'origin_source_id': 'Adyen'}) == ['techcrunch']


def test_sources_include_merged_siblings_once():
    assert _source_names({
        'source': 'TechCrunch',
        'merged_sources': ['TechCrunch', 'Reuters', '  ', 'reuters'],
    }) == ['techcrunch', 'reuters']


# ---------- 新数据：没有落库字段，从去重记录现算 ----------

def test_counts_use_stored_fields_when_present():
    """迁移不可改写：存量字段原样读，页面文案与改动前逐条一致。"""
    assert coverage_counts({'event_source_count': 3, 'event_report_count': 5}) == (3, 5)


def test_counts_derive_from_dedupe_evidence():
    event = {'publisher': 'Reuters', 'merged_sources': ['Reuters', 'The Verge'],
             'merged_from': ['u1', 'u2']}
    assert coverage_counts(event) == (2, 3)
    assert coverage_label(event) == '2 家来源'


def test_new_event_without_stored_fields_still_gets_label():
    """反事实：删掉归组层后新事件身上没有 event_* 字段，角标不能因此消失。"""
    assert coverage_label({'merged_from': ['u1', 'u2']}) == '3 篇报道'


def test_grouping_leftovers_do_not_change_label():
    """反事实：旧数据身上的 group_size / event_heat 不该再影响展示。"""
    event = {'group_size': 1, 'event_heat': 0, 'merged_from': ['u1']}
    assert coverage_label(event) == '2 篇报道'


def test_display_layer_does_not_import_removed_module():
    """展示层与已拆掉的归组模块必须彻底断开，不能留隐式依赖。"""
    import publication.display as display
    src = inspect.getsource(display)
    assert 'event_grouping' not in src
    assert 'event_heat' not in src
    assert 'group_id' not in src


# ---------- 展示层接线 ----------

def test_enrich_frontend_fields_sets_label():
    events = [{'title': 'T', 'event_report_count': 3}]
    enrich_frontend_fields(events)
    assert events[0]['heat_label'] == '3 篇报道'


def test_enrich_frontend_fields_uses_dedupe_evidence_for_new_events():
    events = [{'title': 'T', 'merged_from': ['u1']}]
    enrich_frontend_fields(events)
    assert events[0]['heat_label'] == '2 篇报道'


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
    print('display coverage tests passed')


if __name__ == '__main__':
    _run_all()
