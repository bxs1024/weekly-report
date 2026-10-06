"""打分与格式化：金额解析、事件打分、中国资本判定。从 generate_html 原文搬出（P4 阶段4）。"""

import re
from datetime import datetime, timedelta, timezone

try:
    from content.util import SHANGHAI_TZ, _cn_now, _cn_today
except ImportError:
    from scripts.content.util import SHANGHAI_TZ, _cn_now, _cn_today

# _cn_now / _cn_today 原本在 generate_html 里有一份独立实现，与 content/util.py
# 逐字节相同，此处直接复用下层那份，不再保留副本。SHANGHAI_TZ 同理。

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
