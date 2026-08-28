"""
生成全球互联网动态情报站 HTML 页面
评分系统：基于 Galtung & Ruge 新闻价值理论 + 金融情报平台通用因子
"""
import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from jinja2 import Environment, select_autoescape

try:
    from event_dates import is_display_date
    from event_contract import prepare_event_contract
    from event_value import (
        classify_bd_priority,
        event_score,
        event_type,
        is_company_quality_signal,
        is_google_news_event,
        follow_up_window_for_priority,
    )
    from signal_clusters import build_signal_clusters
    from narratives import build_narrative
    from period_themes import build_monthly_trends, build_weekly_themes, build_company_changes, build_industry_changes
    from entity_signal_conversion_report import event_matches_entity
    from internet_relevance import is_mainline_internet_event
    from fetch_news import _fingerprint_match
    from view_selectors import (
        select_company_events,
        select_company_quality_events,
        select_homepage_events,
        is_main_view_event,
        is_period_high_value_event,
        select_main_list_events,
        select_mature_main_date,
        select_period_high_value_events,
        select_review_events,
        signal_sort_key,
    )
except ImportError:
    from scripts.event_dates import is_display_date
    from scripts.event_contract import prepare_event_contract
    from scripts.event_value import (
        classify_bd_priority,
        event_score,
        event_type,
        is_company_quality_signal,
        is_google_news_event,
        follow_up_window_for_priority,
    )
    from scripts.signal_clusters import build_signal_clusters
    from scripts.narratives import build_narrative
    from scripts.period_themes import build_monthly_trends, build_weekly_themes, build_company_changes, build_industry_changes
    from scripts.entity_signal_conversion_report import event_matches_entity
    from scripts.internet_relevance import is_mainline_internet_event
    from scripts.fetch_news import _fingerprint_match
    from scripts.view_selectors import (
        select_company_events,
        select_company_quality_events,
        select_homepage_events,
        is_main_view_event,
        is_period_high_value_event,
        select_main_list_events,
        select_mature_main_date,
        select_period_high_value_events,
        select_review_events,
        signal_sort_key,
    )

try:
    from zoneinfo import ZoneInfo
    SHANGHAI_TZ = ZoneInfo('Asia/Shanghai')
except Exception:
    SHANGHAI_TZ = timezone(timedelta(hours=8))

# Windows 控制台默认 GBK，emoji 打印会抛 UnicodeEncodeError；统一 UTF-8 输出
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


def _cn_now():
    return datetime.now(SHANGHAI_TZ)


def _cn_today():
    return _cn_now().strftime('%Y-%m-%d')

CATEGORY_MAP = {
    '融资': 'funding', '并购': 'ma', 'IPO': 'earnings',
    '财报': 'earnings', '战略': 'strategy', '其他': 'other',
    '上市': 'earnings', '扩张': 'strategy',
}
INSIGHT_LABEL_MAP = {
    'funding': '融资', 'ma': '并购',
    'earnings': '财报', 'strategy': '战略', 'other': '其他',
}
TRUNCATED_JUNK = {
    'Show HN: I built a f', 'Big-Endian Testing w', 'April 2026 TLDR Setu',
    'Show HN: I built a frontp', 'Show HN: ctx – an Ag',
    'Samsung Magician dis', 'Google releases Gemm', 'Show HN: Apfel – The',
    'Decisions that erode', 'What Category Theory',
    'ESP32-S31: Dual-Core', 'Yeachan-Heo/oh-my-co', 'onyx-dot-app/onyx',
    'google-research/time', 'siddharthvaddem/open', 'dmtrKovalenko/fff.nv',
    'f/prompts.chat', 'sherlock-project/she',
}

# ─── 评分因子 ───────────────────────────────────────────────

def _parse_amount(title):
    """从标题提取金额（单位：M美元），返回浮点数"""
    patterns = [
        (r'\$([0-9,]+(?:\.\d+)?)\s*[Bb](?:illion)?', 1000),
        (r'€([0-9,]+(?:\.\d+)?)\s*[Mm](?:illion)?', 1),
        (r'\$([0-9,]+(?:\.\d+)?)\s*[Mm](?:illion)?', 1),
    ]
    for pat, mult in patterns:
        m = re.search(pat, title, re.I)
        if m:
            val = float(m.group(1).replace(',', '')) * mult
            return val
    return 0

def _format_amount(amount):
    """金额格式化，统一显示为 $XM 或 $XB"""
    if amount >= 1000:
        return f"${amount/1000:.0f}B"
    return f"${amount:.0f}M"

def _extract_title_publisher(title):
    title = (title or '').strip()
    for sep in [' - ', ' | ', ' — ', ' – ']:
        if sep in title:
            left, right = title.rsplit(sep, 1)
            right = right.strip()
            if left.strip() and 1 < len(right) <= 40:
                return right
    return ''

AMOUNT_BUCKETS = [
    (0,      5,    1),
    (5,      20,   2),
    (20,     100,  3),
    (100,    500,  4),
    (500,    1000, 5),
    (1000,   float('inf'), 6),
]

def _amount_score(amount):
    for lo, hi, pts in AMOUNT_BUCKETS:
        if lo <= amount < hi:
            return pts
    return 0

EVT_SCORE = {
    'ma':       2,
    'earnings': 2,
    'funding':  1,
    'strategy': 1,
    'other':    0,
}

REGION_WEIGHT = {
    '非洲': 1.30,
    '中东': 1.25,
    '亚太': 1.20,
    '拉美': 1.15,
    '欧洲': 1.00,
    '中资': 1.25,  # 中国科技巨头海外扩张，高情报价值
}

# 中资出海公司名单（用于识别"中资"区域）
CHINESE_CAPITAL_COMPANIES = {
    '字节', 'tiktok', 'byteDance', 'bytedance', '抖音',
    '腾讯', 'tencent', '微信',
    '阿里巴巴', 'alibaba', 'aliyun', 'lazada',
    '京东', 'jd.com', 'jd retail',
    '快手', 'kuaishou',
    '美团', 'meituan',
    '蚂蚁', 'ant group', 'antgroup', '支付宝', 'alipay',
    '拼多多', 'pinduoduo',
    '百度', 'baidu',
    '小米', 'xiaomi',
    '滴滴', 'didi',
    'shein', '希音',
    'temu',
    'oppo', 'vivo', 'realme',
    '传音', 'transsion', 'tecno',
    '比亚迪', 'byd',
}

# 亚太新增公司（提升区域关联性）
REGION_COMPANIES = {
    '亚太': {'cyberagent', 'square enix', 'vng', 'vnggroup', 'grab', 'gojek', 'sea group', 'shopee'},
    '欧洲': {'trendyol', 'hepsiburada', 'kaspi', 'olx', ' Allegro'},
}

# 用 \b 词边界避免子串误匹配
def _is_hot_industry(title_lower, reason_lower=''):
    combined = (title_lower + ' ' + reason_lower).lower()
    hot = {
        r'\bai\b', r'\bml\b', r'\bllm\b', r'\bgpt\b',
        r'\bfintech\b', r'\bfintech\b',
        r'\brobot\b', r'\bclimate ?tech\b',
        r'\bchips?\b', r'\bchipset\b',
    }
    hot.update({'AI', 'ML', '大模型', '金融科技', '机器人', '农业科技'})
    for kw in hot:
        if kw in combined:
            return True
    return False

def _has_top_investor(title_lower):
    investors = [
        'softbank', 'vision fund', 'mubadala', 'adia', 'temasek',
        'coatue', 'a16z', 'sequoia', 'index ventures',
        'thiel', 'founders fund', 'khosla', 'general atlantic',
    ]
    return any(inv in title_lower for inv in investors)

# 对比语境动词：关键词出现在这些词近旁时，是被比较对象而不是事件主体
COMPARISON_VERBS = (
    'top', 'tops', 'topped', 'beats', 'beat', 'surpasses', 'surpassed',
    'outperforms', 'outperformed', 'overtakes', 'overtook', 'exceeds', 'exceeded',
    'leads', 'led', 'edges', 'edged', '超过', '超越', '高于', '领先', '击败',
    '胜于', '跑赢',
)

def _chinese_entity_hit(name):
    """实体名匹配中资名单，词边界匹配防误伤（'byd' 不命中共名品牌）"""
    n = name.lower()
    for kw in CHINESE_CAPITAL_COMPANIES:
        k = kw.lower()
        if re.search(rf'(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])', n):
            return True
    return False


def _in_comparison_context(text, pos):
    """关键词位置往前 30 字符内出现对比动词 → 关键词是被比较对象而非事件主体"""
    window = text[max(0, pos - 30):pos]
    return any(v in window for v in COMPARISON_VERBS)


def _is_chinese_capital(event):
    """检测事件是否涉及中资出海公司。

    两级判定：
    1. 实体级——company_name / companies 命中中资名单 → 中资（实体是主角）
    2. 文本级——标题/点评含名单词，但若处于对比语境（被超越/领先的对手），
       不算中资（Kakao Kanana-2 标题里的 Alibaba 是对比对象不是主角）
    """
    company = (event.get('company_name') or '').lower()
    if company and _chinese_entity_hit(company):
        return True
    for c in event.get('companies') or []:
        if c and _chinese_entity_hit(str(c)):
            return True
    title = (event.get('title') or '').lower()
    reason = (event.get('reason') or event.get('why_important') or '').lower()
    for text in (title, reason):
        for kw in CHINESE_CAPITAL_COMPANIES:
            k = kw.lower()
            for match in re.finditer(re.escape(k), text):
                if not _in_comparison_context(text, match.start()):
                    return True
    return False

def calculate_score(event):
    """多因子评分，clamp(1-10)，全部从数据推导"""
    title = event.get('title', '')
    title_lower = title.lower()
    ev_type = event.get('event_types', ['other'])[0]

    amount = _parse_amount(title)
    amt_pts = _amount_score(amount) if amount > 0 else 1
    type_pts = EVT_SCORE.get(ev_type, 0)
    region = event.get('region', '')
    region_mult = REGION_WEIGHT.get(region, 1.0)
    industry_pts = 1 if _is_hot_industry(title_lower, event.get('why_important', '')) else 0
    named_pts = 1 if event.get('companies') or event.get('company_name') else 0
    investor_pts = 1 if _has_top_investor(title_lower) else 0

    raw = (amt_pts + type_pts + industry_pts + named_pts + investor_pts) * region_mult
    return max(round(min(raw, 10)), 1)

# ─── 预设公司名单 ─────────────────────────────────────────────

REGION_ORDER = ['全球', '北美', '亚太', '欧洲', '中东', '拉美', '非洲', '中资']


def load_entity_pool(path='data/entity_pool.json'):
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {'entities': [], 'portfolio': {}}


def _portfolio_by_entity(pool):
    result = {}
    for tier, rows in (pool.get('portfolio') or {}).items():
        for row in rows or []:
            result[row.get('entity_id')] = {
                'portfolio_tier': tier,
                'decision_use': row.get('decision_use') or '',
            }
    return result


def build_entity_event_timelines(events_by_date, entities):
    """Map every qualified event to the object aliases it actually describes.

    公司卡门槛 = 主列表事件 或 公司质量信号。主列表刻意整体排除 Google News
    聚合源（防全局 feed 被重复聚合噪声淹没），但公司卡应显示自家公司的强事件，
    哪怕来自 Google News（如 Adyen 无专属 RSS，覆盖面主要靠聚合源）。
    """
    candidates = [
        event
        for rows in (events_by_date or {}).values()
        for event in rows or []
        if is_main_view_event(event) or is_company_quality_signal(event)
    ]
    timelines = {}
    for entity in entities or []:
        matched = []
        seen = set()
        entity_name = entity.get('name') or ''
        for event in candidates:
            if not event_matches_entity(event, entity):
                continue
            key = event.get('url') or f"{event.get('date', '')}|{event.get('title', '')}"
            if key in seen:
                continue
            seen.add(key)
            matched.append(event)
            names = event.setdefault('matched_entities', [])
            if entity_name and entity_name not in names:
                names.append(entity_name)
        timelines[entity.get('id') or entity_name] = sorted(
            matched,
            key=lambda row: ((row.get('date') or '')[:10], event_score(row)),
            reverse=True,
        )
    return timelines

# ─── BD opportunity fallback ────────────────────────────────

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

OPPORTUNITY_BY_TRIGGER = {
    '预算窗口': ['增长方案', '云与AI基础设施', '广告商业化', '支付与风控'],
    '扩张窗口': ['本地化合作', '渠道伙伴', '跨境支付', '云服务'],
    '降本窗口': ['AI客服', '自动化运营', '外包服务', '成本优化'],
    '合规窗口': ['合规科技', '数据治理', '安全风控', '牌照合作'],
    '整合窗口': ['系统整合', '数据迁移', '组织协同工具', '生态打通'],
    '生态窗口': ['联合解决方案', '商户增长', '开放平台合作', '渠道共建'],
    '竞争窗口': ['竞品替代', '差异化增长', '市场进入策略', '客户防守'],
}

OPPORTUNITY_BY_TYPE = {
    'funding': ['增长方案', '云与AI基础设施', '市场拓展合作'],
    'ma': ['系统整合', '数据迁移', '生态打通'],
    'earnings': ['广告商业化', '支付与风控', '成本优化'],
    'strategy': ['联合解决方案', '本地化合作', '渠道伙伴'],
    'other': ['持续观察'],
}

SOURCE_ROLE_BY_TIER = {
    'L1 官方/IR源': 'official_ir',
    'L2 垂直交易源': 'venture_media',
    'L3 区域生态源': 'regional_ecosystem',
    'L4 深度趋势源': 'deep_trend',
    'L4 垂直赛道精品源': 'industry_vertical',
    'L5 Google News 补漏源': 'company_radar',
}

# ─── Fallback reason 生成 ───────────────────────────────────

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

# ─── Enrich ─────────────────────────────────────────────────

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

def load_events():
    with open('data/events.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, list):
        grouped = {}
        for event in data:
            date = event.get('date', _cn_today())[:10]
            grouped.setdefault(date, []).append(enrich(prepare_event_contract(dict(event))))
        return grouped
    return {
        k: [enrich(prepare_event_contract(dict(e))) for e in v]
        for k, v in data.items()
        if is_display_date(k, now=_cn_now())
    }


def split_company_events(events):
    """
    将事件拆分为公司动态和通用热点
    - 公司动态只保留7天内，不过滤
    - 通用热点：排除 other 类型，保留可解释、可展示的信号事件
    """
    week_ago = (_cn_now() - timedelta(days=7)).strftime('%Y-%m-%d')
    for evs in events.values():
        for e in evs:
            if not e.get('is_company'):
                ensure_business_fields(e)
    return select_company_events(events, week_ago)

def get_signal_events(events):
    """
    获取信号事件：
    1. 只取最近7天内的信号事件
    2. 排除中资出海
    3. 排除other类型
    4. 排除低评分（<5）事件
    5. 按日期倒序排序
    """
    seen = set()
    result = []

    week_ago = (_cn_now() - timedelta(days=7)).strftime('%Y-%m-%d')

    for date in sorted(events.keys(), reverse=True):
        # 只处理最近7天内的日期
        if date < week_ago:
            continue

        for event in events[date]:
            if event['url'] in seen:
                continue
            seen.add(event['url'])

            # 排除中资出海
            if event.get('is_chinese_capital'):
                continue

            # 只取信号事件（排除other类型）
            ev_type = event.get('event_types', ['other'])[0]
            if ev_type == 'other':
                continue

            # 排除低评分事件（规则层注意力分<50视为低质量）
            score = event_score(event)
            if score < 50:
                continue

            result.append(event)

    return result  # 已经在日期倒序遍历，返回即有序

def build_weekly_summary(all_feed, signals, latest_date_events, all_events, summary_date=None):
    """生成周报摘要：排除中资出海，只展示真正的"非中美"动态"""
    # 排除中资出海（中资有独立标签页）
    non_chinese = [e for e in all_feed if not e.get('is_chinese_capital')]
    # ── 数字统计 ───────────────────────────────────────────
    funding = sum(1 for e in non_chinese if e.get('event_types', [''])[0] == 'funding')
    ma      = sum(1 for e in non_chinese if e.get('event_types', [''])[0] == 'ma')
    earnings= sum(1 for e in non_chinese if e.get('event_types', [''])[0] == 'earnings')
    strategy= sum(1 for e in non_chinese if e.get('event_types', [''])[0] == 'strategy')
    total   = len(non_chinese)

    # ── type_counts：动态生成筛选按钮用 ───────────────────
    type_counts = {
        '融资': funding, '并购': ma, '财报': earnings, '战略': strategy,
    }

    # 区域分布
    region_counts = {}
    for e in non_chinese:
        r = e.get('region', '未知')
        if r != '未知':
            region_counts[r] = region_counts.get(r, 0) + 1
    region_counts = dict(sorted(region_counts.items(), key=lambda x: x[1], reverse=True))
    hot_region = max(region_counts, key=region_counts.get) if region_counts else ''

    # ── 金额计算（用于 headline）───────────────────────
    # 找最大融资事件
    funding_events = [e for e in non_chinese if e.get('event_types', [''])[0] == 'funding']
    top_funding = max(funding_events, key=lambda x: event_score(x), default=None)
    max_ma = next((e for e in non_chinese if e.get('event_types', [''])[0] == 'ma'), None)

    # ── Headline ────────────────────────────────────────
    # 用趋势描述，不用单一事件（避免"说亚太最强但Top3全是欧洲"的尴尬）
    parts_hl = []
    if funding > 0:
        parts_hl.append(f"融资{int(funding)}起")
    if ma > 0:
        parts_hl.append(f"并购{int(ma)}起")
    if earnings > 0:
        parts_hl.append(f"财报{int(earnings)}起")
    if hot_region and region_counts.get(hot_region):
        parts_hl.append(f"{hot_region}{region_counts[hot_region]}起")
    headline = "、".join(parts_hl) if parts_hl else f"共{int(total)}条动态"
    if len(region_counts) > 1:
        headline += f"覆盖{len(region_counts)}地区"

    # ── Summary ─────────────────────────────────────────
    parts = []
    if hot_region and region_counts.get(hot_region):
        parts.append(f"{hot_region}事件最多（{region_counts[hot_region]}起），占今日大头。")
    if funding >= 3:
        tf = top_funding
        top_co = tf.get('companies', [''])[0] if tf and tf.get('companies') else ''
        top_amt = _format_amount(_parse_amount(tf.get('title', ''))) if tf else ''
        if top_co and top_amt:
            parts.append(f"融资仍是主旋律，共{funding}起，最大单笔{top_co} {top_amt}。")
        elif top_co:
            parts.append(f"融资仍是主旋律，共{funding}起，最大单笔来自{top_co}。")
        else:
            parts.append(f"融资仍是主旋律，共{funding}起。")
    elif funding >= 1:
        parts.append(f"有{funding}起融资落地。")
    if ma >= 1:
        parts.append(f"另有{ma}起并购，显示{hot_region or '该地区'}行业整合加速。")
    if earnings >= 1:
        parts.append(f"本周财报季有{earnings}起值得关注。")
    if strategy >= 1:
        parts.append(f"另有{strategy}起战略动态值得关注。")
    if not parts:
        parts.append(f"共{total}条动态，覆盖{', '.join(region_counts.keys()) if region_counts else '各地区'}。")
    summary = ' '.join(parts)

    # Market Pulse must be scoped to the displayed batch. Otherwise historical
    # date panels show today's signals under an older date.
    mp_events = [
        e for e in non_chinese
        if e.get('event_types', ['other'])[0] != 'other'
    ]
    mp_events.sort(key=lambda e: (event_score(e), e.get('date', '')), reverse=True)
    mp_events = mp_events[:7]

    # ── P0 Agent：读取 AI 趋势分析，覆盖程序摘要 ──
    try:
        summary_path = 'data/summary.json'
        if os.path.exists(summary_path):
            with open(summary_path, 'r', encoding='utf-8') as sf:
                ai_summaries = json.load(sf)
            today_s = summary_date or _cn_today()
            if total and today_s in ai_summaries:
                ai_text = ai_summaries[today_s].strip()
                if len(ai_text) >= 20:
                    summary = ai_text  # 用 AI 生成的趋势分析代替程序摘要
    except Exception:
        pass  # 降级：保留程序生成摘要

    return {
        'total_events': total,
        'total_signals': len(signals),
        'funding': funding,
        'ma': ma,
        'earnings': earnings,
        'strategy': strategy,
        'regions': len(region_counts),
        'region_distribution': region_counts,
        'type_counts': type_counts,
        'headline': headline,
        'summary': summary,
        'top3': mp_events[:3],  # 保持兼容
        'top7': mp_events,  # 新增：今日要点7条
    }

def build_trend_groups(events):
    """将事件按趋势主题分组，如果没有 trend_topic 则按 company_name / insight_label 降级"""
    groups = {}
    for e in events:
        topic = e.get('trend_topic')
        if not topic:
            region = e.get('region', '')
            company = e.get('company_name', '')
            if company:
                topic = f"{company}动态 — {region}" if region else f"{company}动态"
            else:
                label = e.get('insight_label', '其他')
                topic = f"{label} — {region}" if region else label
        groups.setdefault(topic, []).append(e)
    result = [{'topic': k, 'events': v} for k, v in groups.items()]
    result.sort(key=lambda x: len(x['events']), reverse=True)
    return result


def keep_focus_date_clusters(clusters, limit=3):
    """Only keep rolling-window clusters that actually touch the selected date."""
    return [cluster for cluster in clusters or [] if cluster.get('has_focus_date')][:limit]


DAILY_EVENT_GROUPS = [
    ('selected', '精选', '最先看，强信号、强相关、可直接进入判断'),
    ('important', '重点', '值得继续跟，有明确对象或方向'),
    ('watch', '观察', '保留事实，用于背景留档和后续跟踪'),
]


def _daily_event_group_key(event):
    frozen = event.get('view_priority')
    if frozen in {'selected', 'important', 'watch'}:
        return frozen
    priority = classify_bd_priority(event)
    if priority == '高':
        return 'selected'
    if priority == '中':
        return 'important'
    return 'watch'


def build_daily_event_groups(events):
    """Group qualified daily events without weakening the main-list gate."""
    grouped = {key: [] for key, _, _ in DAILY_EVENT_GROUPS}
    for event in events:
        grouped[_daily_event_group_key(event)].append(event)
    return [
        {
            'key': key,
            'label': label,
            'description': description,
            'events': grouped[key],
        }
        for key, label, description in DAILY_EVENT_GROUPS
        if grouped[key]
    ]


def build_daily_navigation_copy(groups):
    """Build plain daily copy for the event-navigation layer."""
    total = sum(len(group['events']) for group in groups)
    if total <= 0:
        return '今日事件导航', '当前没有通过本站边界和信源筛选的日报事件。'
    counts = '，'.join(f"{group['label']} {len(group['events'])} 条" for group in groups)
    return (
        f"今日事件导航：{total} 条合格事件",
        f"{counts}。信源筛选和产品边界仍是准入门槛，分层只负责帮你决定先看什么。",
    )


def build_date_panel(date_str, day_events, all_events, raw_day_events=None, cluster_events=None):
    """预计算某日期的今日面板数据（趋势分组 + 判断 + 统计），供 JS 翻页切换"""
    signals = get_signal_events(all_events)
    weekly = build_weekly_summary(day_events, signals, day_events, all_events, summary_date=date_str)
    trend_groups = build_trend_groups(day_events)
    repair_events = build_review_events(raw_day_events or day_events)
    signal_clusters = keep_focus_date_clusters(
        build_signal_clusters(cluster_events or all_events, date_str, limit=12)
    )
    narrative = build_narrative(signal_clusters, fallback_events=day_events)
    daily_event_groups = build_daily_event_groups(day_events)
    daily_headline, daily_judgment = build_daily_navigation_copy(daily_event_groups)

    dt = datetime.strptime(date_str, '%Y-%m-%d')
    return {
        'trend_groups': trend_groups,
        'repair_events': repair_events,
        'judgment': daily_judgment,
        'top3': weekly.get('top3', []),
        'signal_clusters': strip_cluster_event_payloads(narrative.get('clusters', [])),
        'evidence_events': narrative.get('evidence_events', []),
        'daily_event_groups': daily_event_groups,
        'total_stories': len(day_events),
        'vol_label': f"VOL.{date_str}",
        'cn_date': f"{dt.year}年{dt.month}月{dt.day}日 星期{CHINESE_WEEKDAYS[dt.weekday()]}",
        'headline': daily_headline,
        'funding': weekly.get('funding', 0),
        'ma': weekly.get('ma', 0),
        'earnings': weekly.get('earnings', 0),
        'regions': weekly.get('regions', 0),
    }


def select_homepage_events_for_date(all_visible_events, date_str, fallback_events=None):
    return select_homepage_events(all_visible_events, date_str, fallback_events)


def strip_cluster_event_payloads(clusters):
    public_clusters = []
    for cluster in clusters or []:
        public_cluster = dict(cluster)
        public_cluster.pop('evidence_events', None)
        public_clusters.append(public_cluster)
    return public_clusters


def group_events_by_date(events):
    """将事件按日期分组，按时间倒序"""
    groups = {}
    for e in events:
        d = (e.get('date') or '')[:10]
        groups.setdefault(d, []).append(e)
    result = [{'date': k, 'events': v} for k, v in sorted(groups.items(), reverse=True)]
    return result


DISPLAY_ENTITY_STOPWORDS = {
    'inc', 'corp', 'corporation', 'company', 'co', 'ltd', 'limited', 'group',
    'holdings', 'holding', 'technologies', 'technology', 'tech', 'systems',
    'platform', 'platforms', 'analytics', 'computing', 'apps', 'app', 'software',
    'ai', 'digital', 'global', 'online', 'the', 'amazon', 'fulfillment',
    'competitor', 'more', 'than', 'korea', 'regional', 'local', 'studio',
    'busan', 'cloud', 'hands', 'training', 'startups',
}


def _normalize_display_subject(subject):
    text = re.sub(r'[^a-z0-9\u4e00-\u9fff]+', ' ', (subject or '').lower())
    tokens = [t for t in text.split() if t and t not in DISPLAY_ENTITY_STOPWORDS and len(t) > 1]
    return ' '.join(tokens[:4])


def _title_subject_key(title):
    subject = _extract_subject(title or '') or ''
    if subject:
        key = _normalize_display_subject(subject)
        if key:
            return key
    patterns = [
        r'\b([A-Z][A-Za-z0-9\.\-]{2,})\s+(?:raises?|raised|secures?|secured|closes?|closed)\b',
        r'\b([A-Z][A-Za-z0-9\.\-]{2,})\s+(?:doubles?|doubled|hits?|hit|reaches?|reached|is\s+valued|was\s+valued|valued)\b',
        r'^([A-Z][A-Za-z0-9\s&\.,\'\-\u2019]+?)\s+(?:raises?|raised|secures?|secured|closes?|closed|lands?|landed|bags?|bagged|gets?|got|receives?|received|attracts?|attracted)\b',
        r'^([A-Z][A-Za-z0-9\s&\.,\'\-\u2019]+?)\s+(?:doubles?|doubled|hits?|hit|reaches?|reached|is\s+valued|was\s+valued|valued)\b',
        r'^([A-Z][A-Za-z0-9\s&\.,\'\-\u2019]+?)\s+(?:acquires?|acquired|buys?|bought|merges?|merged|announces?|announced|reports?|reported|posts?|posted)\b',
    ]
    for pattern in patterns:
        match = re.search(pattern, title or '', re.I)
        if match:
            return _normalize_display_subject(match.group(1))
    return ''


def _display_subject_key(event):
    # 优先从标题提取主体：company_name 粒度粗（Kakao Pay 与 Kakao Bank 都标为 Kakao），
    # 标题能区分到子公司/具体实体，避免展示层误合并不同事件。
    key = _title_subject_key(event.get('title', ''))
    if key:
        return key
    key = _normalize_display_subject(event.get('company_name') or '')
    if key:
        return key
    companies = event.get('companies') or []
    if isinstance(companies, list) and companies:
        key = _normalize_display_subject(str(companies[0]))
        if key:
            return key
    return ''


def _normalized_title_key(title):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '', (title or '').lower())


def _nearby_days(date_a, date_b, window=3):
    if not date_a or not date_b:
        return date_a == date_b
    try:
        gap = abs((datetime.strptime(date_a, '%Y-%m-%d')
                   - datetime.strptime(date_b, '%Y-%m-%d')).days)
    except ValueError:
        return False
    return gap <= window


def _title_similarity(a, b):
    ta = _title_tokens(a or '')
    tb = _title_tokens(b or '')
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _title_tokens(title):
    words = re.findall(r'[a-z0-9]+', (title or '').lower())
    return set(w for w in words if len(w) > 2)


def dedupe_display_events(events):
    """展示前按同日、同主体、同类型兜底去重；财报/并购/融资类事件相邻 3 天内
    且标题相似度 ≥0.3 时合并，避免同一事件被多家媒体在相邻日期反复占据列表。
    strategy 仅同日合并，防止误删连续战略动作。低相似度（标题 token 差异大的同事件
    多源报道，如 Square Enix 财报）由采集层跨天去重负责，此处不做。"""
    kept = []
    seen_titles = set()
    seen_semantic = []  # [(date, event_type, subject_key, title)]
    for event in events:
        title_key = _normalized_title_key(event.get('title', ''))
        if title_key and title_key in seen_titles:
            continue
        if title_key:
            seen_titles.add(title_key)

        # AI 指纹兜底：主体+类型+量化锚点全匹配直接合并，绕过正则主体提取与标题相似度
        if event.get('canonical_company'):
            match = next((ev for ev in kept if _fingerprint_match(event, ev)), None)
            if match is not None:
                if event.get('url'):
                    match.setdefault('merged_from', [])
                    if event['url'] not in match['merged_from']:
                        match['merged_from'].append(event['url'])
                continue

        date_key = (event.get('date') or '')[:10]
        event_type = (event.get('event_types') or ['other'])[0]
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


def _bd_priority_rank(event):
    priority_rank = {'高': 3, '中': 2, '观察': 1}
    tier_rank = {
        'L1 官方/IR源': 5,
        'L2 垂直交易源': 4,
        'L3 区域生态源': 3,
        'L4 垂直赛道精品源': 3,
        'L4 深度趋势源': 2,
        'L5 Google News 补漏源': 1,
    }
    ev_type = (event.get('event_types') or ['other'])[0]
    type_rank = {'funding': 4, 'ma': 4, 'earnings': 3, 'strategy': 3, 'other': 1}.get(ev_type, 1)
    return (
        priority_rank.get(classify_bd_priority(event), 0),
        event_score(event),
        tier_rank.get(event.get('source_tier'), 0),
        type_rank,
        event.get('date', ''),
    )


def _short_event_text(event, max_len=54):
    text = clean_display_title(
        event.get('display_title') or event.get('summary_short') or event.get('reason') or event.get('title') or ''
    )
    return text if len(text) <= max_len else text[:max_len].rstrip() + '...'


def _build_top_opportunities(period_events, limit=5):
    seen = set()
    result = []
    for event in sorted(period_events, key=_bd_priority_rank, reverse=True):
        key = (event.get('company_name') or event.get('title') or '').lower()
        if key in seen:
            continue
        seen.add(key)
        result.append({
            'title': _short_event_text(event),
            'company': event.get('company_name') or (event.get('companies') or [''])[0] or '区域事件',
            'region': event.get('region') or '未知',
            'priority': event.get('bd_priority') or '观察',
            'trigger': ' / '.join(event.get('bd_triggers') or ['持续观察']),
            'direction': event.get('opportunity_direction') or '持续观察',
            'window': event.get('follow_up_window') or '持续观察',
            'source_tier': event.get('source_tier') or 'L3 区域生态源',
            'url': event.get('url') or '#',
        })
        if len(result) >= limit:
            break
    return result


def _build_regional_map(period_events, limit=6):
    grouped = {}
    for event in period_events:
        region = event.get('region') or '未知'
        item = grouped.setdefault(region, {
            'region': region,
            'count': 0,
            'high': 0,
            'companies': set(),
            'directions': {},
            'score_sum': 0,
        })
        item['count'] += 1
        item['score_sum'] += event_score(event)
        if is_period_high_value_event(event):
            item['high'] += 1
        if event.get('company_name'):
            item['companies'].add(event['company_name'])
        for direction in re.split(r'\s*/\s*', event.get('opportunity_direction') or ''):
            if direction:
                item['directions'][direction] = item['directions'].get(direction, 0) + 1

    result = []
    for item in grouped.values():
        top_direction = max(item['directions'].items(), key=lambda x: x[1])[0] if item['directions'] else '持续观察'
        avg_score = item['score_sum'] / item['count'] if item['count'] else 0
        result.append({
            'region': item['region'],
            'count': item['count'],
            'high': item['high'],
            'companies': len(item['companies']),
            'direction': top_direction,
            'avg_score': round(avg_score, 1),
        })
    result.sort(key=lambda x: (x['high'], x['count'], x['avg_score']), reverse=True)
    return result[:limit]


def _build_actions(period_events, limit=5):
    windows = ['7天内', '30天内', '持续观察']
    result = []
    for window in windows:
        candidates = [e for e in period_events if e.get('follow_up_window') == window]
        if not candidates:
            continue
        candidates.sort(key=_bd_priority_rank, reverse=True)
        top = candidates[0]
        result.append({
            'window': window,
            'action': f"围绕{top.get('region') or '重点区域'}的{top.get('opportunity_direction') or '合作机会'}建立跟进清单",
            'event': _short_event_text(top, 42),
            'count': len(candidates),
        })
        if len(result) >= limit:
            break
    return result


def _build_customer_tiers(period_events, limit=6):
    grouped = {}
    for event in period_events:
        company = event.get('company_name') or ((event.get('companies') or [''])[0] if event.get('companies') else '')
        if not company:
            continue
        item = grouped.setdefault(company, {
            'company': company,
            'region': event.get('region') or '未知',
            'count': 0,
            'high': 0,
            'score': 0,
            'direction': event.get('opportunity_direction') or '持续观察',
        })
        item['count'] += 1
        item['score'] = max(item['score'], event_score(event))
        if is_period_high_value_event(event):
            item['high'] += 1
        if event.get('opportunity_direction'):
            item['direction'] = event['opportunity_direction']

    result = []
    for item in grouped.values():
        if item['high'] > 0 or item['score'] >= 70:
            tier = 'A类：优先触达'
        elif item['count'] >= 2 or item['score'] >= 50:
            tier = 'B类：持续经营'
        else:
            tier = 'C类：观察入库'
        item['tier'] = tier
        result.append(item)
    result.sort(key=lambda x: (x['tier'], x['high'], x['score'], x['count']), reverse=True)
    return result[:limit]


def _build_themes(period_events, limit=6):
    counts = {}
    for event in period_events:
        for direction in re.split(r'\s*/\s*', event.get('opportunity_direction') or ''):
            if direction and direction != '持续观察':
                counts[direction] = counts.get(direction, 0) + 1
    return [
        {'name': name, 'count': count}
        for name, count in sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]
    ]


def _cluster_objects(cluster):
    companies = cluster.get('companies') or []
    if companies:
        return '、'.join(companies[:4])
    topic = cluster.get('topic') or ''
    if topic:
        return topic
    return cluster.get('region') or '区域对象'


def _period_event_object(event):
    if event.get('company_name'):
        return event['company_name']
    companies = event.get('companies') or []
    if companies:
        return companies[0]
    return _short_event_text(event, 18)


def _weekly_signal_key(event):
    ev_type = event_type(event)
    if ev_type == 'funding':
        return 'funding', '资金进入窗口'
    if ev_type == 'ma':
        return 'ma', '整合窗口'
    if ev_type == 'earnings':
        return 'earnings', '经营拐点窗口'
    triggers = event.get('bd_triggers') or []
    if any(trigger in triggers for trigger in ['扩张窗口', '生态窗口']):
        return 'expansion', '扩张与生态窗口'
    if '合规窗口' in triggers:
        return 'compliance', '合规窗口'
    direction = (event.get('opportunity_direction') or '').split('/')[0].strip()
    if '支付' in direction:
        return 'payment', '支付升级窗口'
    if '云' in direction or 'AI' in direction or '基础设施' in direction:
        return 'ai_infra', 'AI与基础设施窗口'
    return 'strategy', '战略观察窗口'


def _weekly_window_rank(event):
    priority_rank = {'高': 3, '中': 2, '观察': 1}
    return (
        priority_rank.get(classify_bd_priority(event), 0),
        event_score(event),
        event.get('date', ''),
    )


def _build_broad_weekly_focus_windows(period_events, limit=3):
    grouped = {}
    for event in period_events:
        ev_type = event_type(event)
        if ev_type == 'other':
            continue
        if is_google_news_event(event):
            continue
        if not is_mainline_internet_event(event):
            continue
        if not (is_period_high_value_event(event) or classify_bd_priority(event) in {'高', '中'} or ev_type in {'funding', 'ma', 'earnings'}):
            continue
        key, label = _weekly_signal_key(event)
        region = event.get('region') or '多地区'
        bucket = grouped.setdefault((region, key), {
            'region': region,
            'direction': label,
            'events': [],
        })
        bucket['events'].append(event)

    windows = []
    for bucket in grouped.values():
        events = sorted(bucket['events'], key=_weekly_window_rank, reverse=True)
        if len(events) < 2:
            continue
        objects = []
        sources = set()
        dates = set()
        for event in events:
            obj = _period_event_object(event)
            if obj and obj not in objects:
                objects.append(obj)
            if event.get('source_tier') or event.get('source'):
                sources.add(event.get('source_tier') or event.get('source'))
            if event.get('date'):
                dates.add((event.get('date') or '')[:10])
        if len(objects) < 2 and len(sources) < 2 and len(dates) < 2:
            continue
        evidence = [
            {
                'title': _short_event_text(event),
                'url': event.get('url') or '#',
                'date': (event.get('date') or '')[:10],
                'source': event.get('display_source') or event.get('source') or '公开来源',
                'type': event.get('insight_label') or event_type(event),
            }
            for event in events[:3]
        ]
        high_count = sum(1 for event in events if classify_bd_priority(event) == '高' or is_period_high_value_event(event))
        confidence = '高' if len(events) >= 4 and high_count >= 2 else '中' if high_count else '观察'
        windows.append({
            'title': f"{bucket['region']}{bucket['direction']}",
            'region': bucket['region'],
            'objects': '、'.join(objects[:4]) if objects else bucket['region'],
            'direction': bucket['direction'],
            'confidence': confidence,
            'evidence_count': len(events),
            'action': '进入下周观察名单，优先复核对象、预算和合作入口',
            'why': events[0].get('reason') or events[0].get('summary_short') or '',
            'evidence': evidence,
            'score': len(events) * 3 + high_count * 5 + len(objects),
        })
    windows.sort(key=lambda item: (item['confidence'] == '高', item['score'], item['evidence_count']), reverse=True)
    return windows[:limit]


def _entity_region_map():
    pool = load_entity_pool()
    return {
        entity.get('name'): entity.get('region') or '全球'
        for entity in pool.get('entities') or []
        if entity.get('name')
    }


def _build_weekly_focus_windows(period_events, end_date, limit=6):
    return build_weekly_themes(period_events, _entity_region_map(), limit=limit)


# ============================================================
# 编辑层缓存：编辑导读由输入主题唯一决定，封档周期输入冻结即输出冻结，
# 不应每班重调 AI。缓存键 = 周期 + 输入指纹；prompt 变更时递增版本号全量失效。
# ============================================================
EDITORIAL_PROMPT_VERSION = 1


def _editorial_cache_path():
    return os.path.join('data', 'editorial_cache.json')


def _load_editorial_cache():
    try:
        with open(_editorial_cache_path(), 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _save_editorial_cache(cache):
    try:
        os.makedirs('data', exist_ok=True)
        with open(_editorial_cache_path(), 'w', encoding='utf-8') as handle:
            json.dump(cache, handle, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _editorial_cache_get(cache_key, input_hash):
    """返回 (exact, stale)：exact 为 hash 完全命中的导读；stale 为同周期旧版导读（AI 全败时 fail-stale 兜底）。"""
    entry = (_load_editorial_cache().get('periods') or {}).get(cache_key)
    if not entry:
        return None, None
    editorial = entry.get('editorial')
    if entry.get('input_hash') == input_hash:
        return editorial, editorial
    return None, editorial


def _editorial_cache_put(cache_key, input_hash, editorial, channel):
    cache = _load_editorial_cache()
    cache.setdefault('version', 1)
    cache.setdefault('periods', {})
    cache['periods'][cache_key] = {
        'input_hash': input_hash,
        'editorial': editorial,
        'channel': channel,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }
    _save_editorial_cache(cache)


def _editorial_input_hash(brief):
    payload = json.dumps(brief, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(f"v{EDITORIAL_PROMPT_VERSION}:".encode('utf-8') + payload.encode('utf-8')).hexdigest()


def build_weekly_editorial(themes, period_id, cache_key=None):
    """AI 编辑层：把周报主题写成当期标题与叙事导读。失败优先沿用上一版缓存（fail-stale），无缓存返回 None。"""
    if not themes:
        return None
    try:
        from fetch_news import _post_chat, _chat_api_candidates
    except ImportError:
        try:
            from scripts.fetch_news import _post_chat, _chat_api_candidates
        except ImportError:
            return None
    apis = _chat_api_candidates()
    if not apis:
        return None

    # 任务形状路由：编辑层低频长输出，优先走实测最快最稳的 DeepSeek 官方；方舟留给事件分析主链
    apis = sorted(apis, key=lambda a: 0 if a.get('id') == 'deepseek' else 1)

    theme_brief = []
    for t in themes:
        evs = [e.get('title', '') for e in (t.get('evidence') or [])][:4]
        change_events = t.get('change_brief') or [
            {'title': title, 'date': '', 'type': ''} for title in evs
        ]
        theme_brief.append({
            'key': t.get('key', ''),
            'title': t.get('title') or t.get('direction', ''),
            'region': t.get('region', ''),
            'objects': t.get('objects', ''),
            'why': t.get('why', ''),
            'evidence_titles': evs,
            'change_events': change_events,
        })

    input_hash = None
    stale = None
    if cache_key:
        input_hash = _editorial_input_hash(theme_brief)
        exact, stale = _editorial_cache_get(cache_key, input_hash)
        if exact:
            print(f"  📋 周报编辑命中缓存（{period_id}）: {(exact.get('mainline') or '')[:30]}...")
            return exact

    prompt = f"""你是全球互联网科技情报编辑，受众是出海 BD、战略和投资从业者（周期标识：{period_id}）。

以下是本周期聚类出的主题（每主题含代表事件标题与日期）。请把它们编辑成一份可读的周报：

1. 写一个"本期编辑标题"（editorial_title，10-20字）：概括本期最值得关注的方向，像一期周刊的封面标题，不要使用"周报"字样，不要罗列数字。
2. 写一段"本期主线"（mainline，60-90字）：把本周最值得关注的 1-2 个方向串成一段叙事，说明发生了什么、为什么值得关注、指向什么判断。要像编辑写导读，不要罗列统计数字。
3. 为每个主题写一段叙事导读（narrative，40-70字）：把该主题的事件串成一条故事线，说明这些事件合起来意味着什么。不要重复事件标题，不要用"本周XX公司融资"这类清单式表达。

反格式化硬约束（必须遵守）：
- 每个主题的标题（theme_title）必须是具体变化描述，不能直接套用大类标签（如"AI与云基础设施"）。例如"拉美支付基建加速，本地收单商集体扩网"或"欧洲AI算力转向推理部署"。10-25字，含具体对象或区域，体现本周发生了什么变化。
- 禁止套话：不要写"本周…预示…"、"资本涌动"、"开启新篇章"、"值得关注"这类空话；不要用"由 N 条事实支持"这类元描述。每句话都要有可验证的信息。
- 叙事要写"变化"，不写"存在哪些话题"：说明本周在某个方向实际发生了什么（谁做了什么动作、市场怎么变），而不是罗列本周有哪些主题。

主题列表：
{json.dumps(theme_brief, ensure_ascii=False, indent=2)}

只输出 JSON，不要输出其他内容，格式：
{{"editorial_title": "本期编辑标题", "mainline": "本期主线", "themes": [{{"key": "主题key", "theme_title": "具体标题", "narrative": "该主题导读"}}]}}"""

    for api in apis:
        try:
            # 每通道单发：超时说明该通道当前干不了这活，等更久只会放大总时长，直接换下一通道
            resp = _post_chat(api, prompt, max_tokens=1400, temperature=0.3, timeout=(10, 60))
            if resp.status_code != 200:
                print(f"  ⚠️  周报编辑 {api['name']} 返回 {resp.status_code}，尝试下一个")
                continue
            text = resp.json()['choices'][0]['message']['content'].strip()
            text = re.sub(r'^```(?:json)?\s*', '', text).strip().rstrip('`').strip()
            data = json.loads(text)
            mainline = (data.get('mainline') or '').strip()
            editorial_title = (data.get('editorial_title') or '').strip()
            tmap = {t.get('key'): (t.get('narrative') or '').strip() for t in data.get('themes') or []}
            titles = {t.get('key'): (t.get('theme_title') or '').strip() for t in data.get('themes') or []}
            if len(mainline) < 20 or not tmap:
                print(f"  ⚠️  周报编辑 {api['name']} 结果不完整，尝试下一个")
                continue
            result = {'editorial_title': editorial_title, 'mainline': mainline, 'themes': tmap, 'theme_titles': titles}
            if cache_key and input_hash:
                _editorial_cache_put(cache_key, input_hash, result, api['name'])
            print(f"  📝 周报编辑已生成（{api['name']}，{len(tmap)} 个主题导读）: {mainline[:40]}...")
            return result
        except Exception as e:
            print(f"  ⚠️  周报编辑 {api['name']} 失败: {type(e).__name__}")
            continue
    if stale:
        print(f"  ⚠️  AI 编辑全通道失败，沿用上一版缓存导读（{cache_key}）")
        return stale
    return None


def build_monthly_editorial(trends, period_id, cache_key=None):
    """AI 编辑层：把月报趋势写成月度标题与结构变化导读。失败优先沿用上一版缓存（fail-stale），无缓存返回 None。"""
    if not trends:
        return None
    try:
        from fetch_news import _post_chat, _chat_api_candidates
    except ImportError:
        try:
            from scripts.fetch_news import _post_chat, _chat_api_candidates
        except ImportError:
            return None
    apis = _chat_api_candidates()
    if not apis:
        return None

    # 任务形状路由：编辑层低频长输出，优先走实测最快最稳的 DeepSeek 官方；方舟留给事件分析主链
    apis = sorted(apis, key=lambda a: 0 if a.get('id') == 'deepseek' else 1)

    trend_brief = []
    for t in trends:
        evs = [e.get('title', '') for e in (t.get('evidence') or [])][:4]
        trend_brief.append({
            'key': t.get('key', ''),
            'title': t.get('title') or t.get('name', ''),
            'change': t.get('change', ''),
            'region': t.get('region', ''),
            'summary': t.get('summary', ''),
            'week_count': t.get('week_count', 0),
            'count': t.get('count', 0),
            'previous_count': t.get('previous_count', 0),
            'evidence_titles': evs,
        })

    input_hash = None
    stale = None
    if cache_key:
        input_hash = _editorial_input_hash(trend_brief)
        exact, stale = _editorial_cache_get(cache_key, input_hash)
        if exact:
            print(f"  📋 月报编辑命中缓存（{period_id}）: {(exact.get('mainline') or '')[:30]}...")
            return exact

    prompt = f"""你是全球互联网科技情报编辑，受众是出海 BD、战略和投资从业者（周期标识：{period_id}）。

以下是本月聚类出的结构趋势（每趋势含代表事件标题、变化类型、周次跨度和事实数对比）。请把它们编辑成一份可读的月报：

1. 写一个"月度编辑标题"（editorial_title，10-20字）：概括本月最值得关注的结构变化方向，像一期月刊的封面标题，不要使用"月报"字样，不要罗列数字。
2. 写一段"本期主线"（mainline，100-160字）：把本月最重要的 1-2 个结构变化串成一段叙事，与上月对照，说明发生了什么、为什么发生、指向什么判断。
3. 为每个趋势写：
   - theme_title（10-25字）：该趋势本月具体发生了什么变化的具体标题，不能直接套用大类标签（如"支付与金融科技"）。含具体对象、区域或动作。
   - narrative（40-80字）：该趋势本月到底发生了什么变化，为什么值得关注。
   - drivers（最多3条）：驱动该变化的因素，只能基于证据标题/摘要中已出现的事实，不要编造。
   - uncertainty（20-50字）：当前判断的不确定性或反证。
   - next_validation（20-50字）：下月应验证什么才能确认该趋势继续成立。

反格式化硬约束（必须遵守）：
- 禁止套话：不要写"资本涌动"、"开启新篇章"、"值得关注"这类空话；不要用"由 N 条事实支持"这类元描述。每句话都要有可验证的信息。
- 叙事写"变化"，不写"存在哪些话题"：说明本月在某个方向实际发生了什么变化，而不是罗列本月有哪些趋势。

硬约束：不得提及证据中不存在的公司、区域或动作；不要凭空增加事实。

趋势列表：
{json.dumps(trend_brief, ensure_ascii=False, indent=2)}

只输出 JSON，不要输出其他内容，格式：
{{"editorial_title": "月度编辑标题", "mainline": "本期主线", "themes": [{{"key": "趋势key", "theme_title": "具体标题", "narrative": "...", "drivers": ["..."], "uncertainty": "...", "next_validation": "..."}}]}}"""

    for api in apis:
        try:
            # 每通道单发：超时说明该通道当前干不了这活，等更久只会放大总时长，直接换下一通道
            resp = _post_chat(api, prompt, max_tokens=1700, temperature=0.3, timeout=(10, 60))
            if resp.status_code != 200:
                print(f"  ⚠️  月报编辑 {api['name']} 返回 {resp.status_code}，尝试下一个")
                continue
            text = resp.json()['choices'][0]['message']['content'].strip()
            text = re.sub(r'^```(?:json)?\s*', '', text).strip().rstrip('`').strip()
            data = json.loads(text)
            mainline = (data.get('mainline') or '').strip()
            editorial_title = (data.get('editorial_title') or '').strip()
            tmap = {}
            titles = {}
            for t in data.get('themes') or []:
                key = t.get('key')
                if not key:
                    continue
                tmap[key] = {
                    'narrative': (t.get('narrative') or '').strip(),
                    'drivers': [str(d).strip() for d in (t.get('drivers') or []) if str(d).strip()][:3],
                    'uncertainty': (t.get('uncertainty') or '').strip(),
                    'next_validation': (t.get('next_validation') or '').strip(),
                }
                if (t.get('theme_title') or '').strip():
                    titles[key] = (t.get('theme_title') or '').strip()
            if len(mainline) < 30 or not tmap:
                print(f"  ⚠️  月报编辑 {api['name']} 结果不完整，尝试下一个")
                continue
            result = {'editorial_title': editorial_title, 'mainline': mainline, 'themes': tmap, 'theme_titles': titles}
            if cache_key and input_hash:
                _editorial_cache_put(cache_key, input_hash, result, api['name'])
            print(f"  📝 月报编辑已生成（{api['name']}，{len(tmap)} 个趋势导读）: {mainline[:40]}...")
            return result
        except Exception as e:
            print(f"  ⚠️  月报编辑 {api['name']} 失败: {type(e).__name__}")
            continue
    if stale:
        print(f"  ⚠️  AI 编辑全通道失败，沿用上一版缓存导读（{cache_key}）")
        return stale
    return None


def _load_aihot_archive(start_date, end_date, weekly=False):
    """读取周期内 AIHOT 归档热点（data/aihot_hot/YYYY-MM-DD.json），按标题去重。

    AIHOT 是实时外部数据，归属按自然周期（周报=自然周、月报=自然月）判定，
    不受站内事件截止日（end_date）限制——否则当天的实时热点会被挡在周期外。
    """
    import calendar
    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    if weekly:
        natural_end_dt = start_dt + timedelta(days=(6 - start_dt.weekday()))
    else:
        last_day = calendar.monthrange(start_dt.year, start_dt.month)[1]
        natural_end_dt = datetime(start_dt.year, start_dt.month, last_day)
    end = max(end_date, natural_end_dt.strftime('%Y-%m-%d'))

    archive_dir = os.path.join('data', 'aihot_hot')
    if not os.path.isdir(archive_dir):
        return []
    items = []
    seen_titles = set()
    for filename in sorted(os.listdir(archive_dir)):
        date_key = filename[:-5]
        if not (start_date <= date_key <= end):
            continue
        try:
            with open(os.path.join(archive_dir, filename), 'r', encoding='utf-8') as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        for item in data.get('items') or []:
            title = (item.get('title') or item.get('list_title') or '').strip()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            url = ''
            if item.get('original_links'):
                url = item['original_links'][0].get('url') or ''
            if not url:
                url = item.get('story_url') or ''
            items.append({
                'rank': len(items) + 1,
                'title': title,
                'heat': item.get('heat'),
                'url': url,
                'date': date_key,
            })
    return items[:10]


def _aihot_items_to_list(data, limit=10, date_key=None):
    """把 AIHOT 热点数据（当前快照或单日归档的 dict）转为统一列表结构。

    统一结构 [{rank,title,heat,url,date}]：今日 tab 与周报/月报共用同一份数据，
    展示层回退逻辑也以这份结构为锚。数据为空或结构异常返回 []。
    """
    if not data or not isinstance(data, dict):
        return []
    items = data.get('items') or []
    if not items:
        return []
    if date_key is None:
        date_key = (data.get('fetched_date') or '')[:10]
    out = []
    for idx, it in enumerate(items[:limit], 1):
        url = ''
        if it.get('original_links'):
            url = it['original_links'][0].get('url') or ''
        if not url:
            url = it.get('story_url') or ''
        out.append({
            'rank': idx,
            'title': it.get('title') or it.get('list_title') or '',
            'heat': it.get('heat'),
            'url': url,
            'date': date_key,
        })
    return out


def _latest_aihot_items(limit=10):
    """取最近一期有数据的 AIHOT 热点（按归档日期倒序），统一列表结构。

    当前/当期快照为空时用它兜底展示：宁可用最近一期有效数据，也不让区块消失。
    """
    archive_dir = os.path.join('data', 'aihot_hot')
    if not os.path.isdir(archive_dir):
        return []
    for filename in sorted(os.listdir(archive_dir), reverse=True):
        if not filename.endswith('.json'):
            continue
        date_key = filename[:-5]
        try:
            with open(os.path.join(archive_dir, filename), 'r', encoding='utf-8') as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        items = _aihot_items_to_list(data, limit, date_key=date_key)
        if items:
            return items
    return []


def build_period_report(events, start_date, end_date, label, period_id=None, status='closed',
                        focus_windows_enabled=False, require_editorial=False):
    """按 BD 机会视角聚合周报/月报；生产档案可要求 AI 编辑成功后才输出。"""
    period_events = [
        e for e in events
        if start_date <= (e.get('date') or '')[:10] <= end_date
    ]
    regions = sorted({e.get('region') for e in period_events if e.get('region')})
    companies = sorted({
        e.get('company_name') for e in period_events
        if e.get('is_company') and e.get('company_name')
    })

    trend_counts = {}
    trend_regions = {}
    for e in period_events:
        topic = _front_trend_topic(e)
        trend_counts[topic] = trend_counts.get(topic, 0) + 1
        region = e.get('region') or '未知'
        trend_regions.setdefault(topic, {})
        trend_regions[topic][region] = trend_regions[topic].get(region, 0) + 1

    trends = []
    for topic, count in sorted(trend_counts.items(), key=lambda x: x[1], reverse=True):
        region_map = trend_regions.get(topic, {})
        top_region = max(region_map.items(), key=lambda x: x[1])[0] if region_map else '多地区'
        trends.append({'topic': topic, 'count': count, 'region': top_region})

    top_opportunities = _build_top_opportunities(period_events, 5)
    regional_map = _build_regional_map(period_events, 6)
    actions = _build_actions(period_events, 5)
    customer_tiers = _build_customer_tiers(period_events, 6)
    focus_windows = _build_weekly_focus_windows(period_events, end_date, 6) if focus_windows_enabled else []
    monthly_trends = (
        build_monthly_trends(events, start_date, end_date, _entity_region_map(), limit=6)
        if not focus_windows_enabled else []
    )
    themes = focus_windows if focus_windows_enabled else monthly_trends
    company_changes = (
        build_company_changes(events, start_date, end_date, _entity_region_map(), limit=5)
        if not focus_windows_enabled else []
    )
    industry_changes = (
        build_industry_changes(events, start_date, end_date, _entity_region_map(), limit=5)
        if not focus_windows_enabled else []
    )
    high_count = len(select_period_high_value_events(period_events))

    # AI 编辑层：生产档案要求成功生成；单元测试可显式允许模板降级
    narrative_result = None
    editorial_title = ''
    editorial_required = bool(themes) and (focus_windows_enabled or status != 'preview')
    if focus_windows_enabled and themes:
        narrative_result = build_weekly_editorial(
            themes, period_id, cache_key=f"weekly:{period_id or label}")
    elif (not focus_windows_enabled) and monthly_trends and status != 'preview':
        narrative_result = build_monthly_editorial(
            monthly_trends, period_id, cache_key=f"monthly:{period_id or start_date[:7]}")
    if require_editorial and editorial_required and not narrative_result:
        period_type = '周报' if focus_windows_enabled else '月报'
        raise RuntimeError(f'{period_type} {period_id or label} 的 AI 编辑层生成失败，已终止页面生成，拒绝发布降级版')
    if narrative_result:
        editorial_title = narrative_result.get('editorial_title') or ''

    if period_events:
        title = f"{label}{'关注主题周报' if focus_windows_enabled else '趋势与结构月报'}"
        leading_region = regional_map[0]['region'] if regional_map else '多地区'
        if focus_windows_enabled:
            if focus_windows:
                if narrative_result:
                    summary = narrative_result['mainline']
                else:
                    leading_theme = focus_windows[0]['direction']
                    leading_region = focus_windows[0]['region']
                    summary = (
                        f"本周期从 {len(period_events)} 条合格事实中形成 {len(focus_windows)} 个主题。"
                        f"本周主线是{leading_theme}，再回到独立事实确认。"
                    )
            else:
                summary = (
                    f"本周期收录 {len(period_events)} 条合格事实，但尚未形成满足独立证据门槛的主题。"
                    f"本周只保留事件导航，不硬凑结论。"
                )
        else:
            if status == 'preview':
                summary = (
                    f"当前月尚在观察期，已收录 {len(period_events)} 条合格事件。"
                    f"趋势结论待积累完整观察周后输出，先保留事实。"
                )
            elif monthly_trends:
                if narrative_result:
                    summary = narrative_result['mainline']
                else:
                    leading_theme = monthly_trends[0]['title']
                    summary = (
                        f"本周期从 {len(period_events)} 条合格事件中形成 {len(monthly_trends)} 个跨周趋势。"
                        f"本月主线是{leading_theme}，每个判断均可回到独立证据。"
                    )
            else:
                summary = (
                    f"本周期共收录 {len(period_events)} 条事件，但尚未形成跨周、可比较的结构趋势。"
                    f"先保留事实，不用默认标签填充月报。"
                )
    else:
        title = f"{label}{'关注主题周报' if focus_windows_enabled else '趋势与结构月报'}"
        summary = "当前周期事件数量较少，先保留为观察入口。"
    date_label = start_date if start_date == end_date else f"{start_date} 至 {end_date}"
    status_label = {'preview': '观察中', 'mature': '更新中', 'open': '更新中', 'closed': '已封存'}.get(status, '已封存')

    if narrative_result:
        theme_details = narrative_result.get('themes') or {}
        theme_titles = narrative_result.get('theme_titles') or {}
        for t in themes:
            detail = theme_details.get(t.get('key'))
            if detail:
                if isinstance(detail, dict):
                    t['narrative'] = detail.get('narrative') or t.get('narrative') or ''
                    if detail.get('drivers'):
                        t['drivers'] = detail['drivers']
                    if detail.get('uncertainty'):
                        t['uncertainty'] = detail['uncertainty']
                    if detail.get('next_validation'):
                        t['next_validation'] = detail['next_validation']
                else:
                    t['narrative'] = detail
            specific_title = theme_titles.get(t.get('key'))
            if specific_title:
                t['title'] = specific_title
                t['direction'] = specific_title

    return {
        'id': period_id or f"{start_date}_{end_date}",
        'start': start_date,
        'end': end_date,
        'date_label': date_label,
        'month': start_date[:7],
        'label': label,
        'status': status,
        'status_label': status_label,
        'title': title,
        'editorial_title': editorial_title,
        'summary': summary,
        'total': len(period_events),
        'companies': len(companies),
        'regions': len(regions),
        'trends': trends or [{'topic': '暂无趋势', 'count': 0, 'region': '无'}],
        'top_opportunities': top_opportunities,
        'focus_windows': focus_windows,
        'regional_map': regional_map,
        'actions': actions,
        'customer_tiers': customer_tiers,
        'themes': themes,
        'period_themes': themes,
        'company_changes': company_changes,
        'industry_changes': industry_changes,
        'high_priority': high_count,
        'aihot_hot': _load_aihot_archive(start_date, end_date, weekly=focus_windows_enabled)
                    or _latest_aihot_items(),
    }


def build_weekly_archives(events, reference_date, require_editorial=True):
    """按自然周生成独立周报档案，已结束周固定封存，当前周更新至最新日期。"""
    grouped = {}
    reference_dt = datetime.strptime(reference_date, '%Y-%m-%d')
    for event in events:
        date_key = (event.get('date') or '')[:10]
        if not date_key:
            continue
        try:
            dt = datetime.strptime(date_key, '%Y-%m-%d')
        except ValueError:
            continue
        week_start_dt = dt - timedelta(days=dt.weekday())
        week_end_dt = week_start_dt + timedelta(days=6)
        year, week, _ = dt.isocalendar()
        key = f"{year}-W{week:02d}"
        item = grouped.setdefault(key, {
            'id': key,
            'label': f"{year}年第{week:02d}周",
            'start': week_start_dt.strftime('%Y-%m-%d'),
            'natural_end': week_end_dt.strftime('%Y-%m-%d'),
            'end': week_end_dt.strftime('%Y-%m-%d'),
        })
        if week_start_dt <= reference_dt <= week_end_dt:
            item['end'] = reference_date
    archives = []
    for item in grouped.values():
        status = 'open' if item['start'] <= reference_date <= item['natural_end'] else 'closed'
        label = item['label'] if status == 'closed' else f"{item['label']}（更新中）"
        archives.append(build_period_report(
            events, item['start'], item['end'], label, item['id'], status,
            focus_windows_enabled=True, require_editorial=require_editorial,
        ))
    archives.sort(key=lambda x: x['start'], reverse=True)
    return archives


def build_monthly_archives(events, reference_date, require_editorial=True):
    """按自然月生成独立月报档案，已结束月份固定封存，当前月更新至最新日期。"""
    months = sorted({(e.get('date') or '')[:7] for e in events if (e.get('date') or '')[:7]}, reverse=True)
    archives = []
    main_month = reference_date[:7]
    for month in months:
        start_date = f"{month}-01"
        if month == main_month:
            end_date = reference_date
            first_day = datetime.strptime(f"{month}-01", '%Y-%m-%d')
            reference_dt = datetime.strptime(reference_date, '%Y-%m-%d')
            if (reference_dt - first_day).days >= 14:
                status = 'mature'
                label = f"{month} 月度趋势更新"
            else:
                status = 'preview'
                label = f"{month} 月度观察"
        else:
            y, m = [int(x) for x in month.split('-')]
            next_month = datetime(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1)
            end_date = (next_month - timedelta(days=1)).strftime('%Y-%m-%d')
            status = 'closed'
            label = f"{month} 月报"
        archives.append(build_period_report(
            events, start_date, end_date, label, month, status,
            require_editorial=require_editorial,
        ))
    return archives


def clean_display_title(title):
    title = (title or '').strip()
    title = re.sub(r'^(背景补充|合作机会|资金流向|警示信号|中资出海|观察)[：:]\s*', '', title)
    return title


def split_judgment(text, fallback='今日非中美互联网动态更新'):
    """把长判断拆成适合头版展示的标题和正文。"""
    text = (text or '').strip()
    text = text.replace('**', '')
    if not text:
        return fallback, ''
    title = ''
    lead = ''
    sentence_parts = re.split(r'(?<=[。！？])', text, maxsplit=1)
    first_sentence = (sentence_parts[0] or text).strip()
    rest = (sentence_parts[1] if len(sentence_parts) > 1 else '').strip()
    if len(first_sentence) > 42:
        clause_parts = re.split(r'[，,；;]', first_sentence, maxsplit=1)
        title = clause_parts[0].strip()
        lead = text
    else:
        title = first_sentence
        lead = rest
    if not re.search(r'[。！？]$', title):
        title = title.rstrip('，,；;') + '。'
    return clean_display_title(title), lead


def _has_cjk(text):
    return bool(re.search(r'[\u4e00-\u9fff]', text or ''))


def _is_good_summary(summary, title, reason):
    summary = (summary or '').strip()
    if not summary or len(summary) < 8:
        return False
    if summary == (reason or '').strip():
        return False
    if summary[:25] == (title or '')[:25]:
        return False
    if not _has_cjk(summary):
        return False
    return True


def _front_trend_topic(event):
    """把后台分类转换成前台可读的趋势名，避免“背景补充”露出。"""
    region = event.get('region') or '多地区'
    event_types = event.get('event_types') or []
    event_type = event_types[0] if event_types else 'other'
    raw_topic = (event.get('trend_topic') or '').strip()
    if raw_topic and not raw_topic.startswith(('背景补充', '合作机会')) and raw_topic not in {'背景补充', '合作机会', '其他'}:
        return raw_topic
    if event_type == 'funding':
        return f'{region}资金流向'
    if event_type == 'ma':
        return f'{region}并购整合'
    if event_type == 'earnings':
        return f'{region}盈利与财报观察'
    if event_type == 'strategy':
        return f'{region}战略扩张'
    company = event.get('company_name')
    if company:
        return f'{company}连续动态'
    label = event.get('insight_label')
    if label and label not in {'背景补充', '其他'}:
        return f'{region}{label}'
    return f'{region}区域动态'


def enrich_frontend_fields(events):
    """补齐前台专用字段，让模板少做判断。"""
    for event in events:
        title = event.get('title', '')
        summary = event.get('summary_short', '')
        reason = event.get('reason', '')
        if _is_good_summary(summary, title, reason):
            display_title = summary.strip()
            original_title = title
        elif _has_cjk(reason) and reason.strip() not in {'未知', '科技动态'}:
            display_title = reason.strip()
            original_title = title
        else:
            display_title = title
            original_title = ''
        event['display_title'] = clean_display_title(display_title)
        event['original_title'] = original_title if original_title and original_title != display_title else ''
        event['front_trend_topic'] = _front_trend_topic(event)
        event['display_impact'] = '' if event.get('impact') == '未知' else event.get('impact', '')
        event['front_overview'] = _front_overview(event, title, summary, reason, display_title)
    return events


def _front_overview(event, title, summary, reason, display_title):
    """内容概要：优先 AI 扩写的 content_overview；存量数据缺省时用 summary_short 兜底，避免与标题重复。"""
    overview = (event.get('content_overview') or '').strip()
    if _is_good_summary(overview, title, reason) and overview != (summary or '').strip():
        return overview
    if _is_good_summary(summary, title, reason) and (summary or '').strip() != display_title:
        return (summary or '').strip()
    return ''


def refine_daily_headline(headline, lead, trend_groups):
    """避免把统计句当作第一屏判断。"""
    weak = bool(re.search(r'事件最多|占今日大头|共\d+条动态|覆盖\d+地区', headline or ''))
    if not weak:
        return headline, lead
    top_topic = ''
    for group in trend_groups:
        events = group.get('events') or []
        if events:
            top_topic = events[0].get('front_trend_topic') or _front_trend_topic(events[0])
            break
    if top_topic:
        return f'{top_topic}成为今日主线。', lead or headline
    return headline, lead


def build_company_cards(company_list, now_date, observation_ledger=None):
    """生成公司索引里的追踪摘要。"""
    start_7 = (datetime.strptime(now_date, '%Y-%m-%d') - timedelta(days=6)).strftime('%Y-%m-%d')
    start_30 = (datetime.strptime(now_date, '%Y-%m-%d') - timedelta(days=29)).strftime('%Y-%m-%d')
    result = []
    ledger_by_entity = {
        row.get('entity'): row
        for row in (observation_ledger or {}).get('entities') or []
        if row.get('entity')
    }
    # sector 英文码 → 中文观察方向
    _sector_label = {
        'ai_platform': 'AI 平台', 'cloud_ai_infra': '云/AI 基础设施', 'data_ai_platform': '数据/AI 平台',
        'search_ai_cloud': '搜索/AI/云', 'payment': '支付', 'payment_developer_platform': '支付开发者平台',
        'payment_wallet': '支付钱包', 'cross_border_payment': '跨境支付', 'bnpl_payment': 'BNPL 支付',
        'digital_bank': '数字银行', 'commerce': '电商', 'commerce_fintech': '电商+金融科技',
        'commerce_gaming_fintech': '电商/游戏/金融科技', 'commerce_logistics': '电商+物流',
        'commerce_payment': '电商+支付', 'commerce_saas': '电商 SaaS',
        'delivery_fintech': '配送+金融科技', 'mobility_payment': '出行+支付',
        'mobility_super_app': '出行超级应用', 'super_app_fintech': '超级应用+金融科技',
        'gaming': '游戏', 'streaming_media': '流媒体', 'social_payment': '社交+支付',
        'social_payment_gaming': '社交/支付/游戏', 'telco_digital_infra': '电信数字基础设施',
        'travel_local_services': '旅游本地服务',
        'consumer_ai_hardware': '消费电子/端侧 AI', 'ai_hardware_infra': 'AI 硬件基础设施',
        'cloud_commerce': '云+电商', 'social_ai': '社交/AI', 'cloud_ai_search': '云/AI/搜索',
        'ev_ai_autonomy': '新能源车/自动驾驶', 'ai_platform_content': 'AI 平台+内容',
        'cloud_ai_commerce': '云/AI/电商', 'social_ai_gaming': '社交/AI/游戏',
        'ai_search_cloud': 'AI/搜索/云', 'local_services': '本地服务',
    }
    now = datetime.strptime(now_date, '%Y-%m-%d')
    # 公司权重：核心战略公司（must/strategic）的信号比普通观察对象更值得关注
    _company_weight = {
        'must': 1.3,
        'strategic': 1.2,
        'experiment': 1.0,
        'mention': 1.0,
        'watch': 1.0,
    }
    for company in company_list:
        events = company.get('events') or []
        events = sorted(events, key=lambda x: (x.get('date', ''), x.get('score', 0)), reverse=True)
        recent_7 = [e for e in events if (e.get('date') or '')[:10] >= start_7]
        recent_30 = [e for e in events if (e.get('date') or '')[:10] >= start_30]
        quality_events = [event for event in recent_30 if is_main_view_event(event) or is_company_quality_signal(event)]

        def _signal_worth(event):
            """一条事件是否值得作为「最近值得关注动态」展示（排除平凡信号）。"""
            signal = event.get('insight_label') or '观察'
            return signal not in {'观察', '背景补充', '其他', '待分析'}

        def _attention_sort_key(event):
            """按 事件重要性 × 公司权重 × 时间衰减 排序，取值得关注的那条。"""
            attention = float(event.get('attention_score') or 0)
            weight = _company_weight.get(company.get('portfolio_tier'), 1.0)
            date_str = (event.get('date') or '')[:10]
            try:
                days_old = (now - datetime.strptime(date_str, '%Y-%m-%d')).days
            except ValueError:
                days_old = 30
            decay = 0.7 ** max(days_old, 0)  # 今天=1.0，每过约2天衰减到半
            return (attention / 100.0) * weight * decay

        # 核心动态：近30天内信号值得关注且综合分最高的一条；否则回退到最新一条
        worthy = [e for e in recent_30 if _signal_worth(e)]
        featured = max(worthy, key=_attention_sort_key) if worthy else None
        latest = featured or (events[0] if events else {})
        # 公司索引标题用中文：优先中文 summary_short，其次 content_overview，再次 reason，最后英文 title
        def _pick_cn_title(event):
            for key in ('summary_short', 'content_overview', 'reason'):
                text = (event.get(key) or '').strip()
                if text and _has_cjk(text) and text != '未知' and text != '科技动态':
                    return text
            return (event.get('title') or '').strip()
        _featured_title = _pick_cn_title(latest)
        latest_title = clean_display_title(_featured_title or '暂无近期事件')
        signal = latest.get('insight_label') or '观察'
        if signal in {'背景补充', '其他', '待分析'}:
            signal = '观察'
        observation = ledger_by_entity.get(company.get('name')) or {}
        point_rows = observation.get('observation_points') or []
        connected_points = sum(
            1 for row in point_rows
            if row.get('status') not in {'pending', 'unverified'}
        )
        total_points = len(point_rows)
        observation_status = observation.get('status') or 'unverified'
        activity_status = observation.get('activity_status') or (
            'active' if observation_status == 'active' else 'unknown'
        )
        # 覆盖状态以 status 为主：采集闭环(active/quiet)即视为正常，
        # coverage_status 仅在其明确表示降级(failed/partial)时才覆盖。
        coverage_status = observation.get('coverage_status') or observation_status
        if observation_status in {'active', 'quiet', 'changed_below_threshold'} and coverage_status in {'pending', 'unverified'}:
            coverage_status = observation_status
        observation_label = observation.get('status_label') or '状态待确认'
        if observation_status == 'active':
            observation_detail = f"近30天形成 {observation.get('qualified_event_count_30d', 0)} 条合格事件"
            if coverage_status in {'partial', 'pending', 'unverified'}:
                observation_detail += '，直接观察点仍待完善'
        elif observation_status == 'quiet':
            observation_detail = '采集正常，近期没有显著组织行为变化'
        elif observation_status == 'changed_below_threshold':
            observation_detail = f"近7天发现 {observation.get('raw_change_count_7d', 0)} 次变化，尚未升格为情报"
        elif observation_status == 'failed':
            observation_detail = '最近一次采集失败，需要修复接入'
        elif observation_status == 'partial':
            point_type_labels = {
                'jobs': '招聘',
                'changelog': '更新日志',
                'product_update': '产品更新',
                'newsroom': '新闻中心',
                'ir': '投资者关系',
                'developer_docs': '开发者文档',
            }
            notable = [
                f"{point_type_labels.get(row.get('point_type'), row.get('point_type') or '观察点')}：{row.get('status_label')}"
                for row in point_rows
                if row.get('status') not in {'pending', 'unverified'}
            ]
            if notable:
                observation_detail = '；'.join(notable[:2])
            elif any(row.get('status') == 'unverified' for row in point_rows):
                observation_detail = '已执行检查，但旧记录不足以确认采集是否成功'
            else:
                observation_detail = f"已有 {connected_points}/{total_points} 个观察点产生运行证据"
        elif observation_status == 'pending':
            observation_detail = '观察对象已登记，采集器尚未接入'
        elif company.get('portfolio_tier') == 'mention' and company.get('decision_use'):
            observation_detail = company.get('decision_use')
        else:
            observation_detail = '历史运行记录不足，等待下一次采集确认'
        # 三态归一：NORMAL 正常 / PARTIAL_DATA 部分覆盖 / NO_DATA 无数据。
        # coverage_degraded 仅记录原始采集失效(failed)，用于前台"数据可能不完整"角标；
        # pending/partial 属过渡态不打扰读者，线下由运维侧报告跟踪。
        _raw_coverage = coverage_status
        coverage_status = {
            'active': 'NORMAL',
            'quiet': 'NORMAL',
            'changed_below_threshold': 'NORMAL',
            'partial': 'PARTIAL_DATA',
            'pending': 'NO_DATA',
            'unverified': 'NO_DATA',
            'failed': 'NO_DATA',
        }.get(coverage_status, 'NORMAL')
        featured_overview = ''
        if latest:
            _title = latest.get('display_title') or latest.get('summary_short') or latest.get('title') or ''
            _summary = latest.get('summary_short') or ''
            _reason = latest.get('reason') or ''
            featured_overview = _front_overview(latest, _title, _summary, _reason, _title)
        result.append({
            **company,
            'direction': _sector_label.get(company.get('sector') or '', ''),
            'recent_7': len(recent_7),
            'recent_30': len(recent_30),
            'quality_30': len(quality_events),
            'latest_title': latest_title,
            'latest_date': (latest.get('date') or '')[:10],
            'featured_title': latest_title,
            'featured_overview': featured_overview,
            'featured_date': (latest.get('date') or '')[:10],
            'attention_score': float(latest.get('attention_score') or 0),
            'signal': signal,
            'observation_status': observation_status,
            'observation_label': observation_label,
            'activity_status': activity_status,
            'activity_label': {
                'active': '近期有动作',
                'candidate': '有候选，待晋级',
                'changed_below_threshold': '有变化，未达门槛',
                'quiet': '近期安静',
                'unknown': '活动未知',
            }.get(activity_status, '活动未知'),
            'coverage_label': {
                'NORMAL': '正常',
                'PARTIAL_DATA': '部分覆盖',
                'NO_DATA': '数据存疑',
            }.get(coverage_status, '正常'),
            'observation_detail': observation_detail,
            'coverage_status': coverage_status,
            'coverage_degraded': _raw_coverage == 'failed',
            'last_checked_at': (observation.get('last_checked_at') or '')[:10],
            'connected_points': connected_points,
            'total_points': total_points,
        })
    return result

def group_company_cards(company_list):
    """按预设区域顺序组织公司索引，避免全局排序后用户找不到区域。"""
    grouped = []
    for region in REGION_ORDER:
        companies = [c for c in company_list if c.get('region') == region]
        if not companies:
            continue
        companies.sort(key=lambda x: (x.get('count', 0), x.get('recent_30', 0), x.get('recent_7', 0)), reverse=True)
        grouped.append({
            'region': region,
            'total': len(companies),
            'active': sum(1 for c in companies if c.get('activity_status') == 'active'),
            'observed': sum(1 for c in companies if c.get('coverage_status') not in {'pending', 'unverified'}),
            'recent_30': sum(c.get('recent_30', 0) for c in companies),
            'quality_30': sum(c.get('quality_30', 0) for c in companies),
            'companies': companies,
        })
    return grouped

def load_site_updates():
    """读取网站更新日志。"""
    path = os.path.join('data', 'site_updates.json')
    fallback = [{
        'date': _cn_today(),
        'version': 'V0.1',
        'type': '系统',
        'status': '已上线',
        'title': '网站初始化',
        'summary': '全球互联网百晓生开始自动生成情报简报。',
        'changes': ['自动采集事件', '生成静态情报页面'],
    }]
    if not os.path.exists(path):
        return fallback
    try:
        with open(path, 'r', encoding='utf-8') as f:
            updates = json.load(f)
    except (json.JSONDecodeError, OSError):
        return fallback
    if not isinstance(updates, list):
        return fallback
    cleaned = []
    for item in updates:
        if not isinstance(item, dict):
            continue
        changes = item.get('changes') if isinstance(item.get('changes'), list) else []
        date_value = item.get('date') or ''
        cleaned.append({
            'date': date_value,
            'version': item.get('version') or '',
            'type': item.get('type') or '更新',
            'status': item.get('status') or '已记录',
            'title': item.get('title') or '未命名更新',
            'summary': item.get('summary') or '',
            'changes': [str(c) for c in changes if str(c).strip()],
        })
    return sorted(cleaned or fallback, key=lambda x: x.get('date', ''), reverse=True)


def load_entity_observation_ledger():
    path = os.path.join('data', 'entity_observation_ledger.json')
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_model_leaderboard():
    """读取 AIHOT 模型榜数据（由 scripts/fetch_model_leaderboard.py 生成）。"""
    path = os.path.join('data', 'model_leaderboard.json')
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def load_aihot_hot():
    """读取 AIHOT 热点榜数据（由 scripts/fetch_aihot_hot.py 生成）。"""
    path = os.path.join('data', 'aihot_hot.json')
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or not data.get('items'):
            return None
        return data
    except (OSError, json.JSONDecodeError):
        return None


def _clean_hot_title(item, max_len=60):
    """AIHOT 标题去时间戳/序号残留，取干净的主标题。"""
    title = (item.get('title') or item.get('list_title') or '').strip()
    if not title:
        return ''
    if len(title) > max_len:
        title = title[:max_len] + '…'
    return title

CHINESE_WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日']


def _quality_main_events(main_events):
    """Build the quality-filtered main batch used as a fallback display list."""
    seen_titles = set()
    deduped = []
    for e in main_events:
        norm = re.sub(r'[^\w]', '', e.get('title', '').lower())
        if norm in seen_titles or len(norm) <= 10:
            continue
        seen_titles.add(norm)

        if not select_main_list_events([e]):
            continue

        deduped.append(e)

    deduped.sort(key=signal_sort_key, reverse=True)
    return deduped


def build_review_events(today_events, limit=12):
    """Build a deduped review list from the same display batch as high-value events."""
    review_events = select_review_events(today_events, limit=None)
    review_events = dedupe_display_events(review_events)
    review_events.sort(key=signal_sort_key, reverse=True)
    return review_events[:limit]


def build_display_context():
    """Return the same final event model used by the HTML dashboard and RSS feed."""
    events = load_events()
    sorted_dates = sorted(events.keys(), reverse=True)

    # 主tab：最近一次有内容的采集批次（回退到昨天兜底）
    # 历史tab：除主tab批次之外的所有日期
    today_str = _cn_today()
    main_date = None
    main_events = []

    # 找最近一个有内容的批次
    for d in sorted_dates:
        evs = events.get(d, [])
        if evs:
            main_date = d
            main_events = evs
            break

    # 今天批次为空 → 回退到昨天
    if main_date == today_str and not main_events:
        for d in sorted_dates:
            if d != today_str:
                evs = events.get(d, [])
                if evs:
                    main_date = d
                    main_events = evs
                    break

    all_feed = _quality_main_events(main_events)

    # 公司动态单独处理
    company_events, generic_events = split_company_events(events)

    # 收集每家公司所有事件（时间窗口内，不过滤数量上限）
    company_by_company = {}
    qualified_company_events = select_company_quality_events(company_events)
    for e in qualified_company_events:
        name = e.get('company_name', '其他')
        company_by_company.setdefault(name, []).append(e)

    # 按事件数量排序，有事件的排前面
    preset_company_list = []
    entity_pool = load_entity_pool()
    portfolio = _portfolio_by_entity(entity_pool)
    entity_timelines = build_entity_event_timelines(events, entity_pool.get('entities') or [])
    for entity in entity_pool.get('entities') or []:
        company_name = entity.get('name') or ''
        evs = entity_timelines.get(entity.get('id') or company_name, [])
        preset_company_list.append({
            'entity_id': entity.get('id') or '',
            'name': company_name,
            'region': entity.get('region') or '全球',
            'sector': entity.get('sector') or '',
            'priority': entity.get('priority') or 'watch',
            **portfolio.get(entity.get('id'), {'portfolio_tier': 'experiment', 'decision_use': ''}),
            'count': len(evs),
            'events': evs,
        })

    # 按事件数量排序，有事件的排前面
    preset_company_list.sort(key=lambda x: x['count'], reverse=True)

    # 阶段2：实体池拆分 Watchlist / Mention。
    # Watchlist = entity_pool（人工关注对象）；Mention = 监控雷达（COMPANY_SOURCES）
    # 中未纳入 Watchlist 的公司 + 事件中自动发现的公司。07-31 实体池重构把 14 家
    # 被监控公司（Zalando/Allegro/Trendyol/Kaspi.kz/中资7家等）从索引里丢掉，
    # 此处让"在监控"的公司持久出现：有近 7 天合格事件就带事件，没有就显示
    # "监控中"状态而非直接消失。
    try:
        from fetch_news import COMPANY_ALIASES as _RADAR_ALIASES
        from fetch_news import COMPANY_SOURCES as _RADAR_SOURCES
    except Exception:
        _RADAR_ALIASES = {}
        _RADAR_SOURCES = []
    watchlist_name_lower = {
        (entity.get('name') or '').lower()
        for entity in (entity_pool.get('entities') or [])
        if entity.get('name')
    }
    watchlist_alias_lower = {
        alias.lower()
        for entity in (entity_pool.get('entities') or [])
        for alias in (entity.get('aliases') or [])
        if alias
    }
    watch_all = watchlist_name_lower | watchlist_alias_lower

    def _mention_events_for(radar_name):
        """收集公司近 7 天合格事件：公司名 + 别名命中 company_by_company。"""
        names = [radar_name] + list(_RADAR_ALIASES.get(radar_name, []))
        found = []
        for n in names:
            evs = company_by_company.get(n)
            if evs:
                found.extend(evs)
        seen = set()
        dedup = []
        for e in found:
            key = e.get('url') or f"{e.get('date')}|{e.get('title')}"
            if key in seen:
                continue
            seen.add(key)
            dedup.append(e)
        return dedup

    mention_names = set()
    # 1) 监控雷达公司：持久卡片
    for _cfg in _RADAR_SOURCES:
        radar_name = _cfg.get('name') or ''
        if not radar_name or radar_name.lower() in watch_all:
            continue
        evs = _mention_events_for(radar_name)
        mention_names.add(radar_name.lower())
        preset_company_list.append({
            'entity_id': '',
            'name': radar_name,
            'region': _cfg.get('region') or '全球',
            'sector': '',
            'priority': 'mention',
            'portfolio_tier': 'mention',
            'decision_use': '监控中：公司雷达覆盖，未纳入人工观察清单',
            'count': len(evs),
            'events': evs,
        })
    # 2) 自动发现：事件中出现、既不在 Watchlist 也不在雷达配置的公司
    for name, evs in company_by_company.items():
        if not name or name == '其他':
            continue
        key = name.lower()
        if key in watch_all or key in mention_names:
            continue
        region_counts = {}
        for e in evs:
            r = e.get('region') or ''
            if r:
                region_counts[r] = region_counts.get(r, 0) + 1
        region = max(region_counts, key=region_counts.get) if region_counts else '全球'
        preset_company_list.append({
            'entity_id': '',
            'name': name,
            'region': region,
            'sector': '',
            'priority': 'mention',
            'portfolio_tier': 'mention',
            'decision_use': '自动发现：出现在公司源事件中，未纳入人工观察清单',
            'count': len(evs),
            'events': evs,
        })

    # 全部事件 = 通用热点 + 公司动态（筛选后），统一按时间排序
    company_events_filtered = [e for evs in company_by_company.values() for e in evs]
    all_events_for_list = list(generic_events) + company_events_filtered
    all_events_for_list.sort(key=signal_sort_key, reverse=True)
    enrich_frontend_fields(all_events_for_list)
    all_events_for_list = dedupe_display_events(all_events_for_list)
    mature_main_date, latest_data_date, latest_visible_count, batch_notice = select_mature_main_date(sorted_dates, all_events_for_list, events)
    period_reference_date = latest_data_date or main_date or today_str
    if mature_main_date:
        main_date = mature_main_date
        main_events = events.get(main_date, [])
        all_feed = _quality_main_events(main_events)

    # 今日要点 = what'll be displayed — 从 all_events_for_list 中取今天的可展示事件
    raw_today_events = [
        e for e in all_events_for_list
        if (e.get('date') or '')[:10] == main_date
    ]
    today_events = select_homepage_events(all_events_for_list, main_date, all_feed)

    return {
        'events': events,
        'sorted_dates': sorted_dates,
        'today_str': today_str,
        'main_date': main_date,
        'main_events': main_events,
        'all_feed': all_feed,
        'company_events': company_events,
        'generic_events': generic_events,
        'company_by_company': company_by_company,
        'company_events_filtered': company_events_filtered,
        'preset_company_list': preset_company_list,
        'all_events_for_list': all_events_for_list,
        'today_events': today_events,
        'raw_today_events': raw_today_events,
        'latest_data_date': latest_data_date,
        'latest_visible_count': latest_visible_count,
        'batch_notice': batch_notice,
        'period_reference_date': period_reference_date,
        'entity_observation_ledger': load_entity_observation_ledger(),
    }


def generate_html(force=False, preview_mode=False):
    context = build_display_context()
    events = context['events']
    sorted_dates = context['sorted_dates']
    today_str = context['today_str']
    main_date = context['main_date']
    main_events = context['main_events']
    all_feed = context['all_feed']
    company_events = context['company_events']
    generic_events = context['generic_events']
    company_events_filtered = context['company_events_filtered']
    preset_company_list = context['preset_company_list']
    all_events_for_list = context['all_events_for_list']
    today_events = context['today_events']
    raw_today_events = context['raw_today_events']
    latest_data_date = context['latest_data_date']
    latest_visible_count = context['latest_visible_count']
    batch_notice = context['batch_notice']
    period_reference_date = context['period_reference_date']
    entity_observation_ledger = context['entity_observation_ledger']

    preset_company_list = build_company_cards(preset_company_list, main_date, entity_observation_ledger)
    company_groups = group_company_cards(preset_company_list)

    # 历史tab：除主tab批次之外的所有有内容日期
    history_dates = [d for d in sorted_dates if d != main_date]
    history = [(d, events.get(d, [])) for d in history_dates if events.get(d, [])]

    signals = get_signal_events(events)
    # ⚠️ 关键：weekly 必须从 today_events 计数，不是 all_feed
    # all_feed 过滤了 other 类型和低分事件，但页面上展示的是 today_events
    # 两个数据源不一致导致"共0条动态"而实际有 9 条的矛盾
    weekly = build_weekly_summary(today_events, signals, main_events, events, summary_date=main_date)
    # 公司动态也加入周报摘要
    weekly['company_count'] = len(company_events_filtered)
    weekly['company_list'] = preset_company_list

    trend_groups = build_trend_groups(today_events)
    repair_events = build_review_events(raw_today_events)
    daily_trend_signals = weekly.get('top3', [])
    signal_clusters = keep_focus_date_clusters(
        build_signal_clusters(all_events_for_list, main_date, limit=12)
    )
    narrative = build_narrative(signal_clusters, fallback_events=today_events)
    signal_clusters = strip_cluster_event_payloads(narrative.get('clusters', []))
    evidence_events = narrative.get('evidence_events') or today_events[:5]
    daily_event_groups = build_daily_event_groups(today_events)
    daily_headline, daily_lead = build_daily_navigation_copy(daily_event_groups)
    daily_trend_judgment = daily_lead
    total_stories = len(today_events)
    dt = datetime.strptime(today_str, '%Y-%m-%d')
    vol_label = f"VOL.{today_str}"
    cn_date = f"{dt.year}年{dt.month}月{dt.day}日 星期{CHINESE_WEEKDAYS[dt.weekday()]}"

    # 全部事件按日期分组
    date_grouped_events = group_events_by_date(all_events_for_list)
    date_event_counts = {group['date']: len(group['events']) for group in date_grouped_events}

    # 预计算各日期面板数据（供 JS 翻页切换）
    date_panels = {}
    available_dates = []
    for d in sorted_dates:
        raw_day_evs = [e for e in all_events_for_list if (e.get('date') or '')[:10] == d]
        day_evs = select_homepage_events_for_date(all_events_for_list, d)
        if not day_evs and not raw_day_evs:
            continue
        available_dates.append(d)
        date_panels[d] = build_date_panel(
            d,
            day_evs,
            events,
            raw_day_evs,
            cluster_events=all_events_for_list,
        )
        date_panels[d]['event_list_count'] = date_event_counts.get(d, len(raw_day_evs))

    # 今日页平铺事件：精选→重点→观察→待确认（新界面语言）
    merged_events = []
    for group in daily_event_groups:
        for ev in group['events']:
            merged_events.append({'ev': ev, 'tag': group['label'], 'pri': group['key']})
    for ev in repair_events:
        merged_events.append({'ev': ev, 'tag': '待确认', 'pri': 'review'})
    summary_parts = [f"{g['label']} {len(g['events'])}" for g in daily_event_groups if g['events']]
    if repair_events:
        summary_parts.append(f"待确认 {len(repair_events)}")
    summary_counts = ' · '.join(summary_parts)

    # 历史导航：天数 + 当日全部事件数（与"全部事件"面板展开后一致）
    history_dates = [d for d in available_dates if d != main_date][:5]
    history_counts = {
        d: len([e for e in all_events_for_list if (e.get('date') or '')[:10] == d])
        for d in history_dates
    }
    history_total = max(0, len(available_dates) - 1)

    # 周报/月报聚合用全量事件（含所有历史批次），不用展示管线的过滤视图——
    # 展示视图只保留主日期附近窗口，跨周证据会被砍到不足以判断变化。
    full_events_for_period = [
        e for date_key, evs in events.items()
        for e in (evs if isinstance(evs, list) else [])
    ]
    weekly_archives = build_weekly_archives(full_events_for_period, period_reference_date,
                                            require_editorial=not preview_mode)
    monthly_archives = build_monthly_archives(full_events_for_period, period_reference_date,
                                              require_editorial=not preview_mode)
    weekly_report = weekly_archives[0] if weekly_archives else build_period_report([], period_reference_date, period_reference_date, '本周', 'empty', 'open')
    monthly_report = monthly_archives[0] if monthly_archives else build_period_report([], period_reference_date, period_reference_date, '本月', 'empty', 'open')
    site_updates = load_site_updates()
    model_leaderboard = load_model_leaderboard()
    aihot_hot = _aihot_items_to_list(load_aihot_hot()) or _latest_aihot_items()
    update_time = f"最新采集 {period_reference_date}｜展示 {main_date} 成熟批次"

    env = Environment(autoescape=select_autoescape(['html', 'htm', 'xml']))
    template = env.from_string(open('scripts/template.html', 'r', encoding='utf-8').read())
    html = template.render(
        weekly=weekly,
        weekly_report=weekly_report,
        monthly_report=monthly_report,
        weekly_archives=weekly_archives,
        monthly_archives=monthly_archives,
        all_feed=all_feed,
        all_events_for_list=all_events_for_list,
        date_grouped_events=date_grouped_events,
        history=history,
        main_date=main_date,
        company_events=company_events,
        company_list=preset_company_list,
        company_groups=company_groups,
        update_time=update_time,
        trend_groups=trend_groups,
        repair_events=repair_events,
        daily_trend_judgment=daily_trend_judgment,
        daily_headline=daily_headline,
        daily_lead=daily_lead,
        daily_trend_signals=daily_trend_signals,
        signal_clusters=signal_clusters,
        evidence_events=evidence_events,
        daily_event_groups=daily_event_groups,
        merged_events=merged_events,
        summary_counts=summary_counts,
        history_dates=history_dates,
        history_counts=history_counts,
        history_total=history_total,
        narrative=narrative,
        total_stories=total_stories,
        vol_label=vol_label,
        cn_date=cn_date,
        date_panels=date_panels,
        date_event_counts=date_event_counts,
        available_dates=available_dates,
        latest_data_date=latest_data_date,
        latest_visible_count=latest_visible_count,
        batch_notice=batch_notice,
        site_updates=site_updates,
        model_leaderboard=model_leaderboard,
        aihot_hot=aihot_hot,
        feedback_endpoint=os.getenv('FEEDBACK_ENDPOINT', ''),
    )
    html = '\n'.join(line.rstrip() for line in html.splitlines()) + '\n'

    os.makedirs('docs', exist_ok=True)
    index_path = 'docs/preview.html' if preview_mode else 'docs/index.html'

    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(html)

    mode = '预览' if preview_mode else '生产'
    print(f"OK | {mode}模式 | 通用{len(generic_events)}条 | 公司{len(company_events)}条 | {len(history)}天往期")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='生成全球互联网动态情报站 HTML')
    parser.add_argument('--force', action='store_true', help='强制重写 index.html（跳过内容对比）')
    parser.add_argument('--preview', action='store_true', help='生成本地预览文件 preview.html（不覆盖 index.html）')
    args = parser.parse_args()

    if args.preview:
        # 预览模式：生成到 preview.html
        generate_html(preview_mode=True)
    else:
        # 默认模式：生成到 index.html
        generate_html(force=args.force)
