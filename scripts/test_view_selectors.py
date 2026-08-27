from view_selectors import (
    apply_view_contract,
    select_company_events,
    select_company_quality_events,
    select_feed_events,
    select_homepage_events,
    select_mature_main_date,
    select_review_events,
)
from generate_html import build_daily_event_groups


def base_event(**overrides):
    event = {
        'title': 'Example raises $100M to expand AI infrastructure',
        'url': 'https://example.com/article',
        'source': 'TechCrunch',
        'source_tier': 'L2 垂直交易源',
        'event_types': ['funding'],
        'score': 7,
        'reason': '大额融资显示AI基础设施预算窗口打开',
        'impact': '云服务商、AI基础设施供应商',
        'summary_short': 'Example获$100M融资扩张AI基础设施',
        'date': '2026-05-31',
    }
    event.update(overrides)
    return event


def test_homepage_selector_allows_low_score_non_google_signal():
    event = base_event(score=2)
    selected = select_homepage_events([event], '2026-05-31')
    assert selected == [event]


def test_view_contract_does_not_change_after_presentation_enrichment():
    event = base_event(
        title='SaaS startup raises funding from investors',
        summary_short='SaaS startup raises funding.',
        reason='资本进入但没有明确业务动作。',
        impact='投资机构',
    )
    apply_view_contract(event)
    # all-events-ai-first 决策下：低行动性融资进复核层（review）而非直接过滤，
    # 冻结后不因 enrich 改变
    assert event['view_status'] == 'review'
    event['reason'] = '融资将用于扩张云平台、开发者生态和新市场。'
    event['score'] = 10
    assert select_homepage_events([event], '2026-05-31') == []


def test_feed_selector_falls_back_to_latest_high_value_date():
    low_today = base_event(score=1, event_types=['other'], date='2026-06-01')
    high_yesterday = base_event(score=7, date='2026-05-31')
    feed_events, feed_date = select_feed_events([low_today], [low_today, high_yesterday])
    assert feed_events == [high_yesterday]
    assert feed_date == '2026-05-31'


def test_feed_selector_fills_with_today_main_events_up_to_limit():
    events = [
        base_event(score=7, url='https://example.com/high-1'),
        base_event(score=2, url='https://example.com/main-1'),
        base_event(score=2, url='https://example.com/main-2'),
        base_event(score=2, url='https://example.com/main-3'),
        base_event(score=2, url='https://example.com/main-4'),
        base_event(score=2, url='https://example.com/main-5'),
    ]
    feed_events, feed_date = select_feed_events(events, events, limit=5)
    assert len(feed_events) == 5
    assert feed_events[0]['url'] == 'https://example.com/high-1'
    assert feed_date == ''


def test_feed_selector_default_returns_all_qualified_events():
    events = [
        base_event(score=7, url=f'https://example.com/event-{index}')
        for index in range(6)
    ]

    feed_events, feed_date = select_feed_events(events, events)

    assert len(feed_events) == 6
    assert feed_date == ''


def test_feed_selector_excludes_google_news_high_value():
    google_high = base_event(
        source='Google News',
        source_tier='L5 Google News 补漏源',
        url='https://news.google.com/rss/articles/high',
        event_types=['ma'],
        score=8,
        company_name='Careem',
        is_company=True,
    )
    direct_high = base_event(
        source='TechCrunch',
        source_tier='L2 垂直交易源',
        url='https://example.com/direct',
        score=7,
    )
    feed_events, feed_date = select_feed_events([google_high, direct_high], [google_high, direct_high])
    assert feed_events == [direct_high]
    assert feed_date == ''


def test_company_quality_selector_is_independent_from_rss_high_value():
    company_signal = base_event(
        source='Google News',
        source_tier='L5 Google News 补漏源',
        url='https://news.google.com/rss/articles/example',
        company_name='U-NEXT',
        is_company=True,
        event_types=['ma'],
        score=3,
    )
    assert select_company_quality_events([company_signal]) == [company_signal]


def test_company_quality_selector_blocks_edge_company_noise():
    edge_signal = base_event(
        title='Final Fantasy XIV from Square Enix Holdings gets new expansion',
        source='Google News',
        source_tier='L5 Google News 补漏源',
        url='https://news.google.com/rss/articles/edge',
        company_name='Square Enix',
        is_company=True,
        event_types=['strategy'],
        score=5,
        reason='资料片更新',
        impact='玩家',
        summary_short='Square Enix资料片更新',
    )
    assert select_company_quality_events([edge_signal]) == []


def test_mature_date_selector_skips_thin_latest_batch():
    events_by_date = {
        '2026-06-01': [base_event(date='2026-06-01')],
        '2026-05-31': [
            base_event(date='2026-05-31', url='https://example.com/a'),
            base_event(date='2026-05-31', url='https://example.com/b'),
            base_event(date='2026-05-31', url='https://example.com/c'),
        ],
    }
    visible = events_by_date['2026-06-01'] + events_by_date['2026-05-31']
    main_date, latest_date, latest_count, notice = select_mature_main_date(
        ['2026-06-01', '2026-05-31'],
        visible,
        events_by_date,
    )
    assert main_date == '2026-05-31'
    assert latest_date == '2026-06-01'
    assert latest_count == 1
    assert notice


def test_mature_date_selector_counts_main_list_only():
    latest_main = base_event(date='2026-06-01', url='https://example.com/latest-main')
    latest_google_review = base_event(
        date='2026-06-01',
        url='https://news.google.com/rss/articles/latest-review',
        source='Google News',
        source_tier='L5 Google News 补漏源',
        event_types=['strategy'],
        score=5,
    )
    latest_low_google = base_event(
        date='2026-06-01',
        url='https://news.google.com/rss/articles/latest-low',
        source='Google News',
        source_tier='L5 Google News 补漏源',
        event_types=['strategy'],
        score=4,
    )
    mature = [
        base_event(date='2026-05-31', url='https://example.com/a'),
        base_event(date='2026-05-31', url='https://example.com/b'),
        base_event(date='2026-05-31', url='https://example.com/c'),
    ]
    events_by_date = {
        '2026-06-01': [latest_main, latest_google_review, latest_low_google],
        '2026-05-31': mature,
    }
    visible = events_by_date['2026-06-01'] + mature
    main_date, latest_date, latest_count, notice = select_mature_main_date(
        ['2026-06-01', '2026-05-31'],
        visible,
        events_by_date,
    )
    assert main_date == '2026-05-31'
    assert latest_date == '2026-06-01'
    assert latest_count == 3
    assert notice


def test_review_selector_keeps_real_google_org_action_out_of_main():
    event = base_event(
        title='Naver Eyes Baemin Acquisition to Accelerate Super App Strategy',
        source='Google News',
        source_tier='L5 Google News 补漏源',
        url='https://news.google.com/rss/articles/example',
        company_name='Naver',
        is_company=True,
        event_types=['ma'],
        attention_score=60,
        confidence_score=60,
    )
    assert select_review_events([event]) == [event]


def test_daily_event_groups_keep_all_homepage_events_visible():
    selected = base_event(attention_score=75, confidence_score=70, url='https://example.com/selected')
    important = base_event(attention_score=60, confidence_score=60, url='https://example.com/important', event_types=['strategy'])
    watch = base_event(attention_score=25, confidence_score=30, url='https://example.com/watch')
    homepage_events = select_homepage_events([selected, important, watch], '2026-05-31')
    groups = build_daily_event_groups(homepage_events)

    grouped_total = sum(len(group['events']) for group in groups)
    counts = {group['label']: len(group['events']) for group in groups}
    assert grouped_total == len(homepage_events)
    assert counts == {'精选': 1, '重点': 1, '观察': 1}


def test_old_out_of_scope_events_do_not_bypass_history_gate():
    events_by_date = {
        '2026-05-01': [
            base_event(
                date='2026-05-01',
                title='Defense tech startup raises $500M for military drones',
                summary_short='国防科技公司获融资',
                reason='军工AI融资',
                impact='军工供应链',
                event_types=['funding'],
                score=9,
            )
        ]
    }
    _, generic = select_company_events(events_by_date, '2026-06-01')
    assert generic == []


if __name__ == '__main__':
    test_homepage_selector_allows_low_score_non_google_signal()
    test_feed_selector_falls_back_to_latest_high_value_date()
    test_feed_selector_fills_with_today_main_events_up_to_limit()
    test_feed_selector_default_returns_all_qualified_events()
    test_feed_selector_excludes_google_news_high_value()
    test_company_quality_selector_is_independent_from_rss_high_value()
    test_company_quality_selector_blocks_edge_company_noise()
    test_mature_date_selector_skips_thin_latest_batch()
    test_mature_date_selector_counts_main_list_only()
    test_review_selector_keeps_real_google_org_action_out_of_main()
    test_daily_event_groups_keep_all_homepage_events_visible()
    test_old_out_of_scope_events_do_not_bypass_history_gate()
    print('view selector tests passed')
