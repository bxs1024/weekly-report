"""Product-boundary rules for a global internet industry intelligence site.

This is not a source-quality filter. It answers whether an event belongs to
the site's identity: internet platforms, digital infrastructure, and software-
driven business opportunities.
"""

import re
from functools import lru_cache


CORE_INTERNET_TERMS = {
    'ecommerce', 'e-commerce', 'marketplace', 'merchant', 'seller',
    'payment', 'payments', 'fintech', 'wallet', 'bnpl', 'remittance',
    'acquiring', 'banking app', 'digital bank', 'checkout',
    'saas', 'software-as-a-service', 'enterprise software', 'crm', 'erp',
    'cloud', 'ai', 'ai infrastructure', 'ai infra', 'data center', 'datacenter',
    'gpu', 'openai', 'anthropic', 'nvidia', 'developer', 'api', 'changelog', 'serverless', 'database',
    'cybersecurity', 'security platform', 'identity', 'ads', 'advertising',
    'adtech', 'marketing automation', 'social media', 'creator platform',
    'gaming', 'game', 'games', 'streaming', 'app store', 'super app',
    'ride-hailing', 'mobility platform', 'delivery platform', 'food delivery',
    'logistics platform', 'fulfillment', 'last-mile',
    'semiconductor', 'chip', 'chips', 'foundry', 'quantum', 'quantum computing',
    'telecom', 'telecommunications', '5g', 'network equipment', 'smartphone',
    'robotaxi', 'autonomous driving', 'self-driving', 'pc',
    '电商', '支付', '金融科技', '钱包', '跨境汇款', '商户', '收单',
    '云', '云服务', '数据中心', '算力', '开发者', '接口', '数据库',
    '网络安全', '身份认证', '广告', '营销自动化', '游戏', '流媒体',
    '超级app', '本地生活', '外卖', '出行平台', '物流平台', '履约',
    '半导体', '芯片', '晶圆', '代工', '量子', '电信', '通信', '5g',
    '手机', '自动驾驶', '机器人出租车', 'pc', '个人电脑',
}

ADJACENT_INTERNET_TERMS = {
    'platform', 'software', 'app', 'digital', 'data', 'analytics', 'automation',
    'ai platform', 'ai app', 'ai application', 'model', 'llm', 'agent', 'inference', 'workflow', 'notetaker',
    'ehr', 'emr', 'telehealth', 'health it', 'medical it', 'patient support',
    'clinical data', 'medical data', 'digital health', 'healthcare saas',
    '平台', '软件', '应用', '数字化', '数据', '分析', '自动化',
    'ai平台', 'ai应用', '模型', '智能体', '推理', '工作流', '医疗it', '医疗数据',
    '电子病历', '远程医疗', '数字医疗', '医疗saas', '患者支持',
}

STRONG_ADJACENT_HEALTH_IT_TERMS = {
    'ehr', 'emr', 'telehealth', 'health it', 'medical it', 'patient support',
    'clinical data platform', 'medical data platform', 'healthcare saas',
    'ai notetaker', 'notetaker', 'revenue cycle management',
    '电子病历', '远程医疗', '医疗it', '医疗数据平台', '医疗saas',
    '患者支持平台', 'ai病历', '收入周期管理',
}

EDGE_TERMS = {
    'industrial software', 'robotics', 'robot', 'energy software',
    'climate software', 'manufacturing software', 'supply chain software',
    '工业软件', '机器人', '能源软件', '制造软件', '供应链软件',
}

OUT_OF_SCOPE_TERMS = {
    'defense', 'defence', 'military', 'weapon', 'missile', 'ammunition',
    'army', 'battlefield', 'palantir-style',
    'biotech', 'biotherapeutics', 'therapeutics', 'pharma', 'pharmaceutical',
    'drug', 'drug discovery', 'therapy', 'therapies', 'clinical trial',
    'ophthalmology', 'glaucoma', 'retinal', 'cardiac', 'oncology', 'cancer',
    'diagnostics', 'disease diagnosis', 'healthcare fund', 'medical device',
    'ct scanner', 'agriculture', 'agritech', 'construction material', 'mining',
    '国防', '军工', '军事', '武器', '导弹', '弹药', '战场',
    '生物科技', '生物制药', '制药', '药物', '疗法', '治疗', '临床试验',
    '眼科', '青光眼', '视网膜', '心脏', '肿瘤', '癌症', '诊断',
    '医疗基金', '医疗器械', '农业', '建筑材料', '矿业',
}

HARD_OUT_OF_SCOPE_TERMS = {
    'defense', 'defence', 'military', 'weapon', 'missile', 'ammunition',
    'army', 'battlefield', '国防', '军工', '军事', '武器', '导弹', '弹药', '战场',
}

SPACE_OUT_OF_SCOPE_TERMS = {
    'space ipo', 'space company', 'space startup', 'spacetech', 'space tech',
    'satellite imagery', 'earth observation', 'geospatial intelligence',
    '航天', '卫星影像', '地球观测', '地理空间情报',
}

HEALTH_BIO_OUT_OF_SCOPE_TERMS = {
    'healthtech', 'health tech', 'healthcare', 'medical',
    'biotech', 'biotherapeutics', 'therapeutics', 'pharma', 'pharmaceutical',
    'drug', 'therapy', 'therapies', 'clinical trial', 'ophthalmology',
    'glaucoma', 'retinal', 'cardiac', 'oncology', 'cancer', 'diagnostics',
    'disease diagnosis', 'healthcare fund', 'medical device',
    'biomanufacturing', 'low-carbon construction material', 'construction material',
    'construction materials', 'agriculture', 'agritech', 'mining',
    '医疗科技', '医疗健康', '医疗行业', '生物科技', '生物制药', '制药', '药物', '疗法', '治疗', '临床试验',
    '眼科', '青光眼', '视网膜', '心脏', '肿瘤', '癌症', '诊断',
    '医疗基金', '医疗器械', '生物制造', '低碳建材', '建筑材料', '农业', '矿业',
}

HEALTH_PLATFORM_EXCEPTION_TERMS = {
    'openai', 'anthropic', 'nvidia', 'model', 'llm',
    'ai infrastructure', 'ai infra', 'cloud', 'gpu', 'data center', 'datacenter',
    'platform api',
    '模型', '云', '算力', '数据中心',
}

OUT_OF_SCOPE_CAP_TO_EDGE_TERMS = HARD_OUT_OF_SCOPE_TERMS


def _event_text(event):
    parts = [
        event.get('title') or '',
        event.get('display_title') or '',
        event.get('summary_short') or '',
        event.get('reason') or '',
        event.get('trend_topic') or '',
        event.get('source') or '',
        event.get('company_name') or '',
        ' '.join(event.get('companies') or []),
    ]
    return ' '.join(parts).lower()


def _event_fact_text(event):
    parts = [
        event.get('title') or '',
        event.get('display_title') or '',
        event.get('summary_short') or '',
        event.get('source') or '',
        event.get('company_name') or '',
        ' '.join(event.get('companies') or []),
    ]
    return ' '.join(parts).lower()


def _compile_term_matcher(terms):
    alnum = [term.lower() for term in terms if re.search(r'[a-z0-9]', term)]
    plain = [term.lower() for term in terms if not re.search(r'[a-z0-9]', term)]
    pattern = None
    if alnum:
        pattern = re.compile(
            r'(?<![a-z0-9])(?:' + '|'.join(re.escape(term) for term in alnum) + r')(?![a-z0-9])'
        )
    return pattern, tuple(plain)


_TERM_SETS = [
    CORE_INTERNET_TERMS,
    ADJACENT_INTERNET_TERMS,
    STRONG_ADJACENT_HEALTH_IT_TERMS,
    EDGE_TERMS,
    OUT_OF_SCOPE_TERMS,
    HARD_OUT_OF_SCOPE_TERMS,
    SPACE_OUT_OF_SCOPE_TERMS,
    HEALTH_BIO_OUT_OF_SCOPE_TERMS,
    HEALTH_PLATFORM_EXCEPTION_TERMS,
    OUT_OF_SCOPE_CAP_TO_EDGE_TERMS,
]

_COMPILED_MATCHERS = {id(terms): _compile_term_matcher(terms) for terms in _TERM_SETS}


def _contains_any(text, terms):
    matcher = _COMPILED_MATCHERS.get(id(terms))
    if matcher is None:
        matcher = _compile_term_matcher(terms)
    pattern, plain = matcher
    if pattern is not None and pattern.search(text):
        return True
    for term in plain:
        if term in text:
            return True
    return False


def assess_internet_relevance(event):
    """Return score/label/reason for the site's product boundary.

    score:
    - 3: core internet
    - 2: adjacent but clearly software/platform/infrastructure related
    - 1: edge observation only
    - 0: out of scope for the main internet intelligence product
    """
    text = _event_text(event)
    fact_text = _event_fact_text(event)
    has_core = _contains_any(text, CORE_INTERNET_TERMS)
    has_adjacent = _contains_any(text, ADJACENT_INTERNET_TERMS)
    has_fact_core = _contains_any(fact_text, CORE_INTERNET_TERMS)
    has_strong_health_it = _contains_any(fact_text, STRONG_ADJACENT_HEALTH_IT_TERMS)
    has_edge = _contains_any(text, EDGE_TERMS)
    has_out = _contains_any(text, OUT_OF_SCOPE_TERMS)
    has_health_bio_out = _contains_any(fact_text, HEALTH_BIO_OUT_OF_SCOPE_TERMS)
    capped_edge = _contains_any(fact_text, OUT_OF_SCOPE_CAP_TO_EDGE_TERMS)
    has_space_out = _contains_any(fact_text, SPACE_OUT_OF_SCOPE_TERMS)
    has_health_exception = (
        has_strong_health_it
        or (has_fact_core and _contains_any(fact_text, HEALTH_PLATFORM_EXCEPTION_TERMS))
    )

    if capped_edge:
        return {
            'score': 0,
            'label': 'out_of_scope',
            'reason': '军工/国防相关事件默认不属于本站主赛道',
        }

    if has_health_bio_out and not has_health_exception:
        return {
            'score': 0,
            'label': 'out_of_scope',
            'reason': '生物制药、疗法、诊断或医疗器械事件默认不属于本站主赛道',
        }

    if has_space_out and not has_fact_core:
        return {
            'score': 0,
            'label': 'out_of_scope',
            'reason': '纯航天、卫星影像或地理空间资本事件默认不属于本站主赛道',
        }

    if has_core:
        score = 3
        label = 'core_internet'
        reason = '核心互联网平台、软件或数字基础设施信号'
    elif has_adjacent:
        score = 2
        label = 'adjacent_internet'
        reason = '相邻行业事件，但明确指向软件、平台、数据或AI能力'
    elif has_edge:
        score = 1
        label = 'edge_observation'
        reason = '边缘产业数字化信号，只适合观察层'
    else:
        score = 0 if has_out else 1
        label = 'out_of_scope' if has_out else 'edge_observation'
        reason = '不属于本站主赛道' if has_out else '互联网相关性不足，先作为边缘观察'

    if has_out and score >= 2:
        if capped_edge:
            return {
                'score': 0,
                'label': 'out_of_scope',
                'reason': '军工/国防相关事件默认不属于本站主赛道',
            }
        if has_core:
            return {
                'score': 2,
                'label': 'adjacent_internet',
                'reason': '相邻行业事件，因包含互联网平台、软件或AI基础设施能力而保留',
            }
        if has_health_bio_out and not has_health_exception:
            return {
                'score': 0,
                'label': 'out_of_scope',
                'reason': '生物制药、疗法、诊断或医疗器械事件默认不属于本站主赛道',
            }
        if has_adjacent:
            return {
                'score': 2,
                'label': 'adjacent_internet',
                'reason': '相邻行业事件，因明确指向软件、数据或AI应用而保留',
            }

    return {'score': score, 'label': label, 'reason': reason}


def _relevance_key(event):
    """缓存键：assess_internet_relevance 只读这 8 个字段（见 _event_text/_event_fact_text）。"""
    return (
        event.get('title') or '',
        event.get('display_title') or '',
        event.get('summary_short') or '',
        event.get('reason') or '',
        event.get('trend_topic') or '',
        event.get('source') or '',
        event.get('company_name') or '',
        tuple(event.get('companies') or []),
    )


@lru_cache(maxsize=65536)
def _assess_cached(key):
    title, display_title, summary_short, reason, trend_topic, source, company_name, companies = key
    return assess_internet_relevance({
        'title': title,
        'display_title': display_title,
        'summary_short': summary_short,
        'reason': reason,
        'trend_topic': trend_topic,
        'source': source,
        'company_name': company_name,
        'companies': list(companies),
    })


def internet_relevance_score(event):
    """互联网相关度分值。

    性能：本函数是事件内容的纯函数，但被 period_themes 的窗口统计以
    「窗口 × 维度 × 事件」三重循环反复调用，实测单次全量生成命中约 100 万次，
    其中 assess_internet_relevance 内部要跑上千个词表正则。按内容字段缓存后
    相同事件只算一次。
    """
    return _assess_cached(_relevance_key(event))['score']


def is_mainline_internet_event(event):
    return internet_relevance_score(event) >= 2


def is_edge_internet_event(event):
    return internet_relevance_score(event) == 1
