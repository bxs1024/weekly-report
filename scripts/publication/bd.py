"""BD 上下文：信源分层、机会触发、主题抽取与业务字段补全。"""

import re

try:
    from event_value import classify_bd_priority, follow_up_window_for_priority
    from editorial.agents import OPPORTUNITY_BY_TRIGGER, OPPORTUNITY_BY_TYPE
    from publication.score import (
        CATEGORY_MAP, INSIGHT_LABEL_MAP, TRUNCATED_JUNK, _cn_today,
        _extract_title_publisher, _format_amount, _is_chinese_capital, _parse_amount,
    )
except ImportError:
    from scripts.event_value import classify_bd_priority, follow_up_window_for_priority
    from scripts.editorial.agents import OPPORTUNITY_BY_TRIGGER, OPPORTUNITY_BY_TYPE
    from scripts.publication.score import (
        CATEGORY_MAP, INSIGHT_LABEL_MAP, TRUNCATED_JUNK, _cn_today,
        _extract_title_publisher, _format_amount, _is_chinese_capital, _parse_amount,
    )

VERTICAL_DEAL_SOURCES = {
    'techcrunch', 'tech.eu', 'uktn', 'eu-startups', 'tech in asia', 'inc42',
    'wamda', 'menabytes', 'disrupt africa', 'ventureburn', 'latamlist', 'lavca',
}

REGIONAL_ECOSYSTEM_SOURCES = {
    'the recursive', 'the next web', 'techwire asia', 'techcabal',
    'techpoint', 'weetracker', 'contxto', 'dealstreetasia',
}

OFFICIAL_IR_SOURCE_HINTS = {
    'official', 'ir', 'investor', 'newsroom', 'press release',
    'rakuten group', 'grab holdings', 'mercado libre', 'sea limited',
}

BD_TRIGGER_RULES = [
    ('预算窗口', [
        'raises', 'raised', 'funding', 'investment', 'series ', 'seed', 'revenue',
        'earnings', 'profit', 'financial results', 'growth', 'margin', 'cash flow',
        '融资', '财报', '营收', '利润',
    ]),
    ('扩张窗口', [
        'launch', 'expands', 'expansion', 'enters', 'rolls out', 'available in',
        'international', 'overseas', 'global', 'new market', 'debut',
        '扩张', '出海', '上线', '进入',
    ]),
    ('降本窗口', [
        'layoff', 'cuts', 'cost', 'efficiency', 'automation', 'restructure',
        'turnaround', 'loss narrows', '亏损', '降本', '重组',
    ]),
    ('合规窗口', [
        'regulator', 'license', 'compliance', 'fine', 'lawsuit', 'probe',
        'antitrust', 'data protection', 'ban', '牌照', '监管', '合规',
    ]),
    ('整合窗口', [
        'acquires', 'acquisition', 'merger', 'stake', 'takeover', 'buys',
        'integration', '并购', '收购', '整合',
    ]),
    ('生态窗口', [
        'partner', 'partnership', 'alliance', 'ecosystem', 'platform',
        'merchant', 'developer', 'channel', 'mou', '合作', '生态',
    ]),
    ('竞争窗口', [
        'rival', 'competition', 'competes', 'market share', 'overtakes',
        'beats', 'challenges', 'versus', 'vs ', '竞争',
    ]),
]

SOURCE_ROLE_BY_TIER = {
    'L1 官方/IR源': 'official_ir',
    'L2 垂直交易源': 'venture_media',
    'L3 区域生态源': 'regional_ecosystem',
    'L4 深度趋势源': 'deep_trend',
    'L4 垂直赛道精品源': 'industry_vertical',
    'L5 Google News 补漏源': 'company_radar',
}

# 常见监控公司名（用于从标题提取当事人）
# 标题中包含这些词时直接用作 subject
KNOWN_COMPANIES = {
    'tabby', 'grab', 'gojek', 'noon', 'jumia', 'konga', 'trendyol',
    'rakuten', 'adyen', 'zalando', 'mercado', 'rappi', 'meesho',
    'swiggy', 'zomato', 'deliveroo', 'gorillas', 'getir',
    'ant group', 'alibaba', 'tencent', 'bytedance', 'tiktok',
    'jd.com', 'jd.com', 'kuaishou', 'shein', 'temu',
    'hktvmall', 'hong kong technology venture', 'u-next', 'square enix',
    'mercadoli', 'nubank', 'dlocal', 'paystack', 'flutterwave',
    'uber', 'lyft', 'grab', 'ola', 'bolt', 'inDrive',
    'flipkart', 'amazon', 'shopee', 'lazada',
    'stc pay', 'urpay', 'tala', 'chime', 'klarna', 'marqeta',
    'allegro', 'olx', 'letgo', '不成',
    'stord', 'openrouter', 'quantinuum',
}

# 中资出海关键词
CHINESE_OUTBOUND = {
    '字节', 'tiktok', 'bytedance', '抖音', 'temu', 'shein',
    '希音', '腾讯', 'tencent', '阿里', 'alibaba', '蚂蚁',
    'ant group', '京东', 'jd.com', '快手', 'kuaishou', '拼多多',
    '美团', 'meituan', '滴滴', 'didi', '百度', 'baidu',
}

def _extract_subject(title):
    """从标题提取当事人公司/产品名，优先级：已知公司 > 正则模式"""
    # 清理标题（去掉来源后缀）
    clean = re.sub(r'\s*[-|]\s*(Forbes|Reuters|TechCrunch|WIRED|BBC|CNBC|Bloomberg|Al Arabiya|cairoscene| african businessNewswire|Business Wire|PRNewswire|Euronews|Arab News).*$', '', title, flags=re.I)
    clean = clean.strip()

    # 策略1：已知名公司匹配（最优先）
    title_lower = clean.lower()
    for kw in sorted(KNOWN_COMPANIES, key=len, reverse=True):  # 长的先匹配
        if kw in title_lower:
            # 从标题中提取原始大小写版本
            idx = title_lower.find(kw)
            # 往回找到词边界（只吃字母不吃数字，避免 "000 MercadoLibre"）
            start = max(0, idx - 1)
            while start > 0 and title[start-1].isalpha():
                start -= 1
            # 往后取词
            end = idx + len(kw)
            while end < len(title) and title[end].isalnum():
                end += 1
            name = title[start:end].strip().rstrip(' -').strip()
            if len(name) >= 2:
                return name

    # 策略2：正则提取
    patterns = [
        # "X Raises/Closes/Secures $NNNM" → X 是主角
        (r'^([A-Z][A-Za-z0-9\s&\.,\'\-\u2019]+?)\s+(?:raises|closes|secures|wins|gets|attracts|draws)\s+', 1),
        # "X Raises $NNNM in/on Y" → X 是主角
        (r'^([A-Z][A-Za-z0-9\s&\.,\'\-\u2019]+?)\s+raises?\s+\$', 1),
        # "X acquires/buys Y" → X 是主角
        (r'^([A-Z][A-Za-z0-9\s&\.,\'\-\u2019]+?)\s+(?:acquires|acquisition|buys|purchases|merges)', 1),
        # "X to acquire Y" → X 是主角
        (r'^([A-Z][A-Za-z0-9\s&\.,\'\-\u2019]+?)\s+to\s+acquire', 1),
        # "X posts/reports QN revenue/profit" → X 是主角
        (r'^([A-Z][A-Za-z0-9\s&\.\-\u2019]+?)[\'’]?(?:\s+\w+)?\s+(?:posts|reports|beats|misses|revenue|profit|earnings)', 1),
        # "X launches/expands into Y" → X 是主角
        (r'^([A-Z][A-Za-z0-9\s&\.\-\u2019]+?)\s+(?:launches|expands|enters|rolls out|partners)', 1),
        # "X valued at $Y" → X 是主角
        (r'^([A-Z][A-Za-z0-9\s&\.\-\u2019]+?)\s+valued\s+at', 1),
        # "X files for IPO" → X 是主角
        (r'^([A-Z][A-Za-z0-9\s&\.\-\u2019]+?)\s+(?:files|plans|ready)\s+(?:for|to)', 1),
    ]
    for pat, group in patterns:
        m = re.search(pat, clean, re.I)
        if m:
            name = m.group(group).strip().rstrip(',;:').strip()
            # 清理常见前缀词
            skip = {'why ', 'how ', 'what ', 'who ', 'where ', 'when ', 'this ', 'the '}
            for s in skip:
                if name.lower().startswith(s):
                    name = name[len(s):].strip()
            if len(name) >= 2 and len(name) <= 40:
                return name

    return None

def _build_reason(title, ev_type, region, company_name=None):
    """生成 fallback reason：必须包含当事人 + 事件 + 金额（从标题提取）"""
    subject = _extract_subject(title) or company_name
    r = region or ''

    # 金额提取
    amt = _parse_amount(title)
    amt_str = _format_amount(amt) if amt > 0 else ''

    # 中资出海检测
    is_chinese = any(kw.lower() in title.lower() for kw in CHINESE_OUTBOUND)

    if subject:
        # 包含公司名的 reason
        if ev_type == 'funding':
            if amt_str:
                reason = f"{subject}获{amt_str}融资"
            else:
                reason = f"{subject}完成融资"
        elif ev_type == 'ma':
            # 尝试提取收购对象
            m = re.search(r'(?:acquires?|buys|purchases)\s+([A-Z][A-Za-z0-9\s&\-]+?)(?:\s+for|\s+in|\s*$|\.)', title, re.I)
            target = m.group(1).strip() if m else None
            if target and len(target) < 30:
                reason = f"{subject}收购{target}"
            else:
                reason = f"{subject}达成并购"
        elif ev_type == 'earnings':
            # 尝试提取增长数字
            m = re.search(r'(up|down|growth|jumped|rose|fell|slumped)\s+(\d+(?:\.\d+)?%?)', title, re.I)
            if m:
                reason = f"{subject}营收{m.group(1)} {m.group(2)}"
            else:
                reason = f"{subject}发布财报"
        elif ev_type == 'strategy':
            m = re.search(r'(?:launches|expands|enters|partners|files for IPO|plans to go)', title, re.I)
            if m:
                reason = f"{subject}战略新动向"
            else:
                reason = f"{subject}战略调整"
        else:
            # 从标题提取首段代替"有新动态"（零成本提高信息量）
            title_short = re.split(r'[,;、。.!！?？]', title)[0].strip()
            if len(title_short) > 40:
                title_short = title_short[:40] + '…'
            if len(title_short) >= 10:
                if title_short.startswith(subject) and len(title_short) > len(subject):
                    reason = title_short  # 标题以公司名开头，直接用标题
                elif title_short != subject:
                    reason = f"{subject}：{title_short}"
                else:
                    reason = f"{subject}有新动态"
            else:
                reason = f"{subject}有新动态"
    else:
        # 没有任何信息时的最后兜底：用标题前段代替泛化模板
        # 取第一个句子（句号/问号/叹号前），最长 35 字
        title_short = re.split(r'[.。!！?？]', title)[0].strip()
        if len(title_short) > 35:
            title_short = title_short[:35] + '…'
        if len(title_short) >= 8:
            reason = f"{r or '全球'}：{title_short}"
        elif is_chinese:
            for kw in ['tiktok', 'shein', 'temu', 'bytedance', 'alibaba', 'tencent', 'ant', 'jd.com', 'kuaishou']:
                if kw in title.lower():
                    reason = f"{kw.capitalize()}有新动态"
                    break
            else:
                reason = "中资科技公司动态"
        elif r:
            templates = {
                'funding': f"{r}科技公司融资{amt_str}落地" if amt_str else f"{r}科技公司融资",
                'ma':      f"{r}科技公司并购",
                'earnings':f"{r}科技公司财报",
                'strategy':f"{r}科技公司战略",
                'other':   f"{r}科技动态",
            }
            reason = templates.get(ev_type, f"{r}科技动态")
        else:
            reason = "全球科技动态"

    return reason

def _infer_source_tier(event):
    """为历史事件补齐信源分层，保证周/月报能按业务价值排序。"""
    source = (event.get('source') or '').lower()
    url = (event.get('url') or '').lower()
    combined = f'{source} {url}'
    if event.get('source_tier'):
        return event['source_tier']
    if any(hint in combined for hint in OFFICIAL_IR_SOURCE_HINTS):
        return 'L1 官方/IR源'
    if 'google news' in source or 'news.google.com' in url:
        return 'L5 Google News 补漏源'
    if any(name in source for name in ['newzoo', 'gamesindustry', 'pocketgamer', 'paypers', 'fintech futures', 'fintech news singapore', 'ecommercebytes', 'retail4growth', 'mobile world live']):
        return 'L4 垂直赛道精品源'
    if 'rest of world' in source:
        return 'L4 深度趋势源'
    if any(name in source for name in VERTICAL_DEAL_SOURCES):
        return 'L2 垂直交易源'
    if any(name in source for name in REGIONAL_ECOSYSTEM_SOURCES):
        return 'L3 区域生态源'
    return 'L3 区域生态源'

def infer_frontend_bd_context(event):
    """从既有事件字段推断 BD 触发器，修复历史数据缺字段的问题。"""
    ev_type = (event.get('event_types') or ['other'])[0]
    text = ' '.join([
        event.get('title', ''),
        event.get('summary_short', ''),
        event.get('reason', ''),
        event.get('impact', ''),
        event.get('insight_label', ''),
    ]).lower()
    triggers = []
    for name, keywords in BD_TRIGGER_RULES:
        if any(kw in text for kw in keywords):
            triggers.append(name)
    if ev_type == 'funding' and '预算窗口' not in triggers:
        triggers.append('预算窗口')
    if ev_type == 'ma' and '整合窗口' not in triggers:
        triggers.append('整合窗口')
    if ev_type == 'earnings' and '预算窗口' not in triggers:
        triggers.append('预算窗口')
    if ev_type == 'strategy' and not any(t in triggers for t in ['扩张窗口', '生态窗口']):
        triggers.append('扩张窗口')

    opportunities = []
    for trigger in triggers:
        for name in OPPORTUNITY_BY_TRIGGER.get(trigger, []):
            if name not in opportunities:
                opportunities.append(name)
    for name in OPPORTUNITY_BY_TYPE.get(ev_type, []):
        if name not in opportunities:
            opportunities.append(name)

    bd_priority = classify_bd_priority(event)
    follow_up_window = follow_up_window_for_priority(bd_priority)

    return {
        'bd_triggers': triggers[:3] or ['持续观察'],
        'opportunity_direction': ' / '.join(opportunities[:4] or ['持续观察']),
        'follow_up_window': follow_up_window,
        'bd_priority': bd_priority,
    }

def ensure_business_fields(event):
    """补齐 BD 机会字段；新旧事件都走同一口径。"""
    source_tier = _infer_source_tier(event)
    event['source_tier'] = source_tier
    event.setdefault('source_role', SOURCE_ROLE_BY_TIER.get(source_tier, 'regional_ecosystem'))
    bd = infer_frontend_bd_context(event)
    for key, value in bd.items():
        if key in {'bd_priority', 'follow_up_window'} or not event.get(key):
            event[key] = value
    if isinstance(event.get('bd_triggers'), str):
        event['bd_triggers'] = [event['bd_triggers']]
    return event

def enrich(event):
    """统一事件格式 + 自动评分"""
    if 'event_types' not in event:
        event['event_types'] = [CATEGORY_MAP.get(event.get('category', '其他'), 'other')]

    ev_type = event['event_types'][0]
    region = event.get('region', '')
    title = event.get('title', '')

    # 判断 reason 是否有效（通用模板也算无效，必须重新生成）
    why = event.get('why_important', '')
    existing_reason = event.get('reason', '')
    # 通用模板 reason 列表——这些是 AI 生成的烂 reason，必须重新生成
    GENERIC_REASONS = {
        # 短模式（子串匹配 — 覆盖 "亚太科技公司财报披露" 等程序生成变体）
        '科技动态', '财报披露', '融资事件', '战略动态', '并购/收购', '金额待确认',
        '战略调整', '有新动态', '科技公司融资', '科技公司并购', '科技公司战略',
        '科技行业动态', '的高估值',
        # 完整短语保留兼容
        '中东科技公司融资事件，金额待确认',
        '中资科技动态', '亚太科技动态', '欧洲科技动态', '中东科技动态',
        '非洲科技动态', '拉美科技动态',
        '中资科技公司战略动态',
        '中资科技公司财报披露',
        '中资科技公司并购/收购',
        '中资科技巨头持续增长，巩固行业地位，吸引更多合作资源',
        '中资电商巨头海外拓展成功，为国际市场ICT合作带来新机遇',
        '中资视频平台增长强劲提升行业影响力，吸引资金和合作关注',
        '中资金融科技巨头战略布局，吸引资金流入，提升行业关注度',
        '亚太地区出行平台拓展外卖业务版图，加强本地服务能力',
    }
    is_generic = any(p in existing_reason for p in GENERIC_REASONS)
    reason_ok = (existing_reason
                 and len(existing_reason) >= 10
                 and '⚠️' not in existing_reason
                 and '待分析' not in existing_reason
                 and existing_reason not in TRUNCATED_JUNK
                 and not is_generic)
    why_ok = why and len(why) >= 10 and why not in TRUNCATED_JUNK

    if why_ok:
        event['reason'] = why
    elif reason_ok:
        pass  # 保留 AI 生成的 reason
    else:
        # 生成有信息量的 fallback：提取公司名 + 事件类型
        event['reason'] = _build_reason(title, ev_type, region, event.get('company_name'))

    # summary_short fallback：AI 没生成时用 reason 兜底
    ss = event.get('summary_short', '')
    if not ss or len(ss) < 8 or ss[:25] == title[:25]:
        event['summary_short'] = event.get('reason', '')

    event.setdefault('impact', event.get('impact_scope', '未知'))
    event.setdefault('insight_label', INSIGHT_LABEL_MAP.get(ev_type, '其他'))
    event.setdefault('region', '未知')
    event.setdefault('companies', [])
    event.setdefault('source', '未知')
    publisher = event.get('publisher') or event.get('source_detail')
    if not publisher and event.get('source') == 'Google News':
        publisher = _extract_title_publisher(title)
    event['publisher'] = publisher or ''
    event['source_detail'] = event.get('source_detail') or publisher or ''
    if event.get('source') == 'Google News' and publisher:
        event['display_source'] = publisher
    else:
        event['display_source'] = event.get('source', '未知')
    # 规则层字段缺失时补算（内存态；AI 0-10 分仅留档，不参与展示决策）
    if not (event.get('attention_score') or event.get('confidence_score')):
        try:
            from signal_scoring import apply_signal_contract
            apply_signal_contract(event)
        except Exception:
            pass
    # 用于 Market Pulse 突出展示
    amt = _parse_amount(event.get('title', ''))
    event['display_amount'] = _format_amount(amt) if amt > 0 else ''

    # 检测中资出海：若涉及中国科技公司出海，追加"中资"标签
    is_chinese = _is_chinese_capital(event)
    event['is_chinese_capital'] = is_chinese
    if is_chinese:
        ev_type = event.get('event_types', ['other'])[0]
        event['insight_label'] = '中资出海'

    for old_key in ('summary', 'category', 'impact_range', 'impact_scope', 'why_important'):
        event.pop(old_key, None)
    # 保留 date 字段用于 Market Pulse 日期权重
    if not event.get('date'):
        event['date'] = _cn_today()

    return ensure_business_fields(event)
