from event_value import (
    classify_bd_priority,
    event_filter_reason,
    is_company_quality_signal,
    is_high_value_event,
    should_show_in_main_list,
    should_show_in_review,
)


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
    }
    event.update(overrides)
    return event


def test_low_score_signal_is_not_high():
    event = base_event(attention_score=30, confidence_score=30)
    assert classify_bd_priority(event) == '观察'
    assert not is_high_value_event(event)
    assert should_show_in_main_list(event)


def test_google_news_company_low_signal_stays_out():
    event = base_event(
        title='K-pop singer to star in Rakuten Viki romcom',
        url='https://news.google.com/rss/articles/example',
        source='Google News',
        source_tier='L5 Google News 补漏源',
        company_name='Rakuten',
        is_company=True,
        score=5,
    )
    assert classify_bd_priority(event) == '观察'
    assert not should_show_in_main_list(event)
    assert not should_show_in_review(event)


def test_google_news_real_org_action_can_enter_review_not_main():
    event = base_event(
        title='Naver Eyes Baemin Acquisition to Accelerate Super App Strategy',
        url='https://news.google.com/rss/articles/example',
        source='Google News',
        source_tier='L5 Google News 补漏源',
        company_name='Naver',
        is_company=True,
        event_types=['ma'],
        attention_score=60,
        confidence_score=60,
    )
    assert classify_bd_priority(event) == '中'
    assert not should_show_in_main_list(event)
    assert should_show_in_review(event)


def test_non_google_strong_signal_is_high_value():
    event = base_event(attention_score=60, confidence_score=60)
    assert classify_bd_priority(event) == '高'
    assert is_high_value_event(event)
    assert should_show_in_main_list(event)


def test_capital_only_funding_is_watch_not_homepage_main():
    event = base_event(
        title='SaaS startup raises $80M from global investors',
        summary_short='SaaS startup获融资',
        reason='资本进入但未体现明确业务动作',
        impact='投资机构',
        signal_taxonomy=['capital'],
        score=7,
    )
    assert classify_bd_priority(event) == '观察'
    assert event_filter_reason(event) == 'capital_only_low_actionability'
    assert not is_high_value_event(event)
    assert not should_show_in_main_list(event)


def test_actionable_ai_infra_funding_can_stay_homepage_main():
    event = base_event(
        title='AI infrastructure platform raises $120M to expand inference cloud',
        summary_short='AI基础设施平台融资扩张推理云',
        reason='大额融资明确用于AI基础设施和云服务扩张',
        impact='云服务商、GPU供应链、企业AI集成商',
        signal_taxonomy=['capital', 'ai_infra'],
        attention_score=75,
        confidence_score=70,
    )
    assert classify_bd_priority(event) == '高'
    assert is_high_value_event(event)
    assert should_show_in_main_list(event)


def test_company_quality_has_own_threshold():
    event = base_event(
        source='Google News',
        source_tier='L5 Google News 补漏源',
        url='https://news.google.com/rss/articles/example',
        company_name='U-NEXT',
        is_company=True,
        event_types=['ma'],
        score=3,
    )
    assert is_company_quality_signal(event)


def test_out_of_scope_healthcare_does_not_enter_main_or_rss_value():
    event = base_event(
        title='Tavo Biotherapeutics secures $17M for ophthalmology therapies',
        reason='眼科疗法和生物制药研发获融资',
        impact='医疗器械供应商、临床试验服务商',
        summary_short='Tavo Biotherapeutics获$17M融资开发眼科疗法',
        score=8,
    )
    assert classify_bd_priority(event) == '观察'
    assert not is_high_value_event(event)
    assert not should_show_in_main_list(event)
    assert not should_show_in_review(event)


def test_defense_tech_does_not_enter_main_even_when_large():
    event = base_event(
        title='Defense tech startup raises $500M for military drones',
        reason='国防科技和军工AI融资',
        impact='军工供应链企业',
        summary_short='国防科技公司获$500M融资',
        score=9,
    )
    assert not is_high_value_event(event)
    assert not should_show_in_main_list(event)
    assert not should_show_in_review(event)
    assert event_filter_reason(event) == 'out_of_scope_industry'


def test_research_report_can_enter_main_with_signal_scores():
    event = base_event(
        title='IDC publishes China cloud market share forecast report',
        source='IDC Research',
        source_tier='L4 垂直赛道精品源',
        source_type='research_report',
        event_types=['other'],
        score=6,
        summary_short='IDC发布中国云计算市场份额预测',
        reason='研报公开摘要显示云计算市场结构和区域分布变化',
        impact='云服务商、数据中心和企业软件供应商',
        scope_enforced=True,
        scope_status='qualified',
        scope_industries=['ai_infra', 'cloud_saas_developer'],
        content_type='industry_report',
        confidence_score=88,
        attention_score=82,
    )
    assert classify_bd_priority(event) == '高'
    assert is_high_value_event(event)
    assert should_show_in_main_list(event)


def test_model_release_is_separate_from_performance_claim():
    event = base_event(
        title='Open source multimodal model launches with API access',
        source='Model Lab',
        source_tier='L1 官方/IR源',
        event_types=['model_release'],
        score=5,
        summary_short='开放多模态模型并提供 API',
        reason='模型发布事实明确，API 可获得性可直接验证',
        impact='AI 应用开发者、云服务商和集成商',
        scope_enforced=True,
        scope_status='qualified',
        scope_industries=['ai_infra'],
        content_type='model_release',
        subject_type='ai_model',
        claim_type='release_fact',
        confidence_score=90,
        attention_score=80,
    )
    assert classify_bd_priority(event) == '高'
    assert should_show_in_main_list(event)


def test_paid_report_without_public_basis_needs_review():
    event = base_event(
        title='IDC publishes a paid cloud market outlook',
        source='IDC Research',
        source_tier='L4 垂直赛道精品源',
        source_type='research_report',
        event_types=['other'],
        score=7,
        summary_short='IDC发布付费云市场展望',
        reason='报告判断可能影响云服务商',
        impact='云服务商',
        scope_enforced=True,
        scope_status='qualified',
        scope_industries=['ai_infra', 'cloud_saas_developer'],
        content_type='industry_report',
        report_access_level='paid',
        source_excerpt='',
        interpretation_basis='',
        confidence_score=90,
        attention_score=90,
    )
    assert event_filter_reason(event) == 'quality_review'
    assert not should_show_in_main_list(event)


if __name__ == '__main__':
    test_low_score_signal_is_not_high()
    test_google_news_company_low_signal_stays_out()
    test_google_news_real_org_action_can_enter_review_not_main()
    test_non_google_strong_signal_is_high_value()
    test_capital_only_funding_is_watch_not_homepage_main()
    test_actionable_ai_infra_funding_can_stay_homepage_main()
    test_company_quality_has_own_threshold()
    test_out_of_scope_healthcare_does_not_enter_main_or_rss_value()
    test_defense_tech_does_not_enter_main_even_when_large()
    test_research_report_can_enter_main_with_signal_scores()
    test_model_release_is_separate_from_performance_claim()
    test_paid_report_without_public_basis_needs_review()
    print('event_value tests passed')
