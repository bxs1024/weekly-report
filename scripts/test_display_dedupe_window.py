"""展示层去重「日期分桶」优化的等价性测试（差分测试）。

背景（2026-10-07）：`dedupe_display_events` 原实现对每个事件全量扫 `kept` 调
`_is_same_event`，是 O(n²)。实测 2062 条输入约 186 万次调用，是整站渲染的主要
耗时（本地 profile 里 `_is_same_event` 累计占比最高）。而 `build_display_context`
在渲染流程里被跑了两次（HTML 一次、RSS 又一次），成本翻倍 → CI 上渲染步骤要
17–33 分钟，顶穿 60 分钟 job 超时。

优化按「日期 ±7 天」分桶（`_SAME_EVENT_MAX_WINDOW_DAYS`，取自
content/classify.py `_dates_adjacent` 的最长窗口），并单列三个索引补上跨日期路径：
  · url 索引           —— `_is_same_event` 第一条就是 URL 相等即判同，不看日期
  · kept_undated       —— `_dates_adjacent` 对空日期返回 True，必须全比
  · kept_company 索引  —— `_fingerprint_match` 只比公司+锚点，完全不看日期

这里用「参考实现（原全量扫描）+ 对抗性语料」做差分：两者输出必须逐条一致。
只要分桶漏掉任何一个本该判同的配对，签名就会分叉。
"""

import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from content.dedupe import _fingerprint_match, _is_same_event
from publication.display_dedupe import (
    _display_subject_key,
    _nearby_days,
    _normalized_title_key,
    _title_similarity,
    dedupe_display_events,
)


def _reference_dedupe(events):
    """优化前的实现：对每个事件全量扫 kept。只用于差分比对，不进生产路径。"""
    kept = []
    seen_titles = set()
    seen_semantic = []
    for event in events:
        title_key = _normalized_title_key(event.get('title', ''))
        if title_key and title_key in seen_titles:
            continue
        if title_key:
            seen_titles.add(title_key)

        if event.get('canonical_company'):
            match = next((ev for ev in kept if _fingerprint_match(event, ev)), None)
            if match is not None:
                if event.get('url'):
                    match.setdefault('merged_from', [])
                    if event['url'] not in match['merged_from']:
                        match['merged_from'].append(event['url'])
                continue

        event_type = (event.get('event_types') or ['other'])[0]
        if event_type != 'strategy':
            match = next((ev for ev in kept if _is_same_event(event, ev)), None)
            if match is not None:
                if event.get('url'):
                    match.setdefault('merged_from', [])
                    if event['url'] not in match['merged_from']:
                        match['merged_from'].append(event['url'])
                continue

        date_key = (event.get('date') or '')[:10]
        subject_key = _display_subject_key(event)
        if subject_key and event_type in {'funding', 'ma', 'earnings', 'strategy'}:
            dup = False
            for seen_date, seen_type, seen_subject, seen_title in seen_semantic:
                if seen_type != event_type or seen_subject != subject_key:
                    continue
                if event_type == 'strategy':
                    same_window = (seen_date == date_key)
                else:
                    same_window = _nearby_days(seen_date, date_key, window=3)
                if same_window and _title_similarity(event.get('title', ''), seen_title) >= 0.3:
                    dup = True
                    break
            if dup:
                continue
            seen_semantic.append((date_key, event_type, subject_key, event.get('title', '')))
        kept.append(event)
    return kept


def _signature(events):
    """稳定签名：保留顺序 + 每条被并入的 url，能同时反映「留谁」和「并了谁」。"""
    return [(e.get('eid'), tuple(e.get('merged_from') or ())) for e in events]


def _corpus():
    """对抗性语料：专打四条判同路径的边界。"""
    return [
        # ① 同 URL 相隔 75 天 —— 只能靠 url 索引救回来（日期分桶会漏）
        {'eid': 'url-a', 'url': 'https://example.com/same-article', 'date': '2026-01-05',
         'title': 'Acme raises 10 million', 'event_types': ['funding']},
        {'eid': 'url-b', 'url': 'https://example.com/same-article', 'date': '2026-03-21',
         'title': '完全不同的一条标题', 'event_types': ['funding']},

        # ② 指纹相同但相隔 120 天 —— _fingerprint_match 不看日期，只能靠 company 索引救回
        {'eid': 'fp-a', 'url': 'https://example.com/zeta-1', 'date': '2026-02-01',
         'title': 'Zeta closes Series B', 'event_types': ['funding'],
         'canonical_company': 'Zeta', 'canonical_key': '$50M'},
        {'eid': 'fp-b', 'url': 'https://example.com/zeta-2', 'date': '2026-06-01',
         'title': 'Zeta closes Series B round', 'event_types': ['funding'],
         'canonical_company': 'Zeta', 'canonical_key': '$50M'},

        # ③ 指纹公司相同但锚点冲突 —— 必须判为不同事件，不能因分桶被误并
        {'eid': 'fp-c', 'url': 'https://example.com/zeta-3', 'date': '2026-06-02',
         'title': 'Zeta closes Series C', 'event_types': ['funding'],
         'canonical_company': 'Zeta', 'canonical_key': '$300M'},

        # ④ 同实体同类型、相隔 2 天、标题高度相似 —— 窗口内路径
        {'eid': 'near-a', 'url': 'https://example.com/nova-1', 'date': '2026-05-10',
         'title': 'Nova raises 20 million in Series A funding round',
         'event_types': ['funding'], 'company_name': 'Nova'},
        {'eid': 'near-b', 'url': 'https://example.com/nova-2', 'date': '2026-05-12',
         'title': 'Nova raises 20 million in Series A funding',
         'event_types': ['funding'], 'company_name': 'Nova'},

        # ⑤ 同实体同类型但相隔 9 天 —— 超出 7 天窗口，两个实现都必须不并
        {'eid': 'far-a', 'url': 'https://example.com/orbit-1', 'date': '2026-03-01',
         'title': 'Orbit acquires Lumen for 120 million',
         'event_types': ['ma'], 'company_name': 'Orbit'},
        {'eid': 'far-b', 'url': 'https://example.com/orbit-2', 'date': '2026-03-10',
         'title': 'Orbit acquires Lumen for 120 million deal',
         'event_types': ['ma'], 'company_name': 'Orbit'},

        # ⑥ 无日期事件：_dates_adjacent 对空日期返回 True，必须始终参与全比
        {'eid': 'undated-a', 'url': 'https://example.com/nodate-1', 'date': '',
         'title': 'Pine partners with Cedar', 'event_types': ['strategy'],
         'company_name': 'Pine'},
        {'eid': 'undated-b', 'url': 'https://example.com/nodate-2', 'date': '',
         'title': 'Pine partners with Cedar', 'event_types': ['funding'],
         'company_name': 'Pine'},

        # ⑦ strategy 首类型：整段跳过 _is_same_event，只走同日语义窗口
        {'eid': 'strat-a', 'url': 'https://example.com/strat-1', 'date': '2026-04-01',
         'title': 'Vega restructures its cloud division', 'event_types': ['strategy'],
         'company_name': 'Vega'},
        {'eid': 'strat-b', 'url': 'https://example.com/strat-1', 'date': '2026-04-01',
         'title': 'Vega restructures its cloud division unit', 'event_types': ['strategy'],
         'company_name': 'Vega'},

        # ⑧ 普通互不相关事件，保证 kept 里有多样日期分布
        {'eid': 'plain-a', 'url': 'https://example.com/plain-1', 'date': '2026-02-14',
         'title': 'Bank of Nowhere reports Q4 earnings', 'event_types': ['earnings'],
         'company_name': 'Bank of Nowhere'},
        {'eid': 'plain-b', 'url': 'https://example.com/plain-2', 'date': '2026-07-30',
         'title': 'Helios launches a logistics arm', 'event_types': ['industry_report'],
         'company_name': 'Helios'},
    ]


def test_bucketed_dedupe_matches_full_scan_reference():
    corpus = _corpus()
    expected = _signature(_reference_dedupe(copy.deepcopy(corpus)))
    actual = _signature(dedupe_display_events(copy.deepcopy(corpus)))
    assert actual == expected, f'分桶结果与全量扫描不一致\n参考: {expected}\n实际: {actual}'


def test_same_url_merges_across_arbitrary_date_gap():
    """url 相等判同不受日期限制——分桶必须靠 url 索引保住这条路径。"""
    out = dedupe_display_events(copy.deepcopy(_corpus()))
    eids = [e.get('eid') for e in out]
    assert 'url-a' in eids and 'url-b' not in eids


def test_fingerprint_merges_across_arbitrary_date_gap():
    """_fingerprint_match 不看日期——分桶必须靠 company 索引保住这条路径。"""
    out = dedupe_display_events(copy.deepcopy(_corpus()))
    eids = [e.get('eid') for e in out]
    assert 'fp-a' in eids and 'fp-b' not in eids


def test_fingerprint_anchor_conflict_is_not_merged():
    """锚点明确不同（$50M vs $300M）判为不同事件，不能被分桶误并。"""
    out = dedupe_display_events(copy.deepcopy(_corpus()))
    eids = [e.get('eid') for e in out]
    assert 'fp-c' in eids


def test_beyond_window_pair_is_not_merged():
    """相隔 9 天、无指纹 → 两个实现都不并（说明分桶没有把窗口放大）。"""
    out = dedupe_display_events(copy.deepcopy(_corpus()))
    eids = [e.get('eid') for e in out]
    assert 'far-a' in eids and 'far-b' in eids


def test_undated_events_still_compared():
    """无日期事件必须留在全比集合里，否则会漏并。"""
    events = [
        {'eid': 'k1', 'url': 'https://example.com/k1', 'date': '',
         'title': 'Pine partners with Cedar', 'event_types': ['funding'],
         'company_name': 'Pine'},
        {'eid': 'k2', 'url': 'https://example.com/k1', 'date': '2026-09-01',
         'title': 'Pine partners with Cedar', 'event_types': ['funding'],
         'company_name': 'Pine'},
    ]
    expected = _signature(_reference_dedupe(copy.deepcopy(events)))
    actual = _signature(dedupe_display_events(copy.deepcopy(events)))
    assert actual == expected
