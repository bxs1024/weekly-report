"""事件判型与身份识别：事件类型、区域、公司主体键、相似度与指纹。

这一层把「一条事件」变成可比较、可归并的结构化身份：类型/区域/主体/指纹。
去重、过滤、展示、归组都以这里的判断为准，因此它是 content 层的核心。
"""

import re
from functools import lru_cache

try:
    from constants import (
        BANK_PROTECTED_FINTECH, CHINESE_OUTBOUND_PATTERNS, COMPANY_ALIASES,
        COMPANY_LOW_SIGNAL_PATTERNS, EVENT_ENTITY_STOPWORDS, TRADITIONAL_BANKS,
    )
    from content.util import (
        _normalize_text, _parse_iso_date, _strip_title_source, _title_tokens,
    )
except ImportError:
    from scripts.constants import (
        BANK_PROTECTED_FINTECH, CHINESE_OUTBOUND_PATTERNS, COMPANY_ALIASES,
        COMPANY_LOW_SIGNAL_PATTERNS, EVENT_ENTITY_STOPWORDS, TRADITIONAL_BANKS,
    )
    from scripts.content.util import (
        _normalize_text, _parse_iso_date, _strip_title_source, _title_tokens,
    )


def detect_event_types(title):
    t = title.lower()
    types = []
    # 融资（最高优先）
    if any(k in t for k in ['raises', 'secures $', 'closes $', 'raises £',
                       'closes funding', 'series ', 'seed round', 'valued at', 'unicorn',
                       'pre-series', 'investment of $', 'received $', 'attracts $',
                       'ltd raises', 'funding of', 'funding to',
                       # 融资金额直接出现
                       '$50m', '$100m', '$200m', '$500m', '$1b', '$1b+', 'bags $',
                       # 融资进展
                       'funding round', 'raises in ', 'closes $', 'm series',
                       'attracts gulf',  # WAMDA 常见格式
                       # 估值相关
                       'valuation', 'valued at', 'eyes $', '$b valuation',
                       # 日文融资信号（日本创投媒体：The Bridge 等）
                       '調達', 'シード', 'シリーズ', '出資', 'ラウンド',
                       '億円', '億ドル', 'ファンド', '融資']):
        types.append('funding')
    # 并购/收购
    if any(k in t for k in ['acquires', 'acquired', 'acquisition', 'merger', 'merges',
                       'takeover', 'takes control', 'stake in', 'buys', 'purchases',
                       'buyout', 'sold to',
                       # 日文并购
                       '買収', '合併', '過半数', '公開買付']):
        types.append('ma')
    # 财报/IPO
    if any(k in t for k in ['revenue', 'earnings', 'profit', 'quarterly results',
                       'fiscal year', 'ipo ', 'listing', 'goes public',
                       'files to go public', 'quarterly profit', 'quarterly loss',
                       'q1 ', 'q2 ', 'q3 ', 'q4 ', 'financial results',
                       'goes live', 'stock ',
                       # 日文财报/上市
                       '決算', '上場', 'IPO', '営業利益', '増収', '減益',
                       '純利益', '黒字', '赤字', '四半期']):
        types.append('earnings')
    # 精品研报/行业数据：先单独标记，避免被普通 strategy 吞掉。
    is_report = any(k in t for k in [
        'report', 'forecast', 'market size', 'market share', 'market outlook',
        'market map', 'benchmark', 'ranking', 'rankings', 'consumer spend',
        'consumer spending', 'monthly active users', 'subscribers', 'gmv',
        'gross merchandise', 'payment volume', 'gaming market',
        'mobile games market', 'games market', '行业报告', '市场预测',
        '市场规模', '市场份额', '基准测试',
        # 日文行业数据
        '調査', 'レポート', 'ランキング', '市場規模', '市場シェア',
        '予測', '導入率', 'アンケート',
    ])
    # 排除"据报道"语境：report/reported/reporter 或 "X: Report" 结尾是媒体报道/记者手记，
    # 不是行业研报本体（如 "Cursor...: Report"、"Reporter's Notebook"、"Nvidia reportedly..."）。
    reported_ctx = bool(re.search(r'\b(reportedly|reported|reporter)\b', t)) \
        or bool(re.search(r'(:\s*report|\|\s*report|-\s*report)\b', t))
    if is_report and reported_ctx:
        is_report = False
    if is_report:
        types.append('industry_report')

    # AI 模型发布：事实进入日报，性能结论另存 claim_type。
    is_model_release = (
        any(k in t for k in [
            'foundation model', 'language model', 'large language model',
            'multimodal model', 'ai model', 'open-source model',
            'open source model', '模型发布', '大模型', '多模态模型', '开源模型',
            'aiモデル', '言語モデル', 'マルチモーダル',
        ])
        and any(k in t for k in [
            'launch', 'launches', 'launched', 'release', 'released', 'unveils',
            'available', '推出', '发布', '上线', '开放',
            '発表', 'リリース', '公開', 'ローンチ',
        ])
    )
    if is_model_release:
        types.append('model_release')

    # 战略/市场（出海、全球化、产品发布）
    if any(k in t for k in ['partners with', 'partnership', 'strategic',
                       'joint venture', 'expands to', 'flagship store',
                       'exits ', 'layoffs', 'shutdown', 'spins off',
                       'disrupts', 'CEO says', 'CEO on', 'ceo on', 'expansion',
                       'launches ', 'rolls out', 'deploys', 'to launch',
                       'launches in', 'listing ', 'eyes $', '$ valuation',
                       # 出海/国际化关键词（扩充）
                       'overseas', 'offshore', 'abroad', 'foreign market',
                       'international', 'global launch', 'global push', 'global ambition',
                       'enter', 'enters', 'entering', 'to expand', 'expanding',
                       'global expansion', 'international expansion',
                       'digital hub', 'digital status', 'digital economy',
                       'tech hub', 'tech investment', 'AI investment',
                       # 产品/市场动作（扩充）
                       'debut', 'debuts', 'debuting', 'launch', 'launched',
                       'available in', 'rollout', 'available internationally',
                       'files for IPO', 'goes public', 'listing',
                       'turnaround', 'restructure', 'reorganization',
                       'cloud service', 'cloud expansion', 'data center',
                       'partners with', 'signs MOU', 'joint venture',
                        # 垂直赛道报告词已单独归类，这里保留其余市场动作词
                        'report', 'forecast', 'market size', 'market share',
                       'ranking', 'rankings', 'benchmark', 'consumer spend',
                       'consumer spending', 'downloads', 'monthly active users',
                       'subscribers', 'gmv', 'gross merchandise', 'payment volume',
                        'digital payments', 'mobile wallet', 'social commerce',
                        'gaming market', 'mobile games market', 'games market',
                        # 日文战略/市场动作
                        '提携', 'パートナー', '進出', '撤退', '上場申請',
                        '提供開始', '新規事業', '販売開始', '事業拡大', '参入']):
        if not is_report and not is_model_release:
            types.append('strategy')
    return types if types else ['other']

SIGNAL_TAXONOMY = {
    'expansion': ['expands', 'expansion', 'launches in', 'enters', 'new market', 'country', 'localization', 'regional'],
    'partnership': ['partner', 'partnership', 'collaboration', 'alliance', 'mou', 'co-chair', 'joint'],
    'payment': ['payment', 'payments', 'wallet', 'bnpl', 'remittance', 'acquiring', 'checkout', 'card', 'fintech'],
    'commerce': ['commerce', 'ecommerce', 'e-commerce', 'marketplace', 'seller', 'merchant', 'logistics', 'fulfillment'],
    'ai_infra': ['ai', 'agent', 'model', 'inference', 'gpu', 'cloud', 'data center', 'datacenter', 'compute'],
    'developer_change': ['api', 'sdk', 'developer', 'changelog', 'release notes', 'platform update'],
    'capital': ['funding', 'raises', 'raised', 'series ', 'acquires', 'acquisition', 'ipo', 'earnings', 'revenue', 'profit', 'valuation'],
    'org_change': ['hiring', 'jobs', 'layoffs', 'appoints', 'ceo', 'executive', 'head of'],
    'compliance': ['license', 'regulation', 'regulatory', 'compliance', 'approval', 'antitrust'],
}

def infer_signal_taxonomy(item):
    text = ' '.join([
        item.get('title', ''),
        item.get('summary_short', ''),
        item.get('reason', ''),
        ' '.join(item.get('signal_types') or []),
        ' '.join(item.get('source_signal_types') or []),
        ' '.join(item.get('event_types') or []),
        item.get('source_role', ''),
        item.get('source_type', ''),
    ]).lower()
    signals = []
    for signal, keywords in SIGNAL_TAXONOMY.items():
        if any(keyword in text for keyword in keywords):
            signals.append(signal)
    ev_type = (item.get('event_types') or ['other'])[0]
    if ev_type in {'funding', 'ma', 'earnings'} and 'capital' not in signals:
        signals.append('capital')
    return signals or ['general']

REGION_TITLE_KEYWORDS = [
    ('亚太', [
        'india', 'indian', 'vietnam', 'vietnamese', 'singapore', 'malaysia',
        'indonesia', 'philippines', 'thailand', 'japan', 'japanese', 'korea',
        'korean', 'australia', 'australian', 'hong kong', 'taiwan',
        '印度', '越南', '新加坡', '马来西亚', '印尼', '菲律宾', '泰国', '日本', '韩国', '澳大利亚', '香港', '台湾',
    ]),
    ('非洲', [
        'africa', 'african', 'south africa', 'kenya', 'kenyan', 'nigeria',
        'nigerian', 'egypt', 'egyptian', 'ghana', 'morocco',
        '非洲', '南非', '肯尼亚', '尼日利亚', '埃及', '加纳', '摩洛哥',
    ]),
    ('拉美', [
        'latin america', 'latam', 'brazil', 'brazilian', 'mexico', 'mexican',
        'colombia', 'colombian', 'argentina', 'argentine', 'chile', 'chilean',
        '拉美', '巴西', '墨西哥', '哥伦比亚', '阿根廷', '智利',
    ]),
    ('中东', [
        'middle east', 'mena', 'uae', 'dubai', 'saudi', 'riyadh', 'kuwait',
        'qatar', 'turkey', 'turkish',
        '中东', '阿联酋', '迪拜', '沙特', '科威特', '卡塔尔', '土耳其',
    ]),
    ('欧洲', [
        'europe', 'european', 'uk ', 'britain', 'british', 'germany', 'german',
        'france', 'french', 'spain', 'spanish', 'italy', 'italian', 'finland',
        'finnish', 'denmark', 'danish', 'sweden', 'swedish', 'norway',
        '欧洲', '英国', '德国', '法国', '西班牙', '意大利', '芬兰', '丹麦', '瑞典', '挪威',
    ]),
    # 注意：不用裸 "us"（子串会误伤 focus/campus/status/august 等），"us" 由 infer_event_region 按单词边界匹配
    ('北美', [
        'north america', 'united states', 'u.s.', 'us', 'usa', 'american', 'america',
        'canada', 'canadian', 'silicon valley',
        '美国', '北美', '加拿大', '硅谷',
    ]),
]

def infer_event_region(title, fallback):
    text = f' {(title or "").lower()} '
    for region, keywords in REGION_TITLE_KEYWORDS:
        for keyword in keywords:
            kw = keyword.lower()
            # ASCII 短缩写关键词（"us"/"uae"）按单词边界匹配，避免子串误伤 focus/campus/status 等；
            # "uk " 这类带尾空格的和中文关键词保持子串匹配（中文连排字符间无词边界）
            if len(kw) <= 3 and not kw.endswith(' ') and kw.isascii():
                if re.search(rf'\b{re.escape(kw)}\b', text):
                    return region
            elif kw in text:
                return region
    return fallback or '未知'

# 中美公司关键词（匹配标题中出现的公司名，排除不相关内容）
# 用非贪婪匹配 + 上下文判断，避免误杀（如 "DeepMind raises" 才排除，纯叙述不排除）
BLACKLIST_COMPANIES = [
    # 美国公司/产品
    'OpenAI', 'Anthropic', 'xAI', 'x.AI', 'SpaceX', 'Starlink', 'Palantir',
    'ChatGPT', 'GPT-4', 'GPT-5', 'Claude ', 'Perplexity', 'Character.AI',
    'Waymo', 'Cruise',  # 自动驾驶（美）
    # 中国公司/产品
    'ByteDance', 'TikTok', 'Douyin', 'DeepSeek', 'Kimi', 'Qwen',
    # AI 产品名
    'Gemini ', 'Gemini,', 'Gemini.', 'Gemini/',  # Google AI 产品
]

BLACKLIST_PATTERNS = [re.compile(r'\b' + re.escape(c) + r'\b', re.IGNORECASE) for c in BLACKLIST_COMPANIES]

def is_blacklisted(title, official=False):
    # 官方源豁免：黑名单用于压制媒体对非监控中美公司的噪声，
    # 监控对象自己的官方披露标题含公司名（如 OpenAI/Anthropic）不得被误杀。
    if official:
        return False
    t = title
    for pat in BLACKLIST_PATTERNS:
        if pat.search(t):
            return True
    # 域名黑名单（URL 中出现这些域名也算排除）
    for dom in ['openai.com', 'anthropic.com', 'x.ai', 'spacex.com', 'byteDance.com',
                'tiktok.com', 'deepmind.google', 'waymo.com']:
        if dom in t.lower():
            return True
    return False

def _normalize_event_subject(subject):
    tokens = []
    for token in _normalize_text(subject).split():
        if token in EVENT_ENTITY_STOPWORDS:
            continue
        if len(token) <= 1:
            continue
        tokens.append(token)
    return ' '.join(tokens[:4])

def _title_subject_key(title):
    clean = _strip_title_source(title or '').strip()
    clean = re.sub(r'^[^A-Za-z0-9\u4e00-\u9fff]{0,3}(?:[^:]{2,36}:\s*)', '', clean)
    patterns = [
        r'\b([A-Z][A-Za-z0-9\.\-]{2,})\s+(?:raises?|raised|secures?|secured|closes?|closed)\b',
        r'\b([A-Z][A-Za-z0-9\.\-]{2,})\s+(?:doubles?|doubled|hits?|hit|reaches?|reached|is\s+valued|was\s+valued|valued)\b',
        r'^([A-Z][A-Za-z0-9\s&\.,\'\-\u2019]+?)\s+(?:raises?|raised|secures?|secured|closes?|closed|lands?|landed|bags?|bagged|gets?|got|receives?|received|attracts?|attracted|wins?|won)\b',
        r'^([A-Z][A-Za-z0-9\s&\.,\'\-\u2019]+?)\s+(?:doubles?|doubled|hits?|hit|reaches?|reached|is\s+valued|was\s+valued|valued)\b',
        r'^([A-Z][A-Za-z0-9\s&\.,\'\-\u2019]+?)\s+(?:acquires?|acquired|buys?|bought|purchases?|purchased|merges?|merged)\b',
        r'^([A-Z][A-Za-z0-9\s&\.,\'\-\u2019]+?)\s+(?:announces?|announced|reports?|reported|posts?|posted|files?|filed|plans?|planned|launches?|launched|expands?|expanded|partners?|partnered)\b',
    ]
    for pattern in patterns:
        match = re.search(pattern, clean, re.I)
        if not match:
            continue
        subject = match.group(1).strip().strip(',;:-')
        subject = re.sub(r'^(?:why|how|what|when|where|inside|after)\s+', '', subject, flags=re.I)
        if 2 <= len(subject) <= 60:
            key = _normalize_event_subject(subject)
            if key:
                return key
    return ''

def _event_subject_key(item):
    company = item.get('company_name') or ''
    if company:
        key = _normalize_event_subject(company)
        if key:
            return key
    companies = item.get('companies') or []
    if isinstance(companies, list) and companies:
        key = _normalize_event_subject(str(companies[0]))
        if key:
            return key
    return _title_subject_key(item.get('title', ''))

_FINANCIAL_NEGATIVE_WORDS = (
    'down', 'drop', 'falls', 'fall', 'fell', 'loss', 'losses', 'miss',
    'misses', 'decline', 'declines', 'plunge', 'plunges', 'slump', '下滑', '下降', '亏损',
)

def _has_negative_financial_word(title):
    t = (title or '').lower()
    return any(w in t for w in _FINANCIAL_NEGATIVE_WORDS)

def _financial_direction_consistent(a, b):
    """财报方向一致才判同：一条「利润创新高」一条「净利大跌」方向相反，是不同事件。"""
    return _has_negative_financial_word(a.get('title', '')) == _has_negative_financial_word(b.get('title', ''))

def _get_company_aliases(cfg_or_name):
    name = cfg_or_name if isinstance(cfg_or_name, str) else cfg_or_name.get('name', '')
    aliases = list(COMPANY_ALIASES.get(name, []))
    if name and name not in aliases:
        aliases.append(name)
    return aliases

def _title_mentions_aliases(title, aliases):
    norm_title = ' ' + _normalize_text(title) + ' '
    for alias in aliases:
        alias_norm = _normalize_text(alias)
        if not alias_norm:
            continue
        if f' {alias_norm} ' in norm_title:
            return True
        if alias_norm.replace(' ', '') and alias_norm.replace(' ', '') in norm_title.replace(' ', ''):
            return True
    return False

def _title_mentions_company(title, cfg):
    """
    Google News 查询会放大相关词，这里要求标题至少命中一个公司别名，
    防止把行业新闻误记到监控公司名下。
    """
    return _title_mentions_aliases(title, _get_company_aliases(cfg))

def _is_low_signal_company_title(title):
    title_lower = title.lower()
    return any(pattern in title_lower for pattern in COMPANY_LOW_SIGNAL_PATTERNS)

def _is_traditional_bank_item(item):
    """Fintech 源会带回传统商业银行事件（财报/贷款/资产出售），
    这些机构主体不是互联网/科技公司，不属于情报站定位，排除。
    数字银行/金融科技公司（名字含 bank 但属于科技）优先豁免。"""
    if item.get('is_company') or _is_official_company_source(item):
        return False
    text = ' '.join([
        item.get('title', ''),
        item.get('company_name', ''),
        item.get('publisher', ''),
    ]).lower()
    if any(p.lower() in text for p in BANK_PROTECTED_FINTECH):
        return False
    return any(b.lower() in text for b in TRADITIONAL_BANKS)

def _is_chinese_outbound_title(title):
    title_lower = (title or '').lower()
    return any(pattern in title_lower for pattern in CHINESE_OUTBOUND_PATTERNS)

def _is_official_company_source(item):
    return item.get('source_tier') == 'L1 官方/IR源' or item.get('source_role') == 'official_ir'

def _is_vertical_source(item):
    return item.get('source_role') == 'industry_vertical' or item.get('source_tier') == 'L4 垂直赛道精品源'

def _is_high_signal_vertical_title(title):
    t = (title or '').lower()
    keywords = [
        'report', 'market', 'forecast', 'ranking', 'rankings', 'top ', 'top-', 'trend',
        'trends', 'benchmark', 'data', 'revenue', 'spend', 'spending', 'downloads',
        'users', 'subscribers', 'gmv', 'payment', 'payments', 'wallet', 'license',
        'regulation', 'regulatory', 'launches', 'expands', 'partners', 'partnership',
        'acquires', 'acquisition', 'merger', 'raises', 'funding', 'investment',
        'gaming market', 'mobile games', 'ecommerce', 'e-commerce', 'fintech',
        'digital commerce', 'social commerce', 'super app',
    ]
    return any(k in t for k in keywords)

def _event_similarity(a, b):
    ta = set(_title_tokens(a.get('title', '')))
    tb = set(_title_tokens(b.get('title', '')))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)

# 公司名后缀归一：Sea Limited → sea、Square Enix Holdings → square enix
_COMPANY_KEY_SUFFIXES = (
    'inc', 'incorporated', 'limited', 'ltd', 'corporation', 'corp',
    'holdings', 'technologies', 'technology', 'plc', 'ag',
)

# 与常见英文词冲突的公司别名：子串/词边界匹配会把 "credit line"、"SeABank"、
# "to grab share" 等普通词误判为公司，禁止用它们做别名对齐（公司名来源不受影响）。
_GENERIC_ALIAS_TOKENS = {
    'line', 'sea', 'noon', 'grab', 'stc', 'jd', 'mo', 'tab', 'tabby', 'allegro',
}

# 事实性事件类型：同一实体同日只可能有一件，直接以实体键合并。
# strategy/industry_report 等允许一实体一日多事，仍走标题相似度，避免误并。
_SINGULAR_EVENT_TYPES = {'funding', 'ma', 'earnings'}

_EVENT_TYPE_PRIORITY = (
    'funding', 'ma', 'earnings', 'industry_report', 'model_release',
    'regional_policy', 'strategy', 'other',
)

def _normalize_company_key(name):
    """公司实体键：去公司后缀、归一为小写词序列，用于跨报道对齐。"""
    if not name:
        return ''
    norm = _normalize_text(str(name))
    if not norm:
        return ''
    tokens = norm.split()
    while tokens and tokens[-1] in _COMPANY_KEY_SUFFIXES:
        tokens.pop()
    if not tokens:
        tokens = norm.split()[:1]
    return ' '.join(tokens[:4])

def _entity_key_info(item):
    """
    提取事件实体键，返回 (entity_key, source)。
    source 标识键的可靠度：
      'company' — company_name 权威（监控公司）
      'alias'   — 标题命中已知公司别名（补 company_name 缺失的缺口，如 Jumia 融资第二条）
      'title'   — 标题动词提取（弱信号，合并时需相似度防误并）

    性能：本函数只依赖 (company_name, title)，是纯函数；但别名遍历要做
    上百次 re.search，而展示层去重是 O(n²) 调用，实测 3796 条事件时
    单点耗时累积到 40 分钟以上。因此内部按 (company_name, title) 缓存。
    """
    return _entity_key_info_cached(item.get('company_name') or '', item.get('title') or '')

@lru_cache(maxsize=200000)
def _entity_key_info_cached(company_name, title):
    item = {'company_name': company_name, 'title': title}
    company = item.get('company_name') or ''
    key = _normalize_company_key(company)
    if key:
        return key, 'company'
    title_lower = (item.get('title') or '').lower()
    best_alias = ''
    for aliases in COMPANY_ALIASES.values():
        for alias in aliases:
            a = str(alias).lower()
            if len(a) < 3 or a in _GENERIC_ALIAS_TOKENS:
                continue
            if len(a) > len(best_alias) and re.search(r'\b' + re.escape(a) + r'\b', title_lower):
                best_alias = a
    if best_alias:
        return _normalize_company_key(best_alias), 'alias'
    subj = _event_subject_key(item)
    if subj:
        return subj, 'title'
    return '', 'none'

def _primary_event_type(item):
    """事件类型主键：多类型归一为优先级最高的那个（Jumia funding+earnings → funding）。"""
    types = item.get('event_types') or ['other']
    for t in _EVENT_TYPE_PRIORITY:
        if t in types:
            return t
    return types[0] if types else 'other'

# 事件锚点词：同公司同日不同文章是否指向同一事件（财报/融资/并购）
_FINANCIAL_ANCHOR_WORDS = (
    'revenue', 'earnings', 'profit', 'quarter', 'quarterly', 'result',
    'financial', 'fiscal', 'income', 'operating', 'net income',
    '财报', '营收', '净利', '净亏', '決算', '営業利益', '純利益', '増収', '減益',
)

_FUNDING_SIGNAL_WORDS = (
    'raise', 'raises', 'raised', 'funding', 'seed', 'valuation', 'valued',
    'investment', 'secures', 'secured', 'closes', 'closed', 'bags', 'landed',
    '$', '€', '£', 'series ', 'unicorn', '融资', '調達', '出資', '億円',
    'ipo', 'listing', 'filing', 'registration', '上市', '上場',
)

_MA_SIGNAL_WORDS = (
    'acquire', 'acquires', 'acquired', 'acquisition', 'merger', 'merges', 'merging',
    'merge', 'buy', 'buys', 'buying', 'purchase', 'takeover',
    'deal', 'deals', 'deal to', 'agreement', 'bid', '收购', '并购', '買収', '合併',
)

def _has_financial_anchor(title):
    t = (title or '').lower()
    if re.search(r'\bq[1-4]\b', t):
        return True
    return any(w in t for w in _FINANCIAL_ANCHOR_WORDS)

def _has_funding_signal(title):
    t = (title or '').lower()
    if re.search(r'\bseries\s+[a-e]\b', t):
        return True
    return any(w in t for w in _FUNDING_SIGNAL_WORDS)

def _has_ma_signal(title):
    t = (title or '').lower()
    return any(w in t for w in _MA_SIGNAL_WORDS)

def _dates_adjacent(a, b, window_days=3):
    date_a = (a.get('article_date') or a.get('date') or '')[:10]
    date_b = (b.get('article_date') or b.get('date') or '')[:10]
    if not date_a or not date_b:
        return True
    parsed_a = _parse_iso_date(date_a)
    parsed_b = _parse_iso_date(date_b)
    if parsed_a is None or parsed_b is None:
        return False
    return abs((parsed_a - parsed_b).days) <= window_days

def _normalize_canonical_key(value):
    """归一化事件量化锚点：金额统一成 数字+m 格式（$250M / $250 Million / 2.5亿美元 → 250m）；
    非金额（公司名/人数/百分比）小写去标点。空或无识别内容返回空串（指纹路径不触发）。"""
    if not value:
        return ''
    v = str(value).strip()
    if not v:
        return ''
    m = re.search(r'([\d.]+)\s*(bn|billion|b|mn|million|m|k|亿|万)', v.lower())
    if m:
        try:
            num = float(m.group(1))
        except ValueError:
            return ''
        unit = m.group(2)
        if unit in ('bn', 'billion', 'b'):
            num *= 1000
        elif unit == '亿':
            num *= 100
        elif unit == '万':
            num *= 0.01
        elif unit == 'k':
            num *= 0.001
        if abs(num - round(num)) < 1e-9:
            return f'{int(round(num))}m'
        return f'{num:.2f}m'.rstrip('0').rstrip('.') + 'm'
    return re.sub(r'[^a-z0-9%一-鿿]+', '', v.lower())

def _fingerprint_match(a, b):
    """AI 指纹合并判定：canonical_company + canonical_key 全匹配视为同一事件。
    类型漂移仲裁：AI 对同一件事前后两班可能判出不同主类型（实测同一增持事件
    funding/strategy 来回漂），此时用标题相似度（≥0.42，与旧规则 strategy 守卫
    同档）确认是同一件事的不同报道；不像则不敢单凭指纹判同，返回 None 交回旧规则。
    锚点缺失放宽：无量化锚点的事件（股价异动、合作等）canonical_key 常为空，
    原逻辑直接不判导致漏并（PayPal 股价案）；改为公司主体相同 + 主类型相同 +
    标题相似度达标也判同。任一项缺失（存量事件无指纹）返回 None；
    锚点明确不同返回 False（不同事件）。"""
    ca = a.get('canonical_company') or ''
    cb = b.get('canonical_company') or ''
    if not ca or not cb:
        return None
    if _normalize_company_key(ca) != _normalize_company_key(cb):
        return None
    ka = _normalize_canonical_key(a.get('canonical_key') or '')
    kb = _normalize_canonical_key(b.get('canonical_key') or '')
    if not ka or not kb:
        # 无锚点：主体+类型+相似度三重确认，防同公司不同事件误并
        sim = _event_similarity(a, b)
        if _primary_event_type(a) == _primary_event_type(b) and sim >= 0.42:
            return True
        return None
    if ka != kb:
        return False
    if _primary_event_type(a) == _primary_event_type(b):
        return True
    if _event_similarity(a, b) >= 0.42:
        return True
    return None

_VALID_EVENT_TYPES = {
    'funding', 'ma', 'earnings', 'strategy', 'industry_report',
    'model_release', 'regional_policy', 'other',
}

def _ai_event_types(analysis_types, fallback_types):
    """AI 判定的事件类型：合法单值才采用，否则用采集侧类型兜底（防模型幻觉输出垃圾）。"""
    t = analysis_types
    if isinstance(t, str):
        t = [t]
    if isinstance(t, list) and t and all(x in _VALID_EVENT_TYPES for x in t):
        return t
    return fallback_types or ['other']
