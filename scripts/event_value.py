"""Shared event value rules for collection, dashboard, and RSS.

The dashboard should treat "high value" as a product promise, not as a
synonym for "funding/MA-shaped title".
"""

import re
from functools import lru_cache


STRONG_EVENT_TYPES = {
    'funding', 'ma', 'earnings', 'strategy',
    'industry_report', 'model_release', 'regional_policy',
}
HIGH_VALUE_SIGNAL_TYPES = {'funding', 'ma', 'earnings'}
FIRST_CLASS_CONTENT_TYPES = {'industry_report', 'model_release', 'regional_policy'}
ACTIONABLE_FUNDING_TERMS = {
    'ai', 'artificial intelligence', 'agent', 'model', 'llm', 'inference',
    'cloud', 'data center', 'datacenter', 'gpu', 'compute', 'infrastructure',
    'payment', 'payments', 'fintech', 'wallet', 'bnpl', 'remittance',
    'checkout', 'merchant', 'acquiring', 'banking app', 'digital bank',
    'ecommerce', 'e-commerce', 'commerce', 'marketplace', 'seller',
    'saas', 'enterprise software', 'developer', 'api', 'sdk', 'platform',
    'cybersecurity', 'security platform', 'identity', 'ads', 'advertising',
    'adtech', 'marketing automation', 'gaming', 'game', 'streaming',
    'super app', 'ride-hailing', 'delivery', 'food delivery',
    'logistics platform', 'fulfillment', 'last-mile',
    'ai基础设施', '算力', '云', '数据中心', '支付', '金融科技', '钱包',
    '电商', '商户', 'saas', '企业软件', '开发者', 'api', '网络安全',
    '广告', '游戏', '流媒体', '超级app', '本地生活', '出行', '外卖',
    '物流平台',
}
FUNDING_ACTION_TERMS = {
    'expand', 'expands', 'expansion', 'scale', 'scales', 'scaling',
    'launch', 'launches', 'roll out', 'rolls out', 'enter', 'enters',
    'new market', 'infrastructure', 'build',
    'buildout', 'merchant', 'partner', 'partnership', 'ecosystem',
    'developer', 'api', 'product', 'platform', 'capacity', 'data center',
    'datacenter', 'gpu', 'cloud', 'payment', 'checkout', 'acquiring',
    '扩张', '出海', '区域', '基础设施', '建设', '生态',
    '合作', '商户', '开发者', '接口', '产品', '平台', '产能',
    '数据中心', '算力', '支付', '收单',
}
GOOGLE_NEWS_LOW_SIGNAL_TERMS = {
    'stock', 'shares', 'share price', 'trading limit', 'buy rating',
    'price target', 'stock split', 'director adds', 'open-market buys',
    'k-pop', 'singer', 'romcom', 'drama', 'merchandise', 'training program',
    'promotion', 'rewards', 'contest', 'profile', 'interview',
}

try:
    from internet_relevance import (
        internet_relevance_score,
        is_mainline_internet_event,
    )
    from scope_gate import is_scope_qualified, scope_filter_reason
except ImportError:
    from scripts.internet_relevance import (
        internet_relevance_score,
        is_mainline_internet_event,
    )
    from scripts.scope_gate import is_scope_qualified, scope_filter_reason


def event_type(event):
    types = event.get('event_types') or ['other']
    return types[0] if types else 'other'


def _rule_layer_score(event):
    """规则层注意力分（0-100）；存量事件缺字段时实时补算（内存态，不写回）。"""
    score = signal_score(event, 'attention_score')
    if score > 0:
        return score
    try:
        from signal_scoring import apply_signal_contract
        apply_signal_contract(event)
    except Exception:
        pass
    return signal_score(event, 'attention_score')


def event_score(event):
    """展示/排序用分数：统一走规则层注意力分（0-100），AI 0-10 分不再参与展示决策。"""
    return _rule_layer_score(event)


def signal_score(event, field):
    try:
        return float(event.get(field) or 0)
    except (TypeError, ValueError):
        return 0


def content_type(event):
    value = event.get('content_type')
    if value in FIRST_CLASS_CONTENT_TYPES:
        return value
    return ''


def is_google_news_event(event):
    source = (event.get('source') or '').lower()
    tier = event.get('source_tier') or ''
    url = (event.get('url') or '').lower()
    return source == 'google news' or tier == 'L5 Google News 补漏源' or 'news.google.com' in url


def has_explainable_analysis(event):
    reason = (event.get('reason') or '').strip()
    impact = (event.get('display_impact') or event.get('impact') or '').strip()
    summary = (event.get('summary_short') or '').strip()
    if not reason or not summary:
        return False
    if not impact or impact == '未知':
        return False
    return True


def is_low_signal_google_news(event):
    if not is_google_news_event(event):
        return False
    text = ' '.join([
        event.get('title') or '',
        event.get('summary_short') or '',
        event.get('reason') or '',
    ]).lower()
    return any(term in text for term in GOOGLE_NEWS_LOW_SIGNAL_TERMS)


@lru_cache(maxsize=None)
def _term_matcher(term):
    lowered = term.lower()
    if re.search(r'[a-z0-9]', lowered):
        return re.compile(rf'(?<![a-z0-9]){re.escape(lowered)}(?![a-z0-9])'), None
    return None, lowered


def _contains_term(text, term):
    pattern, lowered = _term_matcher(term)
    if pattern is None:
        return lowered in text
    return bool(pattern.search(text))


def _funding_fact_text(event):
    return ' '.join([
        event.get('title') or '',
        event.get('display_title') or '',
        event.get('summary_short') or '',
        event.get('source') or '',
        event.get('company_name') or '',
        ' '.join(event.get('companies') or []),
        ' '.join(event.get('signal_taxonomy') or []),
        ' '.join(event.get('source_signal_types') or []),
    ]).lower()


def is_actionable_funding_event(event):
    if event_type(event) != 'funding':
        return True
    text = _funding_fact_text(event)
    has_domain = any(_contains_term(text, term) for term in ACTIONABLE_FUNDING_TERMS)
    has_action = any(_contains_term(text, term) for term in FUNDING_ACTION_TERMS)
    signals = set(event.get('signal_taxonomy') or [])
    has_non_capital_signal = bool(signals - {'capital', 'general'})
    return has_domain and (has_action or has_non_capital_signal)


def event_filter_reason(event):
    scope_reason = scope_filter_reason(event)
    if scope_reason:
        return scope_reason
    if internet_relevance_score(event) < 2:
        return 'out_of_scope_industry'
    if event_type(event) == 'funding' and not is_actionable_funding_event(event):
        return 'capital_only_low_actionability'
    if needs_quality_review(event):
        return 'quality_review'
    if is_google_news_event(event):
        return 'google_not_main'
    if event_type(event) == 'other':
        return 'other_type'
    return 'weak_signal'


def needs_quality_review(event):
    if event.get('needs_repair') or event.get('quality_flags'):
        return True
    if event.get('analysis_status') in {'fallback', 'failed'}:
        return True
    if content_type(event) == 'industry_report':
        access = event.get('report_access_level') or event.get('access_level')
        if access in {'paid', 'restricted'} and not (
            event.get('source_excerpt') or event.get('interpretation_basis')
        ):
            return True
    if not has_explainable_analysis(event):
        return True
    return False


def classify_bd_priority(event, score=None):
    """Return BD priority using source-aware signal strength.

    Low-score events should not become "high" only because a title parser
    guessed funding or MA. Google News company-monitor items are especially
    conservative because they are a fallback source.
    """
    s = event_score(event) if score is None else score
    ev_type = event_type(event)
    is_google = is_google_news_event(event)
    is_company = bool(event.get('is_company'))
    source_tier = event.get('source_tier') or ''

    if not is_scope_qualified(event):
        return '观察'

    if ev_type == 'funding' and not is_actionable_funding_event(event):
        return '观察'

    typed = content_type(event)
    if typed:
        attention = signal_score(event, 'attention_score')
        confidence = signal_score(event, 'confidence_score')
        if needs_quality_review(event):
            return '观察'
        if attention >= 70 and confidence >= 65:
            return '高'
        if attention >= 50 and confidence >= 50:
            return '中'
        return '观察'

    if ev_type == 'other':
        return '中' if is_company and source_tier == 'L1 官方/IR源' and s >= 40 else '观察'

    high_threshold = 70
    if source_tier == 'L1 官方/IR源':
        high_threshold = 50
    elif is_google:
        high_threshold = 70

    if is_google and is_low_signal_google_news(event):
        return '观察'
    if s >= high_threshold:
        return '高'
    if ev_type in HIGH_VALUE_SIGNAL_TYPES and s >= 50 and not is_google:
        return '高'
    if ev_type in STRONG_EVENT_TYPES and (s >= 40 or is_company):
        return '中'
    return '观察'


def follow_up_window_for_priority(priority):
    if priority == '高':
        return '7天内'
    if priority == '中':
        return '30天内'
    return '持续观察'


def is_high_value_event(event):
    typed = content_type(event)
    typed_value = (
        typed in FIRST_CLASS_CONTENT_TYPES
        and signal_score(event, 'attention_score') >= 70
        and signal_score(event, 'confidence_score') >= 65
    )
    return (
        is_scope_qualified(event)
        and
        classify_bd_priority(event) == '高'
        and is_mainline_internet_event(event)
        and not needs_quality_review(event)
        and (event_type(event) in STRONG_EVENT_TYPES or typed_value)
        and is_actionable_funding_event(event)
    )


def is_company_quality_signal(event):
    if not event.get('is_company'):
        return False
    if not is_scope_qualified(event):
        return False
    if not is_mainline_internet_event(event):
        return False
    if event_type(event) == 'other':
        return False
    if needs_quality_review(event):
        return False
    if is_google_news_event(event) and is_low_signal_google_news(event):
        return False
    if (event.get('source_tier') or '') == 'L1 官方/IR源':
        return event_score(event) >= 2
    return event_score(event) >= 3


def should_show_in_main_list(event):
    if not is_scope_qualified(event):
        return False
    if not is_mainline_internet_event(event):
        return False
    if needs_quality_review(event):
        return False
    if is_high_value_event(event):
        return True
    if event_type(event) == 'funding' and not is_actionable_funding_event(event):
        return False
    if content_type(event):
        return (
            not is_google_news_event(event)
            and signal_score(event, 'attention_score') >= 50
            and signal_score(event, 'confidence_score') >= 50
        )
    return (
        event_type(event) in STRONG_EVENT_TYPES
        and not is_google_news_event(event)
        and event_score(event) >= 2
    )


def should_show_in_review(event):
    if should_show_in_main_list(event):
        return False
    if not is_scope_qualified(event):
        return False
    if internet_relevance_score(event) < 2:
        return False
    ev_type = event_type(event)
    s = event_score(event)
    if content_type(event):
        return (
            signal_score(event, 'attention_score') >= 40
            and signal_score(event, 'confidence_score') >= 45
            and not needs_quality_review(event)
        )
    if is_google_news_event(event):
        return (
            ev_type in STRONG_EVENT_TYPES
            and s >= 5
            and not is_low_signal_google_news(event)
            and not needs_quality_review(event)
        )
    return ev_type in STRONG_EVENT_TYPES and (s >= 3 or classify_bd_priority(event) == '中')
