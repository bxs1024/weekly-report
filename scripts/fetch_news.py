"""
全球互联网动态情报站 — 数据采集
目标：融资 | 并购 | 财报披露 | 重大战略 — 发现 ICT 合作机会点
"""

import json, os, time, re, hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
import feedparser

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import warnings; warnings.filterwarnings('ignore')
import requests
from bs4 import BeautifulSoup

# DeepSeek/豆包均为国内 API，直连即可；trust_env=False 忽略系统代理（含 ALL_PROXY），
# 避免依赖 socks 库且更快。所有 AI 通道共用此 session（新闻抓取仍走系统代理，不受影响）。
_LLM_SESSION = requests.Session()
_LLM_SESSION.trust_env = False

try:
    from zoneinfo import ZoneInfo
    SHANGHAI_TZ = ZoneInfo('Asia/Shanghai')
except Exception:
    SHANGHAI_TZ = timezone(timedelta(hours=8))

try:
    from analysis_quality import annotate_event_quality, summarize_quality
    from event_dates import apply_event_date_metadata, publication_metadata
    from event_contract import prepare_event_contract
    from event_value import classify_bd_priority, follow_up_window_for_priority
    from run_metrics import write_run_metrics
    from scope_gate import apply_scope_contract
    from internet_relevance import assess_internet_relevance
except ImportError:
    from scripts.analysis_quality import annotate_event_quality, summarize_quality
    from scripts.event_dates import apply_event_date_metadata, publication_metadata
    from scripts.event_contract import prepare_event_contract
    from scripts.event_value import classify_bd_priority, follow_up_window_for_priority
    from scripts.run_metrics import write_run_metrics
    from scripts.scope_gate import apply_scope_contract
    from scripts.internet_relevance import assess_internet_relevance

# ============================================================
# 并行采集优化：aiohttp
# ============================================================
try:
    import aiohttp
    import asyncio
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False
    print("安装 aiohttp（并行采集）...")
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "aiohttp", "-q"])
    import aiohttp
    import asyncio
    HAS_AIOHTTP = True

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122.0 Safari/537.36',
    'Accept': 'application/rss+xml,application/atom+xml,application/xml;q=0.9,text/html;q=0.8,*/*;q=0.7',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.google.com/',
}

REQUEST_DELAY = 1.2  # 避免被封（仅用于重试，非采集）
REQUEST_TIMEOUT = 8   # 单次请求超时（秒），降级提速


def _cn_now():
    return datetime.now(SHANGHAI_TZ)


def _cn_today():
    return _cn_now().strftime('%Y-%m-%d')


def _parse_date(value):
    try:
        return datetime.strptime((value or '')[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _recent_article_date(article_date, days=2):
    parsed = _parse_date(article_date)
    if not parsed:
        return True
    cutoff = (_cn_now() - timedelta(days=days)).date()
    return parsed >= cutoff

# ============================================================
# ���源：重点标注是否为融资专属源
# ============================================================

RSS_SOURCES = [
    # --- 欧洲：融资专业源优先 ---
    {'name': 'TechCrunch',       'url': 'https://techcrunch.com/feed/',                  'source': 'TechCrunch',    'region': '全球', 'priority': 3, 'source_tier': 'L2 垂直交易源', 'source_role': 'venture_media', 'max_scan': 20, 'max': 8},
    {'name': 'TechCrunch VC',   'url': 'https://techcrunch.com/category/venture/feed/', 'source': 'TechCrunch',    'region': '全球', 'priority': 3, 'source_tier': 'L2 垂直交易源', 'source_role': 'venture_media', 'max_scan': 20, 'max': 8},
    {'name': 'Tech.eu',          'url': 'https://tech.eu/feed/',                         'source': 'Tech.eu',       'region': '欧洲', 'priority': 3, 'source_tier': 'L2 垂直交易源', 'source_role': 'venture_media', 'max_scan': 20, 'max': 8},
    {'name': 'UKTN',             'url': 'https://www.uktech.news/feed',                  'source': 'UKTN',          'region': '欧洲', 'priority': 3, 'source_tier': 'L2 垂直交易源', 'source_role': 'venture_media', 'max_scan': 20, 'max': 8},
    {'name': 'EU-Startups',      'url': 'https://www.eu-startups.com/feed/',             'source': 'EU-Startups',   'region': '欧洲', 'priority': 3, 'source_tier': 'L2 垂直交易源', 'source_role': 'venture_media', 'max_scan': 20, 'max': 8},
    {'name': 'The Recursive',    'url': 'https://therecursive.com/feed/',                'source': 'The Recursive', 'region': '欧洲', 'priority': 2, 'source_tier': 'L3 区域生态源', 'source_role': 'regional_ecosystem', 'max_scan': 20, 'max': 6},
    {'name': 'The Next Web',     'url': 'https://thenextweb.com/feed/',                  'source': 'The Next Web',  'region': '欧洲', 'priority': 2, 'source_tier': 'L3 区域生态源', 'source_role': 'regional_ecosystem', 'max_scan': 20, 'max': 6},
    # Sifted 已移除：Cloudflare 全面拦截，无法绕过
    # --- 亚太：融资专业源 ---
    {'name': 'Tech in Asia',     'url': 'https://www.techinasia.com/feed/',              'source': 'Tech in Asia',  'region': '亚太', 'priority': 3, 'source_tier': 'L2 垂直交易源', 'source_role': 'venture_media', 'max_scan': 24, 'max': 8},
    {'name': 'Inc42',            'url': 'https://inc42.com/feed/',                       'source': 'Inc42',         'region': '亚太', 'priority': 3, 'source_tier': 'L2 垂直交易源', 'source_role': 'venture_media', 'max_scan': 24, 'max': 8},
    {'name': 'TechWire Asia',    'url': 'https://techwireasia.com/feed/',               'source': 'TechWire Asia', 'region': '亚太', 'priority': 2, 'source_tier': 'L3 区域生态源', 'source_role': 'regional_ecosystem', 'max_scan': 20, 'max': 6},
    # DealStreetAsia RSS 已停用（"Temporarily Disabled"），改用 HTML 降级采集
    # e27 已移除：Cloudflare 全面拦截，无法绕过
    # Google News RSS 不可用：链接为 Google 内部跳转，非原始来源
    # --- 垂直赛道精品源：只保留高信号内容，避免泛资讯噪声 ---
    {'name': 'GamesIndustry.biz', 'url': 'https://www.gamesindustry.biz/rss',            'source': 'GamesIndustry.biz', 'region': '全球', 'priority': 2, 'source_tier': 'L4 垂直赛道精品源', 'source_role': 'industry_vertical', 'vertical': '游戏', 'scope_industries': ['gaming_content'], 'max_scan': 16, 'max': 4, 'signal_only': True},
    {'name': 'PocketGamer.biz',   'url': 'https://www.pocketgamer.biz/rss/',             'source': 'PocketGamer.biz', 'region': '全球', 'priority': 2, 'source_tier': 'L4 垂直赛道精品源', 'source_role': 'industry_vertical', 'vertical': '游戏', 'scope_industries': ['gaming_content'], 'max_scan': 16, 'max': 4, 'signal_only': True},
    {'name': 'Fintech News Singapore', 'url': 'https://fintechnews.sg/feed/',            'source': 'Fintech News Singapore', 'region': '亚太', 'priority': 2, 'source_tier': 'L4 垂直赛道精品源', 'source_role': 'industry_vertical', 'vertical': 'Fintech/支付', 'scope_industries': ['payments'], 'max_scan': 16, 'max': 4, 'signal_only': True},
    {'name': 'Finextra Payments', 'url': 'https://www.finextra.com/rss/channel.aspx?channel=payments', 'source': 'Finextra', 'region': '全球', 'priority': 2, 'source_tier': 'L4 垂直赛道精品源', 'source_role': 'industry_vertical', 'vertical': 'Fintech/支付', 'scope_industries': ['payments'], 'max_scan': 20, 'max': 4, 'signal_only': True},
    {'name': 'Payments Dive', 'url': 'https://www.paymentsdive.com/feeds/news/',         'source': 'Payments Dive', 'region': '全球', 'priority': 2, 'source_tier': 'L4 垂直赛道精品源', 'source_role': 'industry_vertical', 'vertical': 'Fintech/支付', 'scope_industries': ['payments'], 'max_scan': 12, 'max': 3, 'signal_only': True},
    {'name': 'EcommerceBytes',    'url': 'https://www.ecommercebytes.com/feed/',         'source': 'EcommerceBytes', 'region': '全球', 'priority': 2, 'source_tier': 'L4 垂直赛道精品源', 'source_role': 'industry_vertical', 'vertical': '电商', 'scope_industries': ['commerce'], 'max_scan': 16, 'max': 4, 'signal_only': True},
    {'name': 'Retail Dive', 'url': 'https://www.retaildive.com/feeds/news/',             'source': 'Retail Dive', 'region': '全球', 'priority': 2, 'source_tier': 'L4 垂直赛道精品源', 'source_role': 'industry_vertical', 'vertical': '零售', 'scope_industries': [], 'max_scan': 12, 'max': 3, 'signal_only': True},
    {'name': 'Mobile World Live', 'url': 'https://www.mobileworldlive.com/feed/',         'source': 'Mobile World Live', 'region': '全球', 'priority': 2, 'source_tier': 'L4 垂直赛道精品源', 'source_role': 'industry_vertical', 'vertical': '文娱社交/移动生态', 'scope_industries': ['ads_social', 'cloud_saas_developer'], 'max_scan': 16, 'max': 4, 'signal_only': True},
    {'name': 'Social Media Today', 'url': 'https://www.socialmediatoday.com/feeds/news/', 'source': 'Social Media Today', 'region': '全球', 'priority': 2, 'source_tier': 'L4 垂直赛道精品源', 'source_role': 'industry_vertical', 'vertical': '社交平台', 'scope_industries': ['ads_social'], 'max_scan': 12, 'max': 3, 'signal_only': True},
    {'name': 'Mobile Marketing Magazine', 'url': 'https://mobilemarketingmagazine.com/feed/', 'source': 'Mobile Marketing Magazine', 'region': '全球', 'priority': 2, 'source_tier': 'L4 垂直赛道精品源', 'source_role': 'industry_vertical', 'vertical': '移动生态/广告', 'scope_industries': ['ads_social'], 'max_scan': 12, 'max': 3, 'signal_only': True},
    # --- 中东/非洲 ---
    {'name': 'WAMDA',           'url': 'https://www.wamda.com/feed',                     'source': 'WAMDA',         'region': '中东', 'priority': 3, 'source_tier': 'L2 垂直交易源', 'source_role': 'venture_media', 'max_scan': 20, 'max': 8},
    {'name': 'MENAbytes',        'url': 'https://www.menabytes.com/feed/',               'source': 'MENAbytes',     'region': '中东', 'priority': 3, 'source_tier': 'L2 垂直交易源', 'source_role': 'venture_media', 'max_scan': 20, 'max': 8},
    {'name': 'TechCabal',        'url': 'https://techcabal.com/feed',                   'source': 'TechCabal',     'region': '非洲', 'priority': 2, 'source_tier': 'L3 区域生态源', 'source_role': 'regional_ecosystem', 'max_scan': 20, 'max': 6},
    # Disrupt Africa：RSS 恢复，root feed 可用
    {'name': 'Disrupt Africa',   'url': 'https://disrupt-africa.com/feed/',             'source': 'Disrupt Africa', 'region': '非洲', 'priority': 2, 'source_tier': 'L2 垂直交易源', 'source_role': 'venture_media', 'max_scan': 20, 'max': 8},
    {'name': 'Techpoint',        'url': 'https://techpoint.africa/feed/',               'source': 'Techpoint',     'region': '非洲', 'priority': 2, 'source_tier': 'L3 区域生态源', 'source_role': 'regional_ecosystem', 'max_scan': 20, 'max': 6},
    {'name': 'Ventureburn',      'url': 'https://ventureburn.com/feed/',                'source': 'Ventureburn',   'region': '非洲', 'priority': 2, 'source_tier': 'L2 垂直交易源', 'source_role': 'venture_media', 'max_scan': 20, 'max': 8},
    {'name': 'WeeTracker',       'url': 'https://weetracker.com/feed/',                 'source': 'WeeTracker',    'region': '非洲', 'priority': 2, 'source_tier': 'L3 区域生态源', 'source_role': 'regional_ecosystem', 'max_scan': 20, 'max': 6},
    # --- 拉美 ---
    # 注意：Bloomberg RSS 是全球综合科技，不限于拉美，已移除避免噪声
    {'name': 'LatamList',        'url': 'https://latamlist.com/feed/',                   'source': 'LatamList',     'region': '拉美', 'priority': 3, 'source_tier': 'L2 垂直交易源', 'source_role': 'venture_media', 'max_scan': 20, 'max': 8},
    {'name': 'LAVCA',            'url': 'https://lavca.org/feed/',                        'source': 'LAVCA',         'region': '拉美', 'priority': 3, 'source_tier': 'L2 垂直交易源', 'source_role': 'venture_media', 'max_scan': 20, 'max': 8},
    {'name': 'Contxto',          'url': 'https://contxto.com/en/feed/',                  'source': 'Contxto',       'region': '拉美', 'priority': 2, 'source_tier': 'L3 区域生态源', 'source_role': 'regional_ecosystem', 'max_scan': 24, 'max': 6},
    # --- 深度趋势源：只保留高信号，不参与普通新闻补量 ---
    {'name': 'Rest of World Money', 'url': 'https://restofworld.org/feed/money/',        'source': 'Rest of World', 'region': '全球', 'priority': 2, 'source_tier': 'L4 深度趋势源', 'source_role': 'deep_trend', 'max_scan': 20, 'max': 4, 'signal_only': True},
    {'name': 'Rest of World Ecommerce', 'url': 'https://restofworld.org/feed/e-commerce/', 'source': 'Rest of World', 'region': '全球', 'priority': 2, 'source_tier': 'L4 深度趋势源', 'source_role': 'deep_trend', 'max_scan': 20, 'max': 4, 'signal_only': True},
    # 2026-08 补缺：Cyberagent 官方 RSS（/en/news/ HTML 页只有分类导航，RSS 才含文章）
    {'name': 'Cyberagent News', 'url': 'https://www.cyberagent.co.jp/en/news/rss/data_format=xml', 'source': 'Cyberagent', 'region': '亚太', 'priority': 1, 'source_tier': 'L1 官方/IR源', 'source_role': 'official_ir', 'company_name': 'Cyberagent', 'is_company': True, 'max_scan': 20, 'max': 4},
]

# ============================================================
# 27家重点公司监控 — Google News RSS
# ============================================================

COMPANY_SOURCES = [
    # 中国企业海外
    {'name': 'ByteDance/TikTok', 'query': 'ByteDance', 'region': '中资', 'priority': 3},
    {'name': 'Tencent', 'query': 'Tencent international', 'region': '中资', 'priority': 2},
    {'name': 'Alibaba', 'query': 'Alibaba international overseas', 'region': '中资', 'priority': 2},
    {'name': 'JD.com', 'query': 'JD.com international overseas', 'region': '中资', 'priority': 2},
    {'name': 'Kuaishou', 'query': 'Kuaishou', 'region': '中资', 'priority': 1},
    {'name': 'Ant Group', 'query': 'Ant Group', 'region': '中资', 'priority': 2},
    {'name': 'Meituan', 'query': 'Meituan', 'region': '中资', 'priority': 1},
    # 亚太
    {'name': 'Kakao', 'query': 'Kakao', 'region': '亚太', 'priority': 2},
    {'name': 'Naver', 'query': 'Naver', 'region': '亚太', 'priority': 2},
    {'name': 'Rakuten', 'query': 'Rakuten', 'region': '亚太', 'priority': 2},
    {'name': 'Sea Limited', 'query': 'Sea Limited Shopee', 'region': '亚太', 'priority': 2},
    {'name': 'Grab', 'query': 'Grab holdings Singapore', 'region': '亚太', 'priority': 2},
    {'name': 'Gojek', 'query': 'Gojek', 'region': '亚太', 'priority': 2},
    {'name': 'VNG Group', 'query': 'VNG', 'region': '亚太', 'priority': 1},
    {'name': 'Yahoo', 'query': 'Yahoo Tech APAC', 'region': '亚太', 'priority': 1},
    {'name': 'Cyberagent', 'query': 'CyberAgent', 'region': '亚太', 'priority': 1},
    {'name': 'HKTVmall', 'query': 'HKTVmall Hong Kong Technology Venture', 'region': '亚太', 'priority': 1},
    {'name': 'U-NEXT', 'query': 'U-NEXT', 'region': '亚太', 'priority': 1},
    {'name': 'Square Enix', 'query': 'Square Enix', 'region': '亚太', 'priority': 1},
    # 欧洲
    {'name': 'Adyen', 'query': 'Adyen', 'region': '欧洲', 'priority': 2},
    {'name': 'Zalando', 'query': 'Zalando Germany', 'region': '欧洲', 'priority': 2},
    {'name': 'Allegro', 'query': 'Allegro ecommerce', 'region': '欧洲', 'priority': 2},
    {'name': 'Trendyol', 'query': 'Trendyol', 'region': '欧洲', 'priority': 1},
    # 拉美
    {'name': 'MercadoLibre', 'query': 'MercadoLibre', 'region': '拉美', 'priority': 3},
    {'name': 'Nubank', 'query': 'Nubank', 'region': '拉美', 'priority': 2},
    {'name': 'Rappi', 'query': 'Rappi', 'region': '拉美', 'priority': 1},
    # 中东
    {'name': 'Noon', 'query': 'Noon ecommerce UAE Dubai', 'region': '中东', 'priority': 2},
    {'name': 'Careem', 'query': 'Careem UAE', 'region': '中东', 'priority': 2},
    {'name': 'Tabby', 'query': 'Tabby fintech', 'region': '中东', 'priority': 2},
    {'name': 'Kaspi.kz', 'query': 'Kaspi.kz', 'region': '中东', 'priority': 2},
    # 非洲
    {'name': 'Jumia', 'query': 'Jumia', 'region': '非洲', 'priority': 2},
    {'name': 'Konga', 'query': 'Konga Nigeria', 'region': '非洲', 'priority': 1},
]

for _company_cfg in COMPANY_SOURCES:
    _company_cfg.setdefault('source_tier', 'L5 Google News 补漏源')
    _company_cfg.setdefault('source_role', 'company_radar')
    _company_cfg.setdefault('max', 2)
    _company_cfg.setdefault('max_other', 0)

COMPANY_ALIASES = {
    'ByteDance/TikTok': ['ByteDance', 'TikTok', 'Douyin'],
    'Tencent': ['Tencent', 'WeChat', 'Weixin'],
    'Alibaba': ['Alibaba', 'AliExpress', 'Cainiao', 'Lazada', 'Alibaba Cloud'],
    'JD.com': ['JD.com', 'JD', 'Jingdong', 'Jing Dong'],
    'Kuaishou': ['Kuaishou', 'Kwai'],
    'Ant Group': ['Ant Group', 'Ant International', 'Alipay'],
    'Meituan': ['Meituan', 'Keeta'],
    'Kakao': ['Kakao', 'Kakao Pay', 'Kakao Games', 'Kakao Entertainment'],
    'Naver': ['Naver', 'Line'],
    'Rakuten': ['Rakuten', 'Rakuten Securities'],
    'Sea Limited': ['Sea Limited', 'Sea', 'Shopee', 'Garena'],
    'Grab': ['Grab', 'Grab Holdings', 'GrabPay'],
    'Gojek': ['Gojek', 'GoTo', 'Tokopedia'],
    'VNG Group': ['VNG', 'VNG Group', 'Zalo'],
    'Yahoo': ['Yahoo'],
    'Cyberagent': ['CyberAgent', 'Cyberagent', 'ABEMA'],
    'HKTVmall': ['HKTVmall', 'Hong Kong Technology Venture', 'HKTV'],
    'U-NEXT': ['U-NEXT', 'U-NEXT HOLDINGS', 'U-NEXT Holdings', 'USEN-NEXT'],
    'Square Enix': ['Square Enix', 'Square Enix Holdings', 'SQUARE ENIX'],
    'Stord': ['Stord'],
    'OpenRouter': ['OpenRouter'],
    'Quantinuum': ['Quantinuum'],
    'Adyen': ['Adyen'],
    'Zalando': ['Zalando'],
    'Allegro': ['Allegro'],
    'Trendyol': ['Trendyol'],
    'MercadoLibre': ['MercadoLibre', 'Mercado Libre', 'Mercado Pago', 'MELI'],
    'Rappi': ['Rappi', 'RappiCard'],
    'Noon': ['Noon'],
    'Careem': ['Careem', 'Careem Pay'],
    'Tabby': ['Tabby'],
    'Kaspi.kz': ['Kaspi.kz', 'Kaspi'],
    'Jumia': ['Jumia'],
    'Konga': ['Konga'],
    'MoMo': ['MoMo', 'Momo', 'Momo Vietnam'],
    'Tamara': ['Tamara', 'Tamara.co'],
    'stc': ['stc', 'stc Group', 'Saudi Telecom', 'STC'],
    'Nubank': ['Nubank', 'Nu Holdings'],
    'OPay': ['OPay', 'OPay Nigeria', 'OPay Digital Services'],
    'M-Pesa': ['M-Pesa', 'Safaricom', 'M-PESA', 'MPESA'],
    'OpenAI': ['OpenAI', 'ChatGPT', 'OpenAI API'],
    'Anthropic': ['Anthropic', 'Claude', 'Anthropic API'],
    'Databricks': ['Databricks', 'MosaicML'],
}

# Google News RSS 关键词黑名单（公司新闻噪音）
COMPANY_BLACKLIST = [
    'show hn:', 'launch HN', 'Ask HN:', 'Hiring ',
    'Introducing Claude', 'Introducing GPT', 'Introducing Gemini',
    'openai launches', 'anthropic announces', 'google announces',
    'apple announces', 'meta announces', 'microsoft announces',
    'weekly newsletter', 'daily newsletter',
    # 体育/娱乐噪声
    'baseball', 'football', 'soccer', 'basketball', 'tennis', 'cricket',
    'playoffs', 'championship', 'world cup', 'olympic', 'sports',
    'mother\'s day', 'mothers day', 'valentine', 'christmas', 'easter',
    'celebrity', 'gossip', 'entertainment', 'tv show', 'movie',
    'interview with', 'exclusive interview', 'we spoke to',
    'highlights', 'replay', 'match report', 'ahegao',
    # 产品页面/购物噪声
    'free shipping', 'buy now', 'shop now', 'best price',
    'glossy photo paper', 'tone paper', 'photo paper',
    'coupon', 'discount', 'on sale', 'clearance ',
    'promo:', 'bonus', 'points', 'earn up to',
    'order ', 'purchase ', 'delivery ',
    # 占位/无内容噪声
    '404', 'page not found', 'access denied', 'subscribe to',
    # 政治/非科技
    'election', 'local elections', 'president', 'protest', 'poll ', 'voting',
    'trial', 'arrested', 'prison', 'graft', 'corruption',
    # Google News 金融站/安全告警噪声
    'phishing', 'password reset', 'urgent alert', 'security alert',
    'analyst rating', 'analyst ratings', 'analyst price target',
    'target price', 'price target', 'valuation check', 'stock focus',
    'nasdaqgs:', 'nyse:', 'otcmkts:', 'kr7 ', 'simply wall st',
    'tipranks', 'yahoo finance', 'ad hoc news', 'indexbox',
    'should you buy', 'is it time to buy', 'is it too late to buy',
    'shares fall', 'shares jump', 'shares slide', 'stock price forecast',
    'stock price', 'gf score', 'strong investment opportunity',
    'xrp', 'crypto', 'token', 'stablecoin', 'crypto exchange',
    'social media traffic', 'vs. stripe', 'comparison',
]

COMPANY_LOW_SIGNAL_PATTERNS = [
    'earnings call highlights', 'earnings snapshot', 'transcript :',
    'stock is trending', 'price prediction', 'shares bought by',
    'live score', 'predictions', 'gift (nasdaq', 'simplywall.st',
    'marketbeat', 'benzinga', 'seeking alpha', 'openpr.com',
    'upgraded points', 'sofascore',
    'analyst target', 'analyst ratings', 'target price', 'price target',
    'valuation check', 'stock focus', 'stock analysis', 'stock forecast',
    'stock to buy', 'brokerages set', 'short interest', 'dividend yield',
    'institutional investors', 'etf inflows', 'options trading',
    'ticker report', 'defense world', 'american banking news',
    'zacks', 'motley fool', 'investing.com', 'insider monkey',
    'yahoo finance', 'tipranks', 'simply wall st', 'ad hoc news',
    'indexbox', 'phishing', 'password', 'urgent alert',
    'promo:', 'bonus', 'points', 'earn up to', 'stock price',
    'shares fall', 'shares jump', 'shares slide', 'stock price forecast',
    'gf score', 'strong investment opportunity', 'xrp', 'crypto',
    'token', 'stablecoin', 'crypto exchange', 'trial', 'arrested',
    'prison', 'graft', 'corruption', 'local elections',
    'social media traffic', 'comparison', 'tech times',
]

CHINESE_OUTBOUND_PATTERNS = [
    'overseas', 'international', 'global', 'abroad', 'offshore', 'foreign market',
    'cross-border', 'cross border', 'southeast asia', 'middle east', 'europe',
    'latin america', 'africa', 'india', 'japan', 'korea', 'singapore',
    'malaysia', 'indonesia', 'thailand', 'vietnam', 'philippines', 'uae',
    'saudi', 'dubai', 'kuwait', 'turkey', 'brazil', 'mexico',
    'expands to', 'expands into', 'launches in', 'enters',
    '海外', '出海', '国际', '跨境', '境外',
]

# 传统持牌商业银行主体名单。Fintech 源的编辑视野覆盖整个金融服务业，
# 但这些机构主体不是互联网/科技公司，不属于情报站定位，排除。
TRADITIONAL_BANKS = [
    # 亚太
    'DBS', 'United Overseas Bank', 'UOB', 'OCBC', 'Maybank', 'CIMB',
    'RHB Bank', 'Public Bank', 'Bank Rakyat', 'BDO Unibank',
    'Bank of the Philippine Islands', 'Bank Central Asia', 'Bank Mandiri',
    'Bank Rakyat Indonesia', 'Bank BRI', 'Kaspi Bank', 'HDFC Bank',
    'ICICI Bank', 'State Bank of India', 'Axis Bank',
    # 欧美
    'HSBC', 'Standard Chartered', 'Citibank', 'JPMorgan', 'JP Morgan',
    'Bank of America', 'Wells Fargo', 'Goldman Sachs', 'Morgan Stanley',
    'Barclays', 'Deutsche Bank', 'BNP Paribas', 'Societe Generale',
    'Société Générale', 'ING Group', 'ING Bank', 'Santander', 'BBVA',
    'UBS', 'Credit Suisse', 'Lloyds Bank', 'NatWest', 'Bank of England',
    'Royal Bank of Canada',
    # 中东
    'First Abu Dhabi Bank', 'Emirates NBD', 'Qatar National Bank', 'QNB',
    'Saudi National Bank', 'National Bank of Kuwait', 'Al Rajhi Bank',
    'Abu Dhabi Commercial Bank', 'Dubai Islamic Bank', 'Mashreq Bank',
    # 非洲
    'Standard Bank', 'Absa', 'Nedbank', 'Ecobank', 'GTBank',
    'Guaranty Trust Bank', 'Zenith Bank', 'First Bank of Nigeria',
    'Access Bank', 'KCB Bank', 'Equity Bank',
    # 拉美
    'Itau', 'Itaú', 'Banco do Brasil', 'Bradesco',
    # 中文
    '工商银行', '建设银行', '农业银行', '中国银行', '招商银行', '汇丰银行',
]

# 名字含 bank/banking 但属于科技/互联网公司或数字银行（用户监控对象），保留。
# 在传统银行匹配中优先豁免，防误杀。
BANK_PROTECTED_FINTECH = [
    '10x Banking', 'GXS Bank', 'Trust Bank', 'Revolut', 'Nubank',
    'Nu Holdings', 'Monzo', 'Starling Bank', 'Chime', 'Varo', 'N26',
    'Tinkoff', 'WeBank', 'Alipay', 'Ant Group', 'Paytm', 'GoPay',
    'Grab', 'GoTo', 'KakaoBank', 'Kakao', 'Klarna', 'Pine Labs',
    'Razorpay', 'Juspay', 'Flutterwave', 'Kaspi', 'SeaMoney',
    'Shopee', 'Lazada', 'MercadoPago', 'Mercado Pago',
]

TITLE_STOPWORDS = {
    'the', 'and', 'for', 'with', 'from', 'into', 'over', 'under', 'amid', 'after',
    'before', 'across', 'through', 'about', 'says', 'report', 'reports', 'reported',
    'amid', 'launch', 'launches', 'launched', 'announces', 'announced', 'latest',
    'today', 'week', 'news', 'update', 'live', 'analysis', 'opinion',
}

EVENT_ENTITY_STOPWORDS = {
    'inc', 'corp', 'corporation', 'company', 'co', 'ltd', 'limited', 'group',
    'holdings', 'holding', 'technologies', 'technology', 'tech', 'systems',
    'platform', 'platforms', 'analytics', 'computing', 'apps', 'app', 'software',
    'ai', 'digital', 'global', 'online', 'the', 'amazon', 'fulfillment',
    'competitor', 'more', 'than', 'korea', 'regional', 'local', 'studio',
    'busan', 'cloud', 'hands', 'training', 'startups',
}

SECTOR_SCOPE_MAP = {
    'ai_platform': ['ai_infra'],
    'data_ai_platform': ['ai_infra', 'cloud_saas_developer'],
    'cloud_ai_infra': ['ai_infra', 'cloud_saas_developer'],
    'search_ai_cloud': ['ai_infra', 'cloud_saas_developer'],
    'telco_digital_infra': ['cloud_saas_developer'],
    'payment': ['payments'],
    'payment_wallet': ['payments'],
    'payment_developer_platform': ['payments', 'cloud_saas_developer'],
    'cross_border_payment': ['payments'],
    'digital_bank': ['payments'],
    'bnpl_payment': ['payments'],
    'commerce': ['commerce'],
    'commerce_payment': ['commerce', 'payments'],
    'commerce_fintech': ['commerce', 'payments'],
    'commerce_saas': ['commerce', 'cloud_saas_developer'],
    'commerce_logistics': ['commerce', 'local_services_logistics'],
    'commerce_gaming_fintech': ['commerce', 'gaming_content', 'payments'],
    'gaming': ['gaming_content'],
    'streaming_media': ['gaming_content'],
    'social_payment': ['ads_social', 'payments'],
    'social_payment_gaming': ['ads_social', 'payments', 'gaming_content'],
    'mobility_payment': ['local_services_logistics', 'payments'],
    'mobility_super_app': ['local_services_logistics'],
    'super_app_fintech': ['local_services_logistics', 'payments'],
    'delivery_fintech': ['local_services_logistics', 'payments'],
    'travel_local_services': ['local_services_logistics'],
    # 2026-08-14 新增：美国 7 姐妹/云厂商 + 中国模型厂商/头部互联网
    'consumer_ai_hardware': ['ai_infra'],
    'ai_hardware_infra': ['ai_infra'],
    'cloud_commerce': ['commerce', 'cloud_saas_developer'],
    'social_ai': ['ads_social', 'ai_infra'],
    'cloud_ai_search': ['ai_infra', 'cloud_saas_developer'],
    'ev_ai_autonomy': ['ai_infra'],
    'ai_platform_content': ['ai_infra', 'ads_social'],
    'cloud_ai_commerce': ['commerce', 'cloud_saas_developer', 'ai_infra'],
    'social_ai_gaming': ['ads_social', 'gaming_content', 'ai_infra'],
    'ai_search_cloud': ['ai_infra', 'cloud_saas_developer'],
    'local_services': ['local_services_logistics'],
}


def _load_company_scope_contracts(path='data/entity_pool.json'):
    try:
        with open(path, encoding='utf-8') as f:
            pool = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    contracts = {}
    for entity in pool.get('entities') or []:
        industries = SECTOR_SCOPE_MAP.get(entity.get('sector'), [])
        contract = {
            'scope_industries': industries,
            'scope_regions': [entity['region']] if entity.get('region') else [],
            'vertical': entity.get('sector', ''),
        }
        for name in [entity.get('name'), *(entity.get('aliases') or [])]:
            if name:
                contracts[name.lower()] = contract
    return contracts


COMPANY_SCOPE_CONTRACTS = _load_company_scope_contracts()


def _apply_company_scope_contract(cfg):
    names = [
        cfg.get('company_name'),
        cfg.get('name'),
        *(COMPANY_ALIASES.get(cfg.get('company_name'), [])),
        *(COMPANY_ALIASES.get(cfg.get('name'), [])),
    ]
    for name in names:
        contract = COMPANY_SCOPE_CONTRACTS.get((name or '').lower())
        if not contract:
            continue
        for key, value in contract.items():
            if value and not cfg.get(key):
                cfg[key] = value
        break
    return cfg


for _company_cfg in COMPANY_SOURCES:
    _apply_company_scope_contract(_company_cfg)

# ============================================================
# 关键词检测（宽松模式，宁多不漏）
# ============================================================

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

def _source_meta(cfg):
    """保留信源分层，供后续日报/周报/月报按业务口径组织。"""
    return {
        'source_tier': cfg.get('source_tier', 'L3 区域生态源'),
        'source_role': cfg.get('source_role', 'regional_ecosystem'),
        'vertical': cfg.get('vertical', ''),
        'source_type': cfg.get('source_type', ''),
        'access_method': cfg.get('access_method', ''),
        'signal_types': cfg.get('signal_types', []),
        'source_id': cfg.get('id', cfg.get('name', '')),
        'credibility_score': cfg.get('credibility_score', 0),
        'noise_level': cfg.get('noise_level', ''),
        'scope_industries': cfg.get('scope_industries', []),
        'scope_regions': cfg.get('scope_regions', []),
        'publisher_type': cfg.get('publisher_type', ''),
        'authority_domains': cfg.get('authority_domains', []),
        'claim_roles': cfg.get('claim_roles', []),
        'access_level': cfg.get('access_level', ''),
        'report_access_level': cfg.get('report_access_level', cfg.get('access_level', '')),
        'methodology_visibility': cfg.get('methodology_visibility', ''),
        'report_methodology_visible': cfg.get('report_methodology_visible', False),
    }

def _with_source_meta(item, cfg):
    item.update(_source_meta(cfg))
    company = item.get('company_name') or ''
    if company and item.get('is_company') and not _title_mentions_aliases(item.get('title', ''), _get_company_aliases(company)):
        item['title'] = f"{company}: {item.get('title', '')}"
    item['region'] = infer_event_region(item.get('title', ''), item.get('region', cfg.get('region', '未知')))
    item['signal_taxonomy'] = infer_signal_taxonomy(item)
    return item


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


REGISTRY_TIER_MAP = {
    'L1': 'L1 官方/IR源',
    'L2': 'L2 垂直交易源',
    'L3': 'L3 区域生态源',
    'L4': 'L4 垂直赛道精品源',
    'L5': 'L5 Google News 补漏源',
}

REGISTRY_ROLE_MAP = {
    'newsroom': 'official_ir',
    'ir': 'official_ir',
    'changelog': 'developer_change',
    'developer_changelog': 'developer_change',
    'engineering_blog': 'industry_vertical',
    'research_report': 'industry_vertical',
    'industry_media': 'industry_vertical',
    'media': 'regional_ecosystem',
}


def _registry_source_to_cfg(src):
    tier = REGISTRY_TIER_MAP.get(src.get('tier'), src.get('source_tier') or src.get('tier') or 'L3 区域生态源')
    source_type = src.get('source_type') or 'media'
    role = src.get('source_role') or REGISTRY_ROLE_MAP.get(source_type, 'regional_ecosystem')
    cfg = {
        'id': src.get('id') or src.get('name'),
        'name': src.get('name'),
        'url': src.get('url'),
        'source': src.get('source') or src.get('name'),
        'region': src.get('region', '全球'),
        'priority': src.get('priority', 2),
        'source_tier': tier,
        'source_role': role,
        'source_type': source_type,
        'access_method': src.get('access_method') or src.get('method') or 'rss',
        'signal_types': src.get('signal_types') or src.get('bd_signal_types') or [],
        'vertical': src.get('track', ''),
        'max_scan': src.get('max_scan', 12),
        'max': src.get('max', 4),
        'signal_only': src.get('signal_only', True),
        'credibility_score': src.get('credibility_score', 0),
        'noise_level': src.get('noise_level', ''),
        'scope_industries': src.get('scope_industries') or [],
        'scope_regions': src.get('scope_regions') or [],
    }
    for key in ('company_name', 'is_company', 'include_url_patterns', 'allowed_scope_layers'):
        if key in src:
            cfg[key] = src[key]
    for key in ('access_level', 'report_access_level', 'methodology_visibility', 'report_methodology_visible'):
        if key in src:
            cfg[key] = src[key]
    if (
        tier == 'L1 官方/IR源'
        and source_type in {'changelog', 'developer_changelog', 'newsroom', 'ir'}
        and not cfg.get('company_name')
    ):
        cfg['company_name'] = src.get('company_name') or _source_entity_name(src.get('source') or src.get('name'))
        cfg['is_company'] = True
    return cfg


def _source_entity_name(value):
    name = (value or '').strip()
    for suffix in (
        ' Developer Changelog',
        ' Changelog',
        ' Newsroom',
        ' IR News',
        ' IR',
        ' Press',
        ' News',
    ):
        if name.endswith(suffix):
            return name[:-len(suffix)].strip()
    return name


def _source_metric_key(item):
    return item.get('source_id') or item.get('source') or item.get('display_source') or item.get('source_detail') or '未知来源'


def _count_by_source(items):
    counts = {}
    for item in items:
        key = _source_metric_key(item)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _source_funnel_stage(items, total_key):
    rows = {}
    for key, count in _count_by_source(items).items():
        rows[key] = {total_key: count}
    return rows


def _merge_source_funnel(target, stage_counts):
    for key, counts in stage_counts.items():
        row = target.setdefault(key, {})
        for metric, count in counts.items():
            row[metric] = row.get(metric, 0) + count


def load_registry_sources(path='data/source_registry.json'):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            registry = json.load(f)
    except (OSError, json.JSONDecodeError):
        return [], []
    sources = registry.get('sources') or registry.get('active_sources') or []
    rss, html = [], []
    existing_names = {cfg.get('name') for cfg in RSS_SOURCES + HTML_SOURCES}
    existing_urls = {cfg.get('url') for cfg in RSS_SOURCES + HTML_SOURCES}
    for src in sources:
        if src.get('status') not in {'active', 'enabled'}:
            continue
        if not src.get('url') or src.get('name') in existing_names or src.get('url') in existing_urls:
            continue
        cfg = _registry_source_to_cfg(src)
        method = cfg.get('access_method')
        if method in {'rss', 'atom'}:
            rss.append(cfg)
        elif method in {'html', 'sitemap', 'pressroom', 'changelog'}:
            html.append(cfg)
    return rss, html


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


def _strip_title_source(title):
    """去掉标题末尾的媒体名尾缀，避免同事件因来源不同被拆成多条。"""
    title = (title or '').strip()
    for sep in [' - ', ' | ', ' — ', ' – ', ' —']:
        if sep in title:
            left, right = title.rsplit(sep, 1)
            if right and len(right) <= 40:
                return left.strip()
    return title


def _extract_title_publisher(title):
    """提取 Google News 标题尾部媒体名，保留真实来源用于控噪和展示。"""
    title = (title or '').strip()
    for sep in [' - ', ' | ', ' — ', ' – ', ' —']:
        if sep in title:
            left, right = title.rsplit(sep, 1)
            right = right.strip()
            if left.strip() and 1 < len(right) <= 40:
                return right
    return ''


def _normalize_text(text):
    text = _strip_title_source(text).lower()
    text = text.replace('&', ' and ')
    text = re.sub(r'[\u2018\u2019\u201c\u201d]', ' ', text)
    text = re.sub(r'[^a-z0-9\u4e00-\u9fff]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _title_tokens(title):
    tokens = []
    for token in _normalize_text(title).split():
        if token in TITLE_STOPWORDS:
            continue
        if len(token) <= 2 and token not in {'q1', 'q2', 'q3', 'q4', 'ai', 'ipo'}:
            continue
        if token.isdigit():
            continue
        tokens.append(token)
    return tokens


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
    """
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
    'acquires', 'acquired', 'acquisition', 'merger', 'merges', 'merging',
    'buys', 'buying', 'purchase', 'takeover', '收购', '并购', '買収', '合併',
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
    date_a = a.get('article_date') or a.get('date') or ''
    date_b = b.get('article_date') or b.get('date') or ''
    if not date_a or not date_b:
        return True
    try:
        gap = abs((datetime.strptime(date_a[:10], '%Y-%m-%d')
                   - datetime.strptime(date_b[:10], '%Y-%m-%d')).days)
    except ValueError:
        return False
    return gap <= window_days


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
    """AI 指纹合并判定：canonical_company + 主类型 + canonical_key 三项全匹配才视为同一事件。
    任一项缺失（存量事件无指纹）返回 None，交回旧判定逻辑。"""
    ca = a.get('canonical_company') or ''
    cb = b.get('canonical_company') or ''
    if not ca or not cb:
        return None
    if _normalize_company_key(ca) != _normalize_company_key(cb):
        return None
    if _primary_event_type(a) != _primary_event_type(b):
        return None
    ka = _normalize_canonical_key(a.get('canonical_key') or '')
    kb = _normalize_canonical_key(b.get('canonical_key') or '')
    if not ka or not kb:
        return None
    return ka == kb


# ============================================================
# 事实评分账本：同一件事（指纹）只打一次分，跨批次/跨 run 复用
# （治"同一事实多版本分数漂移"；8-24 指纹定案的延伸，不新建识别体系）
# ============================================================

_FACT_LEDGER_PATH = Path('data/fact_score_ledger.json')
_fact_ledger = {}


def _load_fact_ledger():
    global _fact_ledger
    try:
        if _FACT_LEDGER_PATH.exists():
            with open(_FACT_LEDGER_PATH, encoding='utf-8') as f:
                _fact_ledger = json.load(f)
            if not isinstance(_fact_ledger, dict):
                _fact_ledger = {}
    except Exception:
        _fact_ledger = {}


def _save_fact_ledger():
    try:
        _FACT_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_FACT_LEDGER_PATH, 'w', encoding='utf-8') as f:
            json.dump(_fact_ledger, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _fact_ledger_key(ev):
    """指纹键：归一化公司 + 主类型 + 归一化金额锚点；缺任一返回 None（不进账本）。"""
    cc = _normalize_company_key(ev.get('canonical_company') or '')
    ck = _normalize_canonical_key(ev.get('canonical_key') or '')
    pt = _primary_event_type(ev)
    if not cc or not ck:
        return None
    return f'{cc}|{pt}|{ck}'


def _apply_fact_score_rules(ev):
    """同事实复用历史分（第1层）；边界外事件不许高分（第3层，落实 A12.4）。

    拦截口径只取明确的 out_of_scope（军工/生物/医疗/纯航天），
    不含边缘/相邻类（无关键词的财报、产品事件不能被误伤）。"""
    if assess_internet_relevance(ev).get('label') == 'out_of_scope':
        cur = ev.get('score') or 0
        if cur > 4:
            ev['score'] = 4
            ev['boundary_capped'] = True
    key = _fact_ledger_key(ev)
    if not key:
        return
    if key in _fact_ledger:
        ev['score'] = _fact_ledger[key]['score']
        ev['score_reused'] = True
    else:
        _fact_ledger[key] = {
            'score': ev.get('score') or 0,
            'first_seen': ev.get('date') or '',
            'samples': 1,
        }


def _is_same_event(candidate, existing):
    if candidate.get('url') and candidate.get('url') == existing.get('url'):
        return True

    if _fingerprint_match(candidate, existing):
        return True

    if not _dates_adjacent(candidate, existing):
        return False

    type_a = _primary_event_type(candidate)
    type_b = _primary_event_type(existing)
    key_a, src_a = _entity_key_info(candidate)
    key_b, src_b = _entity_key_info(existing)

    # 实体键 + 类型主键一致时，按类型决定合并强度
    if type_a == type_b and key_a and key_a == key_b:
        if type_a in _SINGULAR_EVENT_TYPES:
            if src_a == 'title' or src_b == 'title':
                # 标题提取的实体是弱信号，需相似度防误并（如两个同名小公司同日出融资）
                if _event_similarity(candidate, existing) < 0.4:
                    return False
            # 事件锚点守卫：两条都必须明确指向同一事件锚（财报/融资/并购词）。
            # 同公司同日可有多条不同类型文章（MercadoLibre 同日多条股票评论、
            # Kakao 财报日发布游戏新闻），没有共同锚点就不是同一事件，不合并。
            anchor_ok = {
                'earnings': _has_financial_anchor(candidate.get('title', '')) and _has_financial_anchor(existing.get('title', '')),
                'funding': _has_funding_signal(candidate.get('title', '')) and _has_funding_signal(existing.get('title', '')),
                'ma': _has_ma_signal(candidate.get('title', '')) and _has_ma_signal(existing.get('title', '')),
            }.get(type_a, True)
            if not anchor_ok:
                return False
            if type_a == 'earnings' and not _financial_direction_consistent(candidate, existing):
                # 财报方向相反是不同事件（「利润创新高」vs「净利大跌/财报不及预期」），不合并
                return False
            # company/alias 来源可靠：同实体同日同类且锚点一致即同一事件
            return True
        if type_a == 'strategy':
            # 一实体一日可有多个策略事件，仍以标题相似度防误并
            return _event_similarity(candidate, existing) >= 0.42

    # 兜底：无实体键或实体不同时，仅高标题相似度合并
    return _event_similarity(candidate, existing) >= 0.72


def _event_info_score(event):
    """估算事件信息完整度：标题越具体、解释字段越全，越值得保留展示。"""
    score = len(_title_tokens(event.get('title', ''))) * 2
    if event.get('content_overview'):
        score += max(0, len(str(event['content_overview'])) // 20)
    if event.get('summary_short'):
        score += 1
    if event.get('reason'):
        score += 1
    if event.get('insight_label'):
        score += 1
    return score


def _is_more_complete(candidate, existing):
    """candidate 比已入库事件信息更完整时返回 True，用于跨批次合并时升级旧事件。"""
    return _event_info_score(candidate) > _event_info_score(existing)


def _upgrade_event(existing, new):
    """跨天合并时，用信息更完整的新事件覆盖已入库事件的可展示字段，保留原日期归属。"""
    for field in ('title', 'url', 'content_overview', 'summary_short', 'reason',
                  'impact', 'insight_label', 'trend_topic'):
        if new.get(field):
            existing[field] = new[field]
    existing.setdefault('merged_from', [])
    if new.get('url') and new['url'] not in existing['merged_from']:
        existing['merged_from'].append(new['url'])



# --- 缓存（仅用于单次运行内去重，不跨天保留）---
CACHE_DIR = Path('data/.cache')
CACHE_TTL = 60 * 60 * 24  # 24小时

def _cache_key(url):
    return hashlib.md5(url.encode()).hexdigest()

def _cache_get(url):
    """返回 (body, age_seconds)，无缓存或过期返回 (None, None)"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = CACHE_DIR / _cache_key(url)
    if not f.exists(): return None, None
    age = time.time() - f.stat().st_mtime
    if age > CACHE_TTL:
        f.unlink()
        return None, None
    return f.read_text(encoding='utf-8', errors='ignore'), age

def _cache_set(url, body):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = CACHE_DIR / _cache_key(url)
    f.write_text(body, encoding='utf-8')


def _clear_old_cache():
    """每次运行前清理旧缓存，确保抓取最新内容"""
    import shutil
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  🗑  已清理历史缓存（{CACHE_DIR}）")


def fetch_url(url, retries=1):
    """
    快速失败策略：
    - 只重试1次（之前重试3次无意义，失败通常是网络/CF，超时后立即失败更好）
    - 超时8s（之前20s太长，RSS本身5s内必返回）
    - 优先读缓存，缓存命中则跳过网络请求
    """
    # 1. 缓存命中
    body, age = _cache_get(url)
    if body:
        print(f"  [CACHE] {url[:50]}... ({age:.0f}s old)")
        return body  # 返回文本，调用方用同样方式解析

    # 2. 网络请求（最多重试1次）
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if r.status_code in (403, 429):
                if i < retries:
                    time.sleep(2 * (i + 1)); continue
                return None
            r.raise_for_status()
            body = r.text
            _cache_set(url, body)  # 写缓存
            return body
        except Exception:
            if i < retries:
                time.sleep(2 ** i); continue
            return None
    return None


async def fetch_url_async(session, url, semaphore):
    """异步单 URL 抓取（带信号量控制并发）"""
    async with semaphore:
        # 检查缓存
        body, age = _cache_get(url)
        if body:
            return url, body, age, True  # cache_hit

        try:
            async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as r:
                if r.status in (403, 429):
                    return url, None, 0, False
                body = await r.text()
                _cache_set(url, body)
                return url, body, 0, False
        except Exception as e:
            return url, None, 0, False


async def fetch_all_parallel(urls):
    """
    并行抓取所有 URL。
    返回 {url: (body_or_None, from_cache)}
    """
    semaphore = asyncio.Semaphore(8)  # 最多8个并发
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_url_async(session, url, semaphore) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    out = {}
    for item in results:
        if isinstance(item, Exception):
            continue
        url, body, age, cached = item
        out[url] = (body, cached)
    return out

# ============================================================
# 工具函数
# ============================================================

def _parse_rss_date(item):
    """从 RSS/Atom 条目提取文章发布日期，返回 ISO 格式字符串，失败返回 None"""
    # 已废弃：feedparser 自动标准化日期，保留接口兼容
    return None


def _rss_date_metadata(entry, link, observed_at=None):
    candidates = []
    if entry.get('published_parsed'):
        candidates.append((datetime(*entry['published_parsed'][:3]).strftime('%Y-%m-%d'), 'rss_published', 'high'))
    if entry.get('updated_parsed'):
        candidates.append((datetime(*entry['updated_parsed'][:3]).strftime('%Y-%m-%d'), 'rss_updated', 'medium'))
    candidates.append((_extract_date_from_url(link), 'url_path', 'high'))
    rejected = None
    for candidate, source, confidence in candidates:
        if not candidate:
            continue
        metadata = publication_metadata(candidate, source, confidence, observed_at=observed_at)
        if metadata['published_at']:
            return metadata
        if metadata.get('scheduled_at') and rejected is None:
            rejected = metadata
    return rejected or publication_metadata('', 'observed_at', 'observed', observed_at=observed_at)


RSS_URL_STOPWORDS = {
    'www', 'com', 'org', 'net', 'html', 'htm', 'amp', 'article', 'news',
    'post', 'posts', 'the', 'and', 'for', 'with', 'from', 'into', 'after',
}


def _rss_candidate_links(entry):
    candidates = []

    def add(value):
        value = (value or '').strip()
        if value.startswith(('http://', 'https://')) and value not in candidates:
            candidates.append(value)

    link_value = entry.get('link', '')
    if isinstance(link_value, dict):
        add(link_value.get('href'))
    else:
        add(link_value)
    for link in entry.get('links', []) or []:
        if isinstance(link, dict):
            add(link.get('href'))
    add(entry.get('id'))
    add(entry.get('guid'))
    return candidates


def _rss_url_match_score(title, url):
    title_tokens = {
        token for token in re.findall(r'[a-z0-9]{3,}', (title or '').lower())
        if token not in RSS_URL_STOPWORDS
    }
    path_tokens = {
        token for token in re.findall(r'[a-z0-9]{3,}', urlparse(url).path.lower())
        if token not in RSS_URL_STOPWORDS
    }
    return len(title_tokens & path_tokens)


def _select_rss_entry_link(entry, title):
    candidates = _rss_candidate_links(entry)
    if not candidates:
        return '', {}
    preferred = candidates[0]
    scored = [(url, _rss_url_match_score(title, url)) for url in candidates]
    best_url, best_score = max(scored, key=lambda item: item[1])
    preferred_score = dict(scored)[preferred]
    if best_url != preferred and best_score >= 2 and best_score > preferred_score:
        return best_url, {
            'source_url_original': preferred,
            'source_url_repaired': True,
            'source_url_repair_reason': 'rss_guid_title_match',
        }
    return preferred, {}

# ============================================================
# 采集
# ============================================================

def _parse_rss_text(cfg, text):
    """解析 RSS/Atom 文本，返回事件列表。feedparser 自动处理编码/日期标准化。"""
    if not text: return []
    text = text.strip()
    if not any(text.startswith(x) or x in text[:300] for x in ['<?xml', '<rss', '<feed']):
        return []

    try:
        parsed = feedparser.parse(text)
    except Exception:
        return []

    results = []
    qualified_results = []
    candidate_results = []
    max_items = cfg.get('max', 8)
    max_scan = cfg.get('max_scan', max_items)
    scanned = 0
    scope_managed = _is_vertical_source(cfg) or cfg.get('source_role') == 'deep_trend'
    scope_stats = {
        'feed_entries': len(parsed.entries),
        'recent_items': 0,
        'qualified': 0,
        'candidate': 0,
        'filtered': 0,
        'filter_reasons': {},
    }

    for entry in parsed.entries:
        if not scope_managed and len(results) >= max_items: break
        if scanned >= max_scan: break
        scanned += 1

        # 标题
        title = (entry.get('title') or '').strip()
        if len(title) < 15 or is_blacklisted(title, official=_is_official_cfg(cfg) or bool(cfg.get('is_company'))):
            continue

        # 链接：优先主链接；若 guid/id 与标题明显更匹配则自动修复。
        link, link_repair = _select_rss_entry_link(entry, title)
        if not link:
            continue

        # 日期：feedparser 标准化时间，URL 日期兜底
        date_meta = _rss_date_metadata(entry, link)
        article_date = date_meta['published_at'] or None
        if article_date and not _recent_article_date(article_date, days=2):
            continue
        scope_stats['recent_items'] += 1

        # 图片：从 RSS media:content 或 media:thumbnail 提取
        image_url = ''
        mc = entry.get('media_content', [])
        if mc:
            for m in mc:
                if m.get('url'):
                    image_url = m['url']
                    break
        if not image_url:
            mt = entry.get('media_thumbnail', [])
            if mt and mt[0].get('url'):
                image_url = mt[0]['url']

        types = detect_event_types(title)
        summary_html = entry.get('summary') or entry.get('description') or ''
        source_excerpt = BeautifulSoup(summary_html, 'html.parser').get_text(' ', strip=True)[:600]
        item = _with_source_meta({
            'title': title,
            'url': link,
            'source': cfg.get('source', cfg.get('name', 'Google News')),
            'region': cfg['region'],
            'priority': cfg.get('priority', 1),
            'event_types': types,
            'article_date': article_date,
            'image_url': image_url,
            'source_excerpt': source_excerpt,
            'is_company': cfg.get('is_company', False),
            'company_name': cfg.get('company_name', ''),
            **link_repair,
            **date_meta,
        }, cfg)
        apply_scope_contract(item)
        if scope_managed:
            status = item.get('scope_status')
            if status == 'filtered':
                scope_stats['filtered'] += 1
                reason = item.get('scope_reason') or 'scope_filtered'
                reasons = scope_stats['filter_reasons']
                reasons[reason] = reasons.get(reason, 0) + 1
                continue
            if status == 'candidate':
                candidate_results.append(item)
                scope_stats['candidate'] += 1
                reason = item.get('scope_reason') or 'scope_candidate'
                reasons = scope_stats['filter_reasons']
                reasons[reason] = reasons.get(reason, 0) + 1
                continue
            if types[0] == 'other':
                item['event_types'] = ['strategy']
            qualified_results.append(item)
            scope_stats['qualified'] += 1
            continue
        if cfg.get('signal_only') and types[0] == 'other':
            continue
        results.append(item)
    if scope_managed:
        results = qualified_results[:max_items] + candidate_results[:max_items]
    cfg['_scope_stats'] = scope_stats
    return results


def _qualified_signal_count(items):
    return sum(
        1 for item in items
        if item.get('scope_status') == 'qualified'
        and (item.get('event_types') or ['other'])[0] != 'other'
    )


def fetch_rss(cfg):
    """顺序抓取（兼容旧接口，保留给 fetch_html 等调用方使用）"""
    text = fetch_url(cfg['url'])
    return _parse_rss_text(cfg, text)

# ============================================================
# HTML 备用采集（RSS 失效时的降级方案）
# ============================================================

# HTML 降级采集时过滤报告/评论类 URL（这类链接无情报价值）
HTML_SKIP_URL_PATTERNS = [
    '/reports/',          # 报告类
    '/review/',           # 回顾类
    'funding-review',     # 融资回顾
    'women-founders',     # 女性创始人报告
    'greater-china',      # 大中华区报告
    'southeast-asia',    # 东南亚报告
    'private-equity',     # PE 基金报告
    'lp-view',           # LP 视角（评论，非新闻）
    'startup-watch',     # 创业观察（长列表，非新闻）
]

HTML_SKIP_TITLE_PATTERNS = [
    'review', 'roundup', 'weekly recap', 'monthly recap',
    '2025 ', '2024 ', '2023 ',  # 历史回顾类标题
    ' Q4 ', ' Q1 ', ' Q2 ', ' Q3 ',  # 季度报告
]

OFFICIAL_SOURCE_LINK_PATTERNS = [
    '/news', '/press', '/media', '/investor', '/ir', '/financial', '/results',
    '/release', '/announcements', '/disclosure', '/reports', '/stories',
]

OFFICIAL_SOURCE_TITLE_PATTERNS = [
    'announces', 'announcement', 'launches', 'launched', 'partners', 'partnership',
    'expands', 'expansion', 'acquires', 'acquisition', 'results', 'revenue',
    'earnings', 'financial', 'quarter', 'annual', 'report', 'shareholder',
    'investor', 'strategy', 'strategic', 'platform', 'payment', 'commerce',
]

CHANGELOG_SOURCE_TITLE_PATTERNS = [
    'api', 'sdk', 'developer', 'changelog', 'release', 'released',
    'update', 'updates', 'new', 'beta', 'ga', 'graphql', 'webhook',
    'checkout', 'payments', 'merchant', 'admin', 'app', 'apps',
    'support', 'supports', 'enabled', 'enables', 'default', 'custom',
    'order', 'discount', 'import', 'duty', 'b2b',
]

OFFICIAL_SOURCE_SKIP_URL_PATTERNS = [
    'category=', '/about', '/products/', '/investor$', '/investors/$',
    '/quarterlyresults', '/annualreports', '/financial-information',
    '/corporategovernance', '/current-reports', '#results-center',
    '/sustainability/', '/financial-reports', '/presentations', '/reports/',
]

OFFICIAL_SOURCE_NAV_TITLES = {
    'investor relations', 'quarterly results', 'financial results',
    'financial information', 'current reports', 'main/about kaspi.kz',
    'kaspi.kz ecosystem', 'annual reports', 'corporate governance',
    'all stories business consumers & drivers people social impact & safety',
    'download grab media content', 'sustainability reports',
    'presentations and reports', 'sustainability',
}

OFFICIAL_SOURCE_NAV_PREFIXES = (
    'all stories business consumers',
    'latest stories business consumers',
)

HTML_SOURCES = [
    # DealStreetAsia RSS 停用（"Temporarily Disabled"），主站为 JS SPA
    # 低频尝试：只采集新闻类页面，报告/评论页已过滤
    {'name': 'DealStreetAsia', 'url': 'https://dealstreetasia.com/', 'source': 'DealStreetAsia', 'region': '亚太', 'priority': 1, 'source_tier': 'L2 垂直交易源', 'source_role': 'venture_media'},
    # e27：Angular JS + Cloudflare 双层保护，RSS + HTML 均无法采集，已移除
    # 官方/IR源：用于校准重点客户自身披露，低频但高可信
    {'name': 'Rakuten IR', 'url': 'https://global.rakuten.com/corp/news/press/?category=ir', 'source': 'Rakuten Group', 'region': '亚太', 'priority': 3, 'source_tier': 'L1 官方/IR源', 'source_role': 'official_ir', 'company_name': 'Rakuten', 'is_company': True, 'max': 4},
    {'name': 'MercadoLibre IR', 'url': 'https://investor.mercadolibre.com/news-and-events', 'source': 'MercadoLibre', 'region': '拉美', 'priority': 3, 'source_tier': 'L1 官方/IR源', 'source_role': 'official_ir', 'company_name': 'MercadoLibre', 'is_company': True, 'max': 4},
    {'name': 'Adyen IR', 'url': 'https://www.adyen.com/press-and-media', 'source': 'Adyen', 'region': '欧洲', 'priority': 3, 'source_tier': 'L1 官方/IR源', 'source_role': 'official_ir', 'company_name': 'Adyen', 'is_company': True, 'max': 4},
    {'name': 'Sea Newsroom', 'url': 'https://www.sea.com/media/news', 'source': 'Sea Limited', 'region': '亚太', 'priority': 3, 'source_tier': 'L1 官方/IR源', 'source_role': 'official_ir', 'company_name': 'Sea Limited', 'is_company': True, 'max': 4},
    {'name': 'Zalando IR', 'url': 'https://www.zalando.com/en/investor-relations/news-stories/', 'source': 'Zalando', 'region': '欧洲', 'priority': 3, 'source_tier': 'L1 官方/IR源', 'source_role': 'official_ir', 'company_name': 'Zalando', 'is_company': True, 'max': 4},
    {'name': 'Allegro Newsroom', 'url': 'https://allegro.eu/newsroom', 'source': 'Allegro', 'region': '欧洲', 'priority': 3, 'source_tier': 'L1 官方/IR源', 'source_role': 'official_ir', 'company_name': 'Allegro', 'is_company': True, 'max': 4},
    {'name': 'Kaspi.kz IR', 'url': 'https://ir.kaspi.kz/news-releases/', 'source': 'Kaspi.kz', 'region': '中东', 'priority': 3, 'source_tier': 'L1 官方/IR源', 'source_role': 'official_ir', 'company_name': 'Kaspi.kz', 'is_company': True, 'max': 4},
    {'name': 'Naver Press', 'url': 'https://www.navercorp.com/en/media/pressReleases', 'source': 'Naver', 'region': '亚太', 'priority': 3, 'source_tier': 'L1 官方/IR源', 'source_role': 'official_ir', 'company_name': 'Naver', 'is_company': True, 'max': 4},
    {'name': 'Kakao Press', 'url': 'https://www.kakaocorp.com/page/detail/pr?lang=en', 'source': 'Kakao', 'region': '亚太', 'priority': 3, 'source_tier': 'L1 官方/IR源', 'source_role': 'official_ir', 'company_name': 'Kakao', 'is_company': True, 'max': 4},
    {'name': 'HKTVmall IR News', 'url': 'https://ir.hktv.com.hk/media-news', 'source': 'HKTVmall', 'region': '亚太', 'priority': 3, 'source_tier': 'L1 官方/IR源', 'source_role': 'official_ir', 'company_name': 'HKTVmall', 'is_company': True, 'max': 4},
    {'name': 'U-NEXT News', 'url': 'https://unext-hd.co.jp/newsrelease/', 'source': 'U-NEXT', 'region': '亚太', 'priority': 3, 'source_tier': 'L1 官方/IR源', 'source_role': 'official_ir', 'company_name': 'U-NEXT', 'is_company': True, 'max': 4},
    {'name': 'Square Enix IR News', 'url': 'https://www.hd.square-enix.com/eng/ir/irnews/', 'source': 'Square Enix', 'region': '亚太', 'priority': 3, 'source_tier': 'L1 官方/IR源', 'source_role': 'official_ir', 'company_name': 'Square Enix', 'is_company': True, 'max': 4},
    {'name': 'Jumia Newsroom', 'url': 'https://group.jumia.com/news', 'source': 'Jumia', 'region': '非洲', 'priority': 3, 'source_tier': 'L1 官方/IR源', 'source_role': 'official_ir', 'company_name': 'Jumia', 'is_company': True, 'max': 4},
    # 2026-08 补缺：JD/Yahoo/Tabby/Cyberagent 官方源（Google News 两路都空，补官方披露）
    {'name': 'JD.com IR', 'url': 'https://ir.jd.com/news-releases', 'source': 'JD.com', 'region': '中资', 'priority': 2, 'source_tier': 'L1 官方/IR源', 'source_role': 'official_ir', 'company_name': 'JD.com', 'is_company': True, 'max': 4},
    {'name': 'Yahoo Press', 'url': 'https://www.yahooinc.com/press/', 'source': 'Yahoo', 'region': '亚太', 'priority': 1, 'source_tier': 'L1 官方/IR源', 'source_role': 'official_ir', 'company_name': 'Yahoo', 'is_company': True, 'max': 4},
    {'name': 'Tabby Press', 'url': 'https://www.tabby.ai/press/', 'source': 'Tabby', 'region': '中东', 'priority': 2, 'source_tier': 'L1 官方/IR源', 'source_role': 'official_ir', 'company_name': 'Tabby', 'is_company': True, 'max': 4},
]

def _is_official_cfg(cfg):
    return cfg.get('source_tier') == 'L1 官方/IR源' or cfg.get('source_role') == 'official_ir'


def _is_changelog_cfg(cfg):
    return cfg.get('source_type') in {'changelog', 'developer_changelog'}


def _official_link_allowed(link, cfg):
    link_lower = (link or '').lower().rstrip('/')
    if any(pattern in link_lower for pattern in OFFICIAL_SOURCE_SKIP_URL_PATTERNS):
        return False
    if _is_changelog_cfg(cfg):
        return True
    patterns = [p.lower() for p in cfg.get('include_url_patterns', [])] or OFFICIAL_SOURCE_LINK_PATTERNS
    return any(pattern in link_lower for pattern in patterns)


def _official_title_allowed(title, cfg):
    clean_title = ' '.join((title or '').split())
    title_lower = clean_title.lower()
    if not clean_title or title_lower in OFFICIAL_SOURCE_NAV_TITLES:
        return False
    if any(title_lower.startswith(prefix) for prefix in OFFICIAL_SOURCE_NAV_PREFIXES):
        return False
    if len(clean_title) < 18 or len(clean_title) > 180:
        return False
    if _is_changelog_cfg(cfg):
        return any(pattern in title_lower for pattern in CHANGELOG_SOURCE_TITLE_PATTERNS)
    company = cfg.get('company_name') or cfg.get('source') or cfg.get('name', '')
    aliases = COMPANY_ALIASES.get(company, [company])
    has_alias = _title_mentions_aliases(clean_title, aliases)
    has_signal = any(pattern in title_lower for pattern in OFFICIAL_SOURCE_TITLE_PATTERNS)
    has_year = bool(re.search(r'\\b20\\d{2}\\b', title_lower))
    return has_alias or has_signal or has_year


def _select_official_articles(soup, cfg):
    selectors = [
        'article', '[class*=news]', '[class*=press]', '[class*=release]',
        '[class*=story]', '[class*=card]', '[class*=item]', 'a[href]'
    ]
    seen = set()
    articles = []
    base_host = urlparse(cfg['url']).netloc.lower().removeprefix('www.')
    for sel in selectors:
        for node in soup.select(sel):
            link_node = node if getattr(node, 'name', '') == 'a' else node.select_one('a[href]')
            href = (link_node.get('href') or '').strip() if link_node else ''
            if not href or href.startswith('#') or href.startswith('javascript'):
                continue
            absolute = urljoin(cfg['url'], href)
            parsed = urlparse(absolute)
            host = parsed.netloc.lower().removeprefix('www.')
            if not parsed.scheme.startswith('http') or not host or host != base_host:
                continue
            if not _official_link_allowed(absolute, cfg):
                continue
            # 优先取节点内嵌标题元素（卡片式整卡链接会带摘要文本超长），
            # 选择器与 fetch_html 循环标题提取保持一致，无标题元素时回退整卡文本
            title_el = node.select_one('h2,h3,h4,h5,.title,.entry-title,.post-title,.article-title')
            text_value = ' '.join((title_el or node).get_text(' ', strip=True).split())
            if not _official_title_allowed(text_value, cfg):
                continue
            if absolute in seen:
                continue
            seen.add(absolute)
            articles.append(node)
    return articles


def _extract_date_from_url(url):
    """从 URL 提取日期兜底，如 /2026/04/15/"""
    m = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', url)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r'(?<!\d)(20\d{2})[-_.](\d{2})[-_.](\d{2})(?!\d)', url or '')
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r'(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)', url or '')
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


_EN_MONTHS = {
    'jan': 1, 'january': 1,
    'feb': 2, 'february': 2,
    'mar': 3, 'march': 3,
    'apr': 4, 'april': 4,
    'may': 5,
    'jun': 6, 'june': 6,
    'jul': 7, 'july': 7,
    'aug': 8, 'august': 8,
    'sep': 9, 'sept': 9, 'september': 9,
    'oct': 10, 'october': 10,
    'nov': 11, 'november': 11,
    'dec': 12, 'december': 12,
}


def _format_date_parts(year, month, day):
    try:
        dt = datetime(int(year), int(month), int(day))
    except (TypeError, ValueError):
        return None
    return dt.strftime('%Y-%m-%d')


def _extract_date_from_text(text):
    """Extract common official/IR date formats from titles or list text."""
    clean = ' '.join((text or '').split())
    if not clean:
        return None
    m = re.search(r'\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b', clean)
    if m:
        return _format_date_parts(m.group(1), m.group(2), m.group(3))
    m = re.search(r'(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)', clean)
    if m:
        return _format_date_parts(m.group(1), m.group(2), m.group(3))
    month_names = '|'.join(sorted(_EN_MONTHS, key=len, reverse=True))
    m = re.search(
        rf'\b({month_names})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+(20\d{{2}})\b',
        clean,
        flags=re.I,
    )
    if m:
        return _format_date_parts(m.group(3), _EN_MONTHS[m.group(1).lower()], m.group(2))
    m = re.search(
        rf'\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_names})\.?\s+(20\d{{2}})\b',
        clean,
        flags=re.I,
    )
    if m:
        return _format_date_parts(m.group(3), _EN_MONTHS[m.group(2).lower()], m.group(1))
    return None


def _extract_recent_month_day_date(text):
    clean = ' '.join((text or '').split())
    if not clean:
        return None
    month_names = '|'.join(sorted(_EN_MONTHS, key=len, reverse=True))
    m = re.search(
        rf'\b({month_names})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b',
        clean,
        flags=re.I,
    )
    if not m:
        return None
    now = _cn_now().date()
    month = _EN_MONTHS[m.group(1).lower()]
    day = int(m.group(2))
    for year in (now.year, now.year - 1):
        candidate = _format_date_parts(year, month, day)
        parsed = _parse_date(candidate)
        if parsed and parsed <= now:
            return candidate
    return None


def _extract_official_article_date_meta(title, link, node_text='', observed_at=None):
    node_full_date = _extract_date_from_text(node_text)
    candidates = [
        (_extract_date_from_url(link), 'url_path', 'high'),
        (_extract_date_from_text(title), 'title_text', 'medium'),
        (node_full_date, 'body_text', 'low'),
        (_extract_recent_month_day_date(node_text) if not node_full_date else None, 'body_month_day', 'low'),
    ]
    rejected = None
    for candidate, source, confidence in candidates:
        if not candidate:
            continue
        metadata = publication_metadata(candidate, source, confidence, observed_at=observed_at)
        if metadata['published_at']:
            return metadata
        if metadata.get('scheduled_at') and rejected is None:
            rejected = metadata
    return rejected or publication_metadata('', 'observed_at', 'observed', observed_at=observed_at)


def _extract_official_article_date(title, link, node_text=''):
    return _extract_official_article_date_meta(title, link, node_text)['published_at'] or None


def _same_host_url(base_url, href):
    absolute = urljoin(base_url, href or '')
    base_host = urlparse(base_url).netloc.lower().replace('www.', '')
    link_host = urlparse(absolute).netloc.lower().replace('www.', '')
    if not absolute.startswith('http') or base_host != link_host:
        return ''
    return absolute


def _select_changelog_items(soup, cfg):
    """Extract dated changelog links directly; card selectors are often noisy."""
    results = []
    seen = set()
    max_items = cfg.get('max', 4)
    max_scan = max(cfg.get('max_scan', 40), 100)

    for link_el in soup.select('a[href]')[:max_scan]:
        if len(results) >= max_items:
            break
        link = _same_host_url(cfg['url'], link_el.get('href'))
        if not link or link in seen:
            continue
        if any(p in link.lower() for p in HTML_SKIP_URL_PATTERNS):
            continue
        if any(p in link.lower() for p in OFFICIAL_SOURCE_SKIP_URL_PATTERNS):
            continue

        title = link_el.get_text(' ', strip=True).lstrip('•·-–— ').strip()
        if not _official_title_allowed(title, cfg) or is_blacklisted(title, official=True):
            continue

        node_text = ''
        date_meta = publication_metadata('', 'observed_at', 'observed')
        article_date = None
        for parent in [link_el] + list(link_el.parents)[:6]:
            node_text = ' '.join(parent.get_text(' ', strip=True).split())
            date_meta = _extract_official_article_date_meta(title, link, node_text)
            article_date = date_meta['published_at'] or None
            if article_date:
                break
        # changelog 列表页同官方源：提取不到日期视为最新条目保留，能提取到且旧才滤
        if article_date and not _recent_article_date(article_date, days=2):
            continue

        types = detect_event_types(title)
        if types == ['other']:
            types = ['strategy']

        seen.add(link)
        results.append(_with_source_meta({
            'title': title,
            'url': link,
            'source': cfg.get('source', cfg.get('name', '')),
            'region': cfg['region'],
            'priority': cfg.get('priority', 1),
            'event_types': types,
            'article_date': article_date,
            'is_company': cfg.get('is_company', False),
            'company_name': cfg.get('company_name', ''),
            **date_meta,
        }, cfg))

    return results


def fetch_company_news(cfg):
    """
    从 Google News RSS 抓取特定公司的新闻
    只取当天/昨天的 + 有信号的事件 + 每公司最多3条
    """
    import urllib.parse
    query = urllib.parse.quote(cfg['query'] + ' when:2d')
    url = f'https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en'
    body = fetch_url(url)
    cfg['_last_fetch_status'] = 'success' if body else 'failed'
    if not body: return []

    if not any(body.strip().startswith(x) or x in body[:300] for x in ['<?xml', '<rss', '<feed']):
        return []

    try:
        parsed = feedparser.parse(body)
    except Exception:
        return []

    today = _cn_today()
    yesterday = (_cn_now() - timedelta(days=1)).strftime('%Y-%m-%d')
    allowed_dates = {today, yesterday}

    results = []
    seen_company_events = []
    publisher_counts = {}
    max_items = cfg.get('max', 3)
    max_other = cfg.get('max_other', 1)
    other_count = 0

    for entry in parsed.entries:
        if len(results) >= max_items: break  # 每公司最多N条

        title = (entry.get('title') or '').strip()
        if len(title) < 15: continue
        publisher = _extract_title_publisher(title)

        if not _title_mentions_company(title, cfg):
            continue

        # 基础噪音过滤
        title_lower = f"{title} {publisher}".lower()
        if any(kw in title_lower for kw in COMPANY_BLACKLIST): continue
        if _is_low_signal_company_title(title):
            continue
        if cfg.get('region') == '中资' and not _is_chinese_outbound_title(title):
            continue
        if publisher and publisher_counts.get(publisher.lower(), 0) >= 1:
            continue

        link, link_repair = _select_rss_entry_link(entry, title)
        if not link: continue

        # 日期过滤：RSS日期优先，URL日期兜底
        date_meta = _rss_date_metadata(entry, link)
        article_date = date_meta['published_at'] or None
        if article_date and article_date not in allowed_dates:
            continue

        types = detect_event_types(title)
        if types[0] == 'other':
            if other_count >= max_other:
                continue
            other_count += 1

        # 图片：从 RSS media:content 或 media:thumbnail
        image_url = ''
        mc = entry.get('media_content', [])
        if mc:
            for m in mc:
                if m.get('url'):
                    image_url = m['url']
                    break
        if not image_url:
            mt = entry.get('media_thumbnail', [])
            if mt and mt[0].get('url'):
                image_url = mt[0]['url']

        item = _with_source_meta({
            'title': title,
            'url': link,
            'source': 'Google News',
            'source_detail': publisher,
            'publisher': publisher,
            'region': cfg['region'],
            'priority': cfg.get('priority', 1),
            'event_types': types,
            'article_date': article_date,
            'is_company': True,
            'company_name': cfg['name'],
            'origin_source_id': cfg.get('id') or cfg['name'],
            'observation_entity_id': cfg.get('entity_id') or cfg.get('id') or cfg['name'],
            'discovery_source': 'google_news',
            'publisher_source': publisher,
            'image_url': image_url,
            **link_repair,
            **date_meta,
        }, cfg)
        if any(_is_same_event(item, existing) for existing in seen_company_events):
            continue
        seen_company_events.append(item)
        if publisher:
            publisher_key = publisher.lower()
            publisher_counts[publisher_key] = publisher_counts.get(publisher_key, 0) + 1
        results.append(item)
    return results


def fetch_html(cfg):
    """从 HTML 页面提取文章列表（降级方案，针对各站点结构定制）"""
    body = fetch_url(cfg['url'])
    cfg['_last_fetch_status'] = 'success' if body else 'failed'
    if not body: return []

    soup = BeautifulSoup(body, 'html.parser')
    results = []

    # 根据来源选择器定制
    source = cfg['source']
    if _is_changelog_cfg(cfg):
        return _select_changelog_items(soup, cfg)
    if _is_official_cfg(cfg):
        articles = _select_official_articles(soup, cfg)
    elif source == 'DealStreetAsia':
        # DealStreetAsia: JS SPA，文章在特定 div 结构中
        # 尝试多种文章容器选择器
        selectors = [
            'article', '.post-card', '.deal-card', '.startup-card',
            '[class*=card]', '[class*=item]', '[class*=post]',
            '.listing article', '.archive article',
        ]
        articles = []
        for sel in selectors:
            found = soup.select(sel)
            if found:
                articles = found
                break
        # 也尝试从链接模式找文章：/2026/ 或包含 deal/startup/invest
        if not articles:
            all_links = soup.select('a[href]')
            art_links = []
            for a in all_links:
                href = a.get('href', '')
                if '/202' in href and any(x in href for x in ['/deals/', '/startups/', '/funding/', '/invest/']):
                    parent = a.find_parent()
                    if parent:
                        art_links.append(parent)
            if art_links:
                articles = art_links
    elif source == 'e27':
        # e27: 文章在特定列表结构中
        selectors = [
            'article', '.post', '.listing-item', '.article-item',
            '[class*=article]', '[class*=post]',
        ]
        articles = []
        for sel in selectors:
            found = soup.select(sel)
            if found:
                articles = found
                break
        # 也从链接中提取：e27.co/20xx/ 模式
        if not articles:
            all_links = soup.select('a[href]')
            art_links = []
            for a in all_links:
                href = a.get('href', '')
                if '/20' in href and ('startup' in href or 'funding' in href or 'investment' in href or 'series' in href):
                    parent = a.find_parent()
                    if parent:
                        art_links.append(parent)
            if art_links:
                articles = art_links
    else:
        # 通用回退
        articles = soup.select('article') or soup.select('.post') or soup.select('.article')
        if not articles:
            articles = soup.select('a[href]')

    max_items = cfg.get('max', 8)
    max_scan = cfg.get('max_scan', 15)
    for art in articles[:max_scan]:
        if len(results) >= max_items:
            break
        # 提取标题和链接
        title_el = art.select_one('h2,h3,h4,h5,.title,.entry-title,.post-title,.article-title') or art
        title = title_el.get_text(' ', strip=True).lstrip('•·-–— ').strip()
        if len(title) < 15 or is_blacklisted(title, official=_is_official_cfg(cfg) or bool(cfg.get('is_company'))): continue

        link_el = art.select_one('a') or (title_el if isinstance(title_el, object) else None)
        link = ''
        if link_el:
            link = (link_el.get('href') or '').strip()
        if not link or link.startswith('#') or link.startswith('javascript'): continue

        # 过滤非文章链接
        if any(x in link for x in ['/category/', '/tag/', '/author/', '/page/',
                                    'subscribe', 'newsletter', 'contact', '/cdn-cgi/']): continue
        # 过滤报告/评论类 URL
        if any(p in link.lower() for p in HTML_SKIP_URL_PATTERNS): continue
        # 过滤报告类标题
        title_lower = title.lower()
        if any(p.lower() in title_lower for p in HTML_SKIP_TITLE_PATTERNS): continue
        # 只保留绝对 URL 或同源链接
        if not link.startswith('http'):
            if link.startswith('/'):
                base = cfg['url'].split('/')[2]  # 提取域名
                link = 'https://' + base + link

        date_meta = publication_metadata('', 'observed_at', 'observed')
        article_date = None
        if _is_official_cfg(cfg):
            node_text = ' '.join(art.get_text(' ', strip=True).split())
            date_meta = _extract_official_article_date_meta(title, link, node_text)
            article_date = date_meta['published_at'] or None
            # 官方源列表页第一屏即最新：能提取到日期才按窗口过滤，提取不到视为新稿保留
            if article_date and not _recent_article_date(article_date, days=2):
                continue

        types = detect_event_types(title)
        results.append(_with_source_meta({
            'title': title,
            'url': link,
            'source': cfg.get('source', cfg.get('name', 'Google News')),
            'region': cfg['region'],
            'priority': cfg.get('priority', 1),
            'event_types': types,
            'article_date': article_date,
            'is_company': cfg.get('is_company', False),
            'company_name': cfg.get('company_name', ''),
            **date_meta,
        }, cfg))

    return results

# ============================================================
# 智能过滤：控制每天总条数，优先保留高价值事件
# ============================================================

MAX_DAILY = 40      # 每天最多保留 40 条
MAX_PER_REGION = 12  # 每个区域最多保留多少条

def smart_filter(items):
    """
    策略：
    1. 所有融资/并购/财报事件全部保留
    2. 官方/IR 公司事件全部保留，Google News 公司 other 只有限补漏
    3. 其他事件按 priority 排序，每天最多 40 条（通用部分）
    """
    # 信号事件（全部保留）
    signal = [it for it in items if it['event_types'][0] != 'other']
    company = [
        it for it in items
        if it.get('is_company') and it['event_types'][0] == 'other' and _is_official_company_source(it)
    ]
    # 非信号、非公司事件（按 priority 排序，取剩余名额）
    others = [it for it in items if it['event_types'][0] == 'other' and not it.get('is_company')]
    others.sort(key=lambda x: x.get('priority', 1), reverse=True)

    result = []
    used_urls = set()
    seen_items = []

    def _add_unique(it):
        if it['url'] in used_urls:
            return False
        if any(_is_same_event(it, existing) for existing in seen_items):
            return False
        result.append(it)
        seen_items.append(it)
        if it['url']:
            used_urls.add(it['url'])
        return True

    # 1. 官方/IR 公司事件（高可信，低频保留）
    company_sorted = sorted(
        company,
        key=lambda x: (
            0 if x['event_types'][0] != 'other' else 1,
            -x.get('priority', 1),
            x.get('company_name', ''),
        )
    )
    company_counts = {}
    company_other_counts = {}
    for it in company_sorted:
        cname = it.get('company_name', '')
        if cname:
            if company_counts.get(cname, 0) >= 3:
                continue
            if it['event_types'][0] == 'other' and company_other_counts.get(cname, 0) >= 1:
                continue
        _add_unique(it)
        if cname:
            company_counts[cname] = company_counts.get(cname, 0) + 1
            if it['event_types'][0] == 'other':
                company_other_counts[cname] = company_other_counts.get(cname, 0) + 1

    # 2. 全部信号事件
    for it in signal:
        _add_unique(it)

    # 3. 非信号事件补足到 MAX_DAILY，每个区域最多 MAX_PER_REGION 条
    regions = list(dict.fromkeys(it['region'] for it in items))  # 保持原始顺序
    for region in regions:
        remaining = MAX_DAILY - len(result)
        if remaining <= 0: break
        region_others = [it for it in others if it['region'] == region and it['url'] not in used_urls]
        signal_in_region = sum(1 for it in result if it['region'] == region)
        max_other_for_region = max(0, MAX_PER_REGION - signal_in_region)
        for it in region_others[:max_other_for_region]:
            _add_unique(it)
            if len(result) >= MAX_DAILY: break

    return result


def dedupe_events_by_day(all_events):
    """清理历史 events.json 中同一天的重复/低信号事件，保持原始顺序。"""
    cleaned = {}
    removed = 0
    reasons = {
        'missing_company_alias': 0,
        'low_signal_company_title': 0,
        'same_day_duplicate': 0,
        'company_daily_cap': 0,
    }
    for date_key, events in all_events.items():
        kept = []
        company_counts = {}
        for event in events:
            event.setdefault('date', date_key)
            if event.get('is_company') and not _title_mentions_aliases(event.get('title', ''), _get_company_aliases(event.get('company_name', ''))):
                removed += 1
                reasons['missing_company_alias'] += 1
                continue
            if event.get('is_company') and _is_low_signal_company_title(event.get('title', '')):
                removed += 1
                reasons['low_signal_company_title'] += 1
                continue
            if any(_is_same_event(event, existing) for existing in kept):
                # 记录被合并来源，保留可追溯性（原 URL 不丢失）
                match = next(existing for existing in kept if _is_same_event(event, existing))
                match.setdefault('merged_from', [])
                if event.get('url') and event['url'] not in match['merged_from']:
                    match['merged_from'].append(event['url'])
                removed += 1
                reasons['same_day_duplicate'] += 1
                continue
            company_name = event.get('company_name', '')
            if event.get('is_company') and company_name:
                if company_counts.get(company_name, 0) >= 3:
                    removed += 1
                    reasons['company_daily_cap'] += 1
                    continue
                company_counts[company_name] = company_counts.get(company_name, 0) + 1
            kept.append(event)
        cleaned[date_key] = kept
    return cleaned, removed, reasons


def apply_event_storage_policy(all_events):
    """Keep the complete event archive; presentation applies its own windows."""
    return all_events

# ============================================================
# MiniMax API（主力）
# ============================================================

def configure_minimax():
    """配置 MiniMax API，优先使用"""
    key = os.environ.get('MINIMAX_API_KEY')
    model = os.environ.get('MINIMAX_MODEL', 'MiniMax-M2.7')
    print(f"  🔑 MINIMAX_API_KEY: {'已设置 (' + str(len(key)) + ' 字符)' if key else '未设置 ❌'}")
    if not key:
        print("  ⚠️  未设置 MINIMAX_API_KEY，将降级使用豆包")
        return False
    if len(key) < 10:
        print(f"  ❌ MINIMAX_API_KEY 长度异常（{len(key)} 字符），降级使用豆包")
        return False
    print(f"  ✅ MiniMax API 配置检查通过，模型: {model}")
    return True


def analyze_events_minimax(items):
    """
    使用 MiniMax 大模型分析新闻事件（OpenAI 兼容格式）
    模型: MiniMax-Text-01
    """
    import os
    api_key = os.environ.get('MINIMAX_API_KEY')
    if not api_key:
        print("  ⚠️  未设置 MINIMAX_API_KEY")
        return None

    url = "https://api.minimax.chat/v1/text/chatcompletion_v2"
    model = os.environ.get('MINIMAX_MODEL', 'MiniMax-M2.7')
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json"
    }

    news = [{'title': it['title'], 'url': it['url'], 'source': it['source'], 'region': it.get('region','')} for it in items]
    prompt = AI_SYSTEM_PROMPT + "\n" + AI_EXAMPLES + "\n\n分析以下事件，返回JSON数组：\n" + json.dumps(news, ensure_ascii=False) + "\n\n返回JSON："

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
        "temperature": 0.1
    }

    # 创建不使用代理的session（MiniMax不需要代理）
    session = requests.Session()
    session.trust_env = False  # 禁用环境变量代理

    for attempt in range(3):
        try:
            resp = session.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code == 429:
                wait = (attempt + 1) * 10
                print(f"  ⚠️  MiniMax API 配额耗尽（429），等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            if resp.status_code == 400:
                print(f"  ⚠️  MiniMax API 请求错误（400）: {resp.text[:200]}，尝试降级...")
                return None
            if resp.status_code != 200:
                print(f"  ❌ MiniMax API HTTP {resp.status_code}: {resp.text[:300]}")
                return None
            data = resp.json()
            text = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            if not text:
                print("  ⚠️  MiniMax 返回空内容: " + str(data))
                return None
            for m in ['```json', '```']:
                if m in text:
                    parts = text.split(m)
                    for p in parts[1:]:
                        text = p.strip()
                        if text.endswith('```'):
                            text = text[:-3].strip()
                        break
                    break
            result = json.loads(re.sub(r'^json\s*', '', text, flags=re.I))
            if isinstance(result, list):
                result = [r for r in result if _is_http_url(r.get('url')) and r.get('summary_short')]
            print(f"  ✅ MiniMax 分析成功，{len(result) if isinstance(result, list) else 0} 条")
            return result
        except requests.exceptions.Timeout:
            print(f"  ⚠️  MiniMax API 超时（60s），快速失败，跳过该批次")
            return None
        except json.JSONDecodeError as e:
            if attempt < 2:
                wait = (attempt + 1) * 5
                print(f"  ⚠️  MiniMax 返回非JSON，尝试修正解析...")
                import re as re2
                match = re2.search(r'\[[\s\S]*\]', text if 'text' in dir() else '')
                if match:
                    try:
                        result = json.loads(match.group())
                        result = [r for r in result if isinstance(r, dict) and _is_http_url(r.get('url'))]
                        if result:
                            print(f"  ✅ 修正解析成功，提取 {len(result)} 条")
                            return result
                    except: pass
                print(f"  解析失败，等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            print(f"  ❌ MiniMax JSON 解析最终失败")
            return None
        except Exception as e:
            if attempt < 2:
                wait = (attempt + 1) * 5
                print(f"  ⚠️  MiniMax API 调用失败（{type(e).__name__}），等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            print(f"  ❌ MiniMax API 最终失败: {type(e).__name__} {str(e)[:200]}")
            return None
    return None


# ============================================================
# 豆包分析（备份）
# ============================================================

def configure_doubao():
    key = os.environ.get('DOUBAO_API_KEY')
    model = os.environ.get('DOUBAO_MODEL', 'ep-20260409223830-dnt5b')
    print(f"  🔑 DOUBAO_API_KEY: {'已设置 (' + str(len(key)) + ' 字符)' if key else '未设置 ❌'}")
    if not key:
        print("  ❌ 未找到 DOUBAO_API_KEY，跳过 AI 分析")
        return False
    if len(key) < 10:
        print(f"  ❌ DOUBAO_API_KEY 长度异常（{len(key)} 字符），跳过 AI 分析")
        return False
    print(f"  ✅ 豆包 API 配置检查通过，模型: {model}")
    return True


def configure_deepseek():
    """配置 DeepSeek API，优先使用"""
    key = os.environ.get('DEEPSEEK_API_KEY')
    model = os.environ.get('DEEPSEEK_MODEL', 'deepseek-chat')
    print(f"  🔑 DEEPSEEK_API_KEY: {'已设置 (' + str(len(key)) + ' 字符)' if key else '未设置 ❌'}")
    if not key:
        print("  ⚠️  未设置 DEEPSEEK_API_KEY，将降级使用豆包")
        return False
    if len(key) < 10:
        print(f"  ❌ DEEPSEEK_API_KEY 长度异常（{len(key)} 字符），降级使用豆包")
        return False
    print(f"  ✅ DeepSeek API 配置检查通过，模型: {model}")
    return True


def configure_ark():
    """配置火山方舟 DeepSeek V4 Flash（ARK），价格约为官方 DeepSeek 的 1/6"""
    key = os.environ.get('ARK_API_KEY')
    model = os.environ.get('ARK_MODEL') or 'ep-20260827101830-qgtm4'
    print(f"  🔑 ARK_API_KEY: {'已设置 (' + str(len(key)) + ' 字符)' if key else '未设置 ❌'}")
    if not key:
        print("  ⚠️  未设置 ARK_API_KEY，跳过方舟 V4 Flash")
        return False
    if len(key) < 10:
        print(f"  ❌ ARK_API_KEY 长度异常（{len(key)} 字符），跳过方舟 V4 Flash")
        return False
    print(f"  ✅ 方舟 V4 Flash 配置检查通过，模型: {model}")
    return True


def analyze_events_ark(items):
    """
    使用火山方舟 DeepSeek V4 Flash 分析新闻事件（OpenAI 兼容 API）
    模型：ep-20260827101830-qgtm4（方舟价格约为官方 1/6）
    """
    api_key = os.environ.get('ARK_API_KEY')
    if not api_key:
        print("  ⚠️  未设置 ARK_API_KEY")
        return None

    url = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    model = os.environ.get('ARK_MODEL') or 'ep-20260827101830-qgtm4'
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json"
    }

    news = [{'title': it['title'], 'url': it['url'], 'source': it['source'], 'region': it.get('region','')} for it in items]
    prompt = AI_SYSTEM_PROMPT + "\n" + AI_EXAMPLES + "\n\n分析以下事件，返回JSON数组：\n" + json.dumps(news, ensure_ascii=False) + "\n\n返回JSON："

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
        "temperature": 0.1,
        "thinking": {"type": "disabled"},  # 关深度思考必须用 thinking 参数（reasoning:{effort:none} 会被静默忽略），降延迟、省 reasoning 计费
    }

    for attempt in range(2):
        try:
            # 关思考后响应快；60s 余量覆盖网络波动与长正文生成
            resp = _LLM_SESSION.post(url, headers=headers, json=payload, timeout=(10, 60))
            if resp.status_code == 429:
                wait = (attempt + 1) * 10
                print("  ⚠️  方舟 API 配额耗尽（429），等待 " + str(wait) + "s 后重试...")
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                print(f"  ❌ 方舟 API HTTP {resp.status_code}: {resp.text[:300]}")
                return None
            data = resp.json()
            text = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            if not text:
                print("  ⚠️  方舟返回空内容: " + str(data))
                return None
            for m in ['```json', '```']:
                if m in text:
                    parts = text.split(m)
                    for p in parts[1:]:
                        text = p.strip()
                        if text.endswith('```'):
                            text = text[:-3].strip()
                        break
                    break
            result = json.loads(re.sub(r'^json\s*', '', text, flags=re.I))
            if isinstance(result, list):
                result = [r for r in result if _is_http_url(r.get('url')) and r.get('summary_short')]
            return result
        except requests.exceptions.Timeout:
            print(f"  ⚠️  方舟 API 超时（30s），快速失败，跳过该批次")
            return None
        except json.JSONDecodeError as e:
            if attempt < 2:
                wait = (attempt + 1) * 5
                print(f"  ⚠️  方舟返回非JSON，尝试修正解析...")
                import re as re2
                match = re2.search(r'\[[\s\S]*\]', text if 'text' in dir() else '')
                if match:
                    try:
                        result = json.loads(match.group())
                        result = [r for r in result if isinstance(r, dict) and _is_http_url(r.get('url'))]
                        if result:
                            print(f"  ✅ 修正解析成功，提取 {len(result)} 条")
                            return result
                    except: pass
                print(f"  解析失败，等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            print(f"  ❌ 方舟 JSON 解析最终失败")
            return None
        except Exception as e:
            if attempt < 2:
                wait = (attempt + 1) * 5
                print(f"  ⚠️  方舟 API 调用失败（{type(e).__name__}），等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            print(f"  ❌ 方舟 API 最终失败: {type(e).__name__} {str(e)[:200]}")
            return None
    return None


def analyze_events_deepseek(items):
    """
    使用 DeepSeek 大模型分析新闻事件（OpenAI 兼容 API）
    模型：deepseek-chat
    """
    api_key = os.environ.get('DEEPSEEK_API_KEY')
    if not api_key:
        print("  ⚠️  未设置 DEEPSEEK_API_KEY")
        return None

    url = "https://api.deepseek.com/v1/chat/completions"
    model = os.environ.get('DEEPSEEK_MODEL', 'deepseek-chat')
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json"
    }

    news = [{'title': it['title'], 'url': it['url'], 'source': it['source'], 'region': it.get('region','')} for it in items]
    prompt = AI_SYSTEM_PROMPT + "\n" + AI_EXAMPLES + "\n\n分析以下事件，返回JSON数组：\n" + json.dumps(news, ensure_ascii=False) + "\n\n返回JSON："

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
        "temperature": 0.1
    }

    for attempt in range(2):
        try:
            resp = _LLM_SESSION.post(url, headers=headers, json=payload, timeout=(10, 20))
            if resp.status_code == 429:
                wait = (attempt + 1) * 10
                print("  ⚠️  DeepSeek API 配额耗尽（429），等待 " + str(wait) + "s 后重试...")
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                print(f"  ❌ DeepSeek API HTTP {resp.status_code}: {resp.text[:300]}")
                return None
            data = resp.json()
            text = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            if not text:
                print("  ⚠️  DeepSeek 返回空内容: " + str(data))
                return None
            for m in ['```json', '```']:
                if m in text:
                    parts = text.split(m)
                    for p in parts[1:]:
                        text = p.strip()
                        if text.endswith('```'):
                            text = text[:-3].strip()
                        break
                    break
            result = json.loads(re.sub(r'^json\s*', '', text, flags=re.I))
            if isinstance(result, list):
                result = [r for r in result if _is_http_url(r.get('url')) and r.get('summary_short')]
            return result
        except requests.exceptions.Timeout:
            print(f"  ⚠️  DeepSeek API 超时（30s），快速失败，跳过该批次")
            return None
        except json.JSONDecodeError as e:
            if attempt < 2:
                wait = (attempt + 1) * 5
                print(f"  ⚠️  DeepSeek 返回非JSON，尝试修正解析...")
                import re as re2
                match = re2.search(r'\[[\s\S]*\]', text if 'text' in dir() else '')
                if match:
                    try:
                        result = json.loads(match.group())
                        result = [r for r in result if isinstance(r, dict) and _is_http_url(r.get('url'))]
                        if result:
                            print(f"  ✅ 修正解析成功，提取 {len(result)} 条")
                            return result
                    except: pass
                print(f"  解析失败，等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            print(f"  ❌ DeepSeek JSON 解析最终失败")
            return None
        except Exception as e:
            if attempt < 2:
                wait = (attempt + 1) * 5
                print(f"  ⚠️  DeepSeek API 调用失败（{type(e).__name__}），等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            print(f"  ❌ DeepSeek API 最终失败: {type(e).__name__} {str(e)[:200]}")
            return None
    return None


# ============================================================
# AI 分析 Prompt 模板（Few-shot，输出稳定）
# ============================================================

AI_SYSTEM_PROMPT = """你是全球互联网科技情报分析师。受众是ICT从业者，关注：合作机会、供应链变化、预算流向。
每条事件输出9个字段：event_types（事件类型，见下）、content_overview（内容概要，1-2句客观复述事件本身发生了什么）、summary_short（一句话事实摘要）、reason（点评/为什么重要，ICT视角）、impact（影响谁）、insight_label（资金流向/合作机会/警示信号/背景补充）、trend_topic（所属趋势主题，如"中东FinTech赛道升温""拉美电商基建加速""欧洲AI融资热潮""东南亚新能源布局"等，15字以内）、canonical_company（事件主体的规范名，如"Mistral""Nubank""Cafeyn"——公司/产品/机构名本身，去修饰语、统一大小写；行业报告等无明确主体的事件填空字符串""）、canonical_key（事件的量化/识别锚点，统一格式：融资或财报填金额，数字+单位——金额低于1亿的用m为单位如"250m""5m"，1亿以上的用b为单位如"2.8b""830m"，保留小数不超过2位；并购填被收购方规范名如"Readly"；战略合作填合作对象规范名如"Qistas"；裁员填人数数字如"2000"；无合适锚点填空字符串""）。

event_types 判定规则（从事件实质判断，不要被标题里的英文单词误导）：
- funding：融资/投资/估值
- ma：并购/收购/入股
- earnings：财报/营收/利润/股价对财报的反应
- strategy：战略/合作/扩张/产品发布/运营/人事/监管
- industry_report：真正的行业研究报告/市场数据/榜单/趋势调查（如"2026东南亚数字银行报告"）。公司新闻里出现"report"表示"据报道"（如"Cursor...: Report"），归为 strategy，不要误判成研究报告
- other：以上都不符合
只能选一个，返回字符串。

content_overview 要求：用1-2句话客观描述事件本身——谁、做了什么、金额/数据、进展，必须从标题提炼事实，禁止写成价值判断或"为什么重要"式的话。比 summary_short 更完整，可含背景或后续进展，两者不得相同。
reason 要求：必须从标题提取公司名/产品名/技术名，组合地区+行业+具体机会描述，格式固定为"[地区][行业]具体描述"。禁止出现"无法判断""无法确定""待确认""相关"等模糊词。
impact 要求：指明具体受益方或受损方，如"东南亚电商平台""海湾主权基金""非洲移动支付商"，禁止"相关行业"。
score 打分规则（硬档位，禁止给档外分数）：10分仅限稀缺事件——国家级战略/投资（政府层面资金）、或并购金额≥$5B、或融资≥$500M且改变行业格局，其余一律封顶9；9分——非中美公司融资≥$100M、并购$1B-5B、战略级投资≥$1B；7-8分——融资$20M-100M、并购$100M-1B、重大战略扩张、裁员/关停；5-6分——财报盈利稳定、普通产品发布、常规战略动作；7-9分（强制，禁止给4-5）——财报亏损/下滑/暴跌；1-3分——微小事件（无金额量化、非关键公司、普通功能更新），不要把所有事件都打4分以上，分数从1开始有梯度。
只返回JSON数组，不要解释。"""

AI_EXAMPLES = """
示例1（融资大额）：
标题: "Mistral raises $830M, 9fin hits unicorn status"
输出: {"url":"","event_types":"funding","content_overview":"法国AI公司Mistral完成8.3亿美元融资，金融科技公司9fin同期晋级独角兽","summary_short":"Mistral获$830M融资，9fin晋级独角兽","reason":"欧洲AI独角兽获顶级融资，后续可能开放生态合作和API采购","impact":"AI基础设施供应商、云服务商、API集成商","insight_label":"资金流向","trend_topic":"欧洲AI融资热潮","score":9,"canonical_company":"Mistral","canonical_key":"830m"}

示例2（财报方向）：
标题: "Nubank Q1 revenue up 34% to $2.8B"
输出: {"url":"","event_types":"earnings","content_overview":"巴西数字银行Nubank一季度营收28亿美元，同比增长34%","summary_short":"Nubank营收$2.8B，同比+34%","reason":"拉美数字银行持续高增长，东南亚复制模式具有参考价值","impact":"拉美金融科技合作方、银行科技供应商","insight_label":"背景补充","trend_topic":"拉美FinTech高增长","score":6,"canonical_company":"Nubank","canonical_key":"2.8b"}

示例3（"Report"是"据报道"而非研报）：
标题: "Cursor To Open First India Office By 2026 End: Report"
输出: {"url":"","event_types":"strategy","content_overview":"AI编程公司Cursor计划在2026年底前开设印度首个办公室","summary_short":"Cursor计划2026年底开印度办公室","reason":"AI编程工具公司加速全球化布局，亚太开发者市场战略地位上升","impact":"印度开发者生态、AI工具渠道合作方","insight_label":"合作机会","trend_topic":"AI编程工具全球化","score":5,"canonical_company":"Cursor","canonical_key":""}

示例4（财报亏损必须高分档，禁止给4-5）：
标题: "Zaggle plunges 20% to hit lower circuit after Q1 profit slump"
输出: {"url":"","event_types":"earnings","content_overview":"印度金融科技SaaS公司Zaggle一季度利润大幅下滑，股价暴跌20%触及单日跌停","summary_short":"Zaggle利润下滑股价暴跌20%","reason":"印度金融科技高估值股业绩失速引发估值修正，同类SaaS公司财报风险需关注","impact":"印度SaaS板块、金融科技投资者","insight_label":"警示信号","trend_topic":"印度金融科技估值修正","score":8,"canonical_company":"Zaggle","canonical_key":"20%"}

示例5（微小事件必须低分1-3）：
标题: "X adds video overlays"
输出: {"url":"","event_types":"strategy","content_overview":"社交平台X为视频功能增加叠加层小工具","summary_short":"X增加视频叠加功能","reason":"社交平台常规功能迭代，为视频创作者提供新工具","impact":"视频创作者、品牌营销方","insight_label":"背景补充","trend_topic":"社交产品功能迭代","score":3,"canonical_company":"X","canonical_key":""}
"""

def analyze_events_doubao(items):
    """
    使用豆包大模型分析新闻事件（OpenAI 兼容 API）
    模型：doubao-pro-32k
    """
    import os
    api_key = os.environ.get('DOUBAO_API_KEY')
    if not api_key:
        print("  ⚠️  未设置 DOUBAO_API_KEY，降级跳过 AI 分析")
        return None

    url = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    model = os.environ.get('DOUBAO_MODEL', 'ep-20260409223830-dnt5b')
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json"
    }

    news = [{'title': it['title'], 'url': it['url'], 'source': it['source'], 'region': it.get('region','')} for it in items]
    prompt = AI_SYSTEM_PROMPT + "\n" + AI_EXAMPLES + "\n\n分析以下事件，返回JSON数组：\n" + json.dumps(news, ensure_ascii=False) + "\n\n返回JSON："

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
        "temperature": 0.1
    }

    for attempt in range(2):  # 最多重试1次（快速降级到程序生成）
        try:
            resp = _LLM_SESSION.post(url, headers=headers, json=payload, timeout=(10, 90))  # 90s 超时（给冷启动留足时间）
            if resp.status_code == 429:
                wait = (attempt + 1) * 10
                print("  ⚠️  豆包 API 配额耗尽（429），等待 " + str(wait) + "s 后重试...")
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                print(f"  ❌ 豆包 API HTTP {resp.status_code}: {resp.text[:300]}")
                return None
            data = resp.json()
            text = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            if not text:
                print("  ⚠️  豆包返回空内容: " + str(data))
                return None
            for m in ['```json', '```']:
                if m in text:
                    parts = text.split(m)
                    for p in parts[1:]:
                        text = p.strip()
                        if text.endswith('```'):
                            text = text[:-3].strip()
                        break
                    break
            result = json.loads(re.sub(r'^json\s*', '', text, flags=re.I))
            if isinstance(result, list):
                result = [r for r in result if _is_http_url(r.get('url')) and r.get('summary_short')]
            return result
        except requests.exceptions.Timeout:
            if attempt < 1:  # 重试一次（网络抖动场景）
                wait = (attempt + 1) * 3
                print(f"  ⚠️  豆包 API 超时，等待 {wait}s 后重试（第 {attempt+1}/2 次）...")
                time.sleep(wait)
                continue
            print(f"  ⚠️  豆包 API 超时，重试耗尽，跳过该批次")
            return None
        except json.JSONDecodeError as e:
            if attempt < 2:
                wait = (attempt + 1) * 5
                print(f"  ⚠️  豆包返回非JSON，尝试修正解析...")
                import re as re2
                match = re2.search(r'\[[\s\S]*\]', text if 'text' in dir() else '')
                if match:
                    try:
                        result = json.loads(match.group())
                        result = [r for r in result if isinstance(r, dict) and _is_http_url(r.get('url'))]
                        if result:
                            print(f"  ✅ 修正解析成功，提取 {len(result)} 条")
                            return result
                    except: pass
                print(f"  解析失败，等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            print(f"  ❌ 豆包 JSON 解析最终失败")
            return None
        except Exception as e:
            if attempt < 2:
                wait = (attempt + 1) * 5
                print(f"  ⚠️  豆包 API 调用失败（{type(e).__name__}），等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            print(f"  ❌ 豆包 API 最终失败: {type(e).__name__} {str(e)[:200]}")
            return None
    return None


def analyze_single_event_minimax(item):
    """单条事件分析（MiniMax批次失败时的兜底）"""
    try:
        result = analyze_events_minimax([item])
        return result
    except Exception:
        return None


def analyze_single_event_doubao(item):
    """单条事件分析（豆包批次失败时的兜底）"""
    # 单条分析也有30s timeout，不等待
    try:
        result = analyze_events_doubao([item])
        return result
    except Exception:
        return None


def _is_http_url(url):
    return isinstance(url, str) and url.startswith(('http://', 'https://'))


def _results_by_url(results):
    if not isinstance(results, list):
        return {}
    return {
        r.get('url'): r
        for r in results
        if isinstance(r, dict) and _is_http_url(r.get('url'))
    }


def _chat_api_candidates():
    """Return AI chat APIs in priority order: 方舟 V4 Flash primary, DeepSeek, Doubao fallback."""
    apis = []
    ark_key = os.environ.get('ARK_API_KEY', '')
    if ark_key and len(ark_key) >= 10:
        apis.append({
            'id': 'ark',
            'name': '方舟 V4 Flash',
            'url': 'https://ark.cn-beijing.volces.com/api/v3/chat/completions',
            'key': ark_key,
            'model': os.environ.get('ARK_MODEL') or 'ep-20260827101830-qgtm4',
        })
    ds_key = os.environ.get('DEEPSEEK_API_KEY', '')
    if ds_key and len(ds_key) >= 10:
        apis.append({
            'id': 'deepseek',
            'name': 'DeepSeek',
            'url': 'https://api.deepseek.com/v1/chat/completions',
            'key': ds_key,
            'model': os.environ.get('DEEPSEEK_MODEL', 'deepseek-chat'),
        })
    db_key = os.environ.get('DOUBAO_API_KEY', '')
    if db_key and len(db_key) >= 10:
        apis.append({
            'id': 'doubao',
            'name': '豆包',
            'url': 'https://ark.cn-beijing.volces.com/api/v3/chat/completions',
            'key': db_key,
            'model': os.environ.get('DOUBAO_MODEL', 'ep-20260409223830-dnt5b'),
        })
    return apis


def _post_chat(api, prompt, max_tokens=1024, temperature=0.1, timeout=(10, 20)):
    payload = {
        "model": api['model'],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if api.get('id') == 'ark':
        # 方舟 DeepSeek-V4-Flash 关深度思考必须用 thinking 参数（实测 reasoning:{effort:none} 会被静默忽略，思考 token 仍产生）
        payload['thinking'] = {"type": "disabled"}
    headers = {
        "Authorization": "Bearer " + api['key'],
        "Content-Type": "application/json"
    }
    return _LLM_SESSION.post(api['url'], headers=headers, json=payload, timeout=timeout)

# ============================================================
# P0 Agent：AI 标题改写 — 对程序层泛化事件用 AI 改写描述
# ============================================================

def rewrite_titles_for_display(events):
    """
    对程序层中仍是泛化描述的事件，优先调用 DeepSeek 改写成完整中文描述。
    轻量级 prompt（~50 tokens），25 条/批，timeout=20s。
    失败时静默降级，保持原描述。
    """
    generic_patterns = ['科技动态', '有新动态', '战略调整', '融资事件', '并购/收购', '财报披露', '金额待确认', '完成融资', '达成并购', '战略新动向', '战略动态']
    to_rewrite = []
    for e in events:
        reason = e.get('reason', '')
        if any(p in reason for p in generic_patterns):
            to_rewrite.append(e)

    if not to_rewrite:
        return

    apis = _chat_api_candidates()
    if not apis:
        return

    rewrote = 0

    for i in range(0, len(to_rewrite), 25):
        batch = to_rewrite[i:i+25]
        items = [{'url': e['url'], 'title': e['title'], 'region': e.get('region', ''), 'type': e.get('event_types', ['other'])[0]} for e in batch]

        prompt = f"""为以下科技新闻事件各写一句简短的中文描述（20字以内），格式为"[地区][公司名][具体动作]"。
要求：必须从标题提取公司名/产品名，描述具体做了什么。禁止出现"融资""并购""财报"等泛化词。
只返回JSON数组，每个元素包含"url"和"reason"字段。

{json.dumps(items, ensure_ascii=False)}

返回JSON："""

        for api in apis:
            try:
                resp = _post_chat(api, prompt, max_tokens=1024, temperature=0.1, timeout=(10, 20))
                if resp.status_code != 200:
                    print(f"  ⚠️ AI改写标题 {api['name']} HTTP {resp.status_code}，尝试下一个")
                    continue
                text = resp.json()['choices'][0]['message']['content']
                for m in ['```json', '```']:
                    if m in text:
                        parts = text.split(m)
                        for p in parts[1:]:
                            text = p.strip()
                            if text.endswith('```'):
                                text = text[:-3].strip()
                            break
                        break
                results = json.loads(re.sub(r'^json\s*', '', text, flags=re.I))
                if not isinstance(results, list):
                    print(f"  ⚠️ AI改写标题 {api['name']} 返回非列表JSON，尝试下一个")
                    continue
                for r in results:
                    url = r.get('url', '')
                    new_reason = r.get('reason', '')
                    if _is_http_url(url) and new_reason and len(new_reason) >= 8:
                        for e in batch:
                            if e['url'] == url:
                                e['reason'] = new_reason
                                e['analysis_source'] = api['id']
                                rewrote += 1
                                break
                break
            except Exception as exc:
                print(f"  ⚠️ AI改写标题 {api['name']} 异常: {exc}, 尝试下一个")
                continue

    if rewrote:
        print(f"  ✏️  AI改写标题：{rewrote}/{len(to_rewrite)} 条")


# ============================================================
# P0 Agent：每日AI趋势分析 — 基于今日信号事件生成专业判断
# ============================================================

def build_daily_ai_summary(today_events, summary_date=None):
    """
    基于今日信号事件，优先调用 DeepSeek 生成 2-4 句专业情报趋势分析。
    保存到 data/summary.json，供 generate_html.py 读取后覆盖模板摘要。
    失败降级到模板生成（无影响）。
    """
    # 只取信号事件（非 other），最多 15 条
    signal = [e for e in today_events if e.get('event_types', ['other'])[0] != 'other']
    if not signal:
        return None

    signal = signal[:15]
    today = summary_date or _cn_today()

    apis = _chat_api_candidates()
    if not apis:
        return None

    news_summary = []
    for e in signal:
        news_summary.append({
            'title': e.get('title', ''),
            'region': e.get('region', ''),
            'type': e.get('event_types', ['other'])[0],
            'reason': e.get('reason', '')[:80],
        })

    prompt = f"""你是全球互联网科技情报分析师，受众是ICT从业者。今天是{today}。

基于以下今日非中美地区科技事件，写一段2-4句的专业情报趋势分析。要求：
1. 总结今日最值得关注的趋势（资金流向哪个赛道、哪个地区最活跃、有什么结构性变化）
2. 如果跨区域/跨赛道有关联，指出交叉分析
3. 给出一个明确的判断结论
4. 语气专业、简洁、有洞察力，不罗列数据

事件列表：
{json.dumps(news_summary, ensure_ascii=False, indent=2)}

趋势分析（2-4句中文，不要超过120字）："""

    for api in apis:
        try:
            resp = _post_chat(api, prompt, max_tokens=512, temperature=0.3, timeout=(10, 20))
            if resp.status_code != 200:
                print(f"  ⚠️  趋势分析 {api['name']} 返回 {resp.status_code}，尝试下一个")
                continue
            data = resp.json()
            text = data['choices'][0]['message']['content'].strip().strip('"').strip()
            if len(text) < 20:
                print(f"  ⚠️  趋势分析 {api['name']} 结果过短: {text}")
                continue

            # 保存到 data/summary.json
            os.makedirs('data', exist_ok=True)
            summary_data = {}
            try:
                with open('data/summary.json', 'r', encoding='utf-8') as f:
                    summary_data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            summary_data[today] = text
            with open('data/summary.json', 'w', encoding='utf-8') as f:
                json.dump(summary_data, f, ensure_ascii=False, indent=2)

            print(f"  📊 AI趋势分析已生成（{api['name']}，{len(text)}字）: {text[:60]}...")
            return text
        except Exception as e:
            print(f"  ⚠️  趋势分析 {api['name']} 失败: {type(e).__name__}")
            continue
    return None


# ============================================================
# P0 Agent：情报价值评分 — AI过滤低价值 other 事件
# ============================================================

def ai_quality_judge(events):
    """
    对 other 类事件进行 AI 情报价值评分（1-5分）。
    低分事件（≤2）将被丢弃。失败时降级：保留全部。
    30 条/批，15s 超时。
    """
    other_events = [e for e in events if e['event_types'][0] == 'other' and not e.get('is_company')]
    if not other_events:
        return events

    apis = _chat_api_candidates()
    if not apis:
        return events

    kept_urls = set()
    kept_count = 0
    total_count = len(other_events)

    for i in range(0, len(other_events), 30):
        batch = other_events[i:i+30]
        items = [{'url': e['url'], 'title': e['title'], 'region': e.get('region', ''), 'source': e.get('source', '')} for e in batch]

        prompt = f"""评估以下科技新闻的情报价值（1-5分）。
5分 = 涉及重大融资/并购/独家合作，直接关系到商业机会或竞争格局
4分 = 重要战略动态，值得关注
3分 = 一般行业动态，有参考价值
2分 = 常规新闻，情报价值有限
1分 = 无情报价值

只返回JSON数组，每个元素包含"url"和"score"字段。

{json.dumps(items, ensure_ascii=False)}

返回JSON："""

        results = None
        for api in apis:
            try:
                resp = _post_chat(api, prompt, max_tokens=1024, temperature=0.1, timeout=(10, 15))
                if resp.status_code != 200:
                    continue
                text = resp.json()['choices'][0]['message']['content']
                for m in ['```json', '```']:
                    if m in text:
                        parts = text.split(m)
                        for p in parts[1:]:
                            text = p.strip()
                            if text.endswith('```'):
                                text = text[:-3].strip()
                            break
                        break
                parsed = json.loads(re.sub(r'^json\s*', '', text, flags=re.I))
                if isinstance(parsed, list):
                    results = parsed
                    break
            except Exception:
                continue

        if not isinstance(results, list):
            # 失败时保留本批次所有事件
            for e in batch:
                kept_urls.add(e.get('url', ''))
            continue

        try:
            scores = {}
            for r in results:
                if 'url' in r and 'score' in r:
                    scores[r['url']] = int(r['score'])
            for e in batch:
                score = scores.get(e.get('url', ''), 3)
                if score >= 3:
                    kept_urls.add(e.get('url', ''))
                    kept_count += 1
        except Exception:
            for e in batch:
                kept_urls.add(e.get('url', ''))

    # 过滤掉未保留的 other 事件
    filtered = [e for e in events if e['event_types'][0] != 'other' or e.get('is_company') or e.get('url', '') in kept_urls]
    dropped = total_count - kept_count
    if dropped > 0:
        print(f"  🎯 AI情报评分：保留 {kept_count}/{total_count} 条 other 事件（丢弃 {dropped} 条低价值）")
    return filtered


def _calc_score(item):
    """Score only scope-qualified facts by capital and causal impact."""
    apply_scope_contract(item)
    title = item.get('title', '')
    ev_type = item.get('event_types', ['other'])[0]

    # 金额解析
    amount = 0
    for pat, mult in [
        (r'\$([0-9,]+(?:\.\d+)?)\s*[Bb](?:illion)?', 1000),
        (r'€([0-9,]+(?:\.\d+)?)\s*[Mm](?:illion)?', 1),
        (r'\$([0-9,]+(?:\.\d+)?)\s*[Mm](?:illion)?', 1),
    ]:
        m = re.search(pat, title, re.I)
        if m:
            amount = float(m.group(1).replace(',', '')) * mult
            break

    # 融资金额分
    if amount >= 1000: amt_pts = 5
    elif amount >= 500: amt_pts = 4
    elif amount >= 100: amt_pts = 3
    elif amount >= 20: amt_pts = 2
    elif amount >= 5: amt_pts = 1
    else: amt_pts = 0

    # 事件类型分。金额只服务资本事件，不再决定政策/行业/公司动作价值。
    type_pts = {
        'ma': 2, 'earnings': 2, 'funding': 1, 'strategy': 1,
        'industry_report': 2, 'model_release': 2,
        'regional_policy': 2, 'other': 0,
    }.get(ev_type, 0)

    scope_layer_pts = {
        'regional_policy': 3,
        'industry_change': 2,
        'company_action': 1,
    }.get(item.get('scope_layer'), 0)
    scope_breadth_pts = 1 if item.get('scope_industries') else 0
    source_pts = 1 if (
        item.get('source_tier') in {'L1 官方/IR源', 'L4 垂直赛道精品源'}
        or item.get('source_role') in {'official_ir', 'developer_change', 'industry_vertical'}
    ) else 0

    # 区域权重
    region_mult = {'非洲': 1.3, '中东': 1.25, '亚太': 1.2, '拉美': 1.15, '欧洲': 1.0}.get(item.get('region', ''), 1.0)

    # 有公司名
    named_pts = 1 if item.get('companies') or item.get('company_name') else 0
    if item.get('region') == '中资' and _is_chinese_outbound_title(title):
        named_pts += 1

    raw = (
        amt_pts + type_pts + named_pts
        + scope_layer_pts + scope_breadth_pts + source_pts
    ) * region_mult
    return max(min(int(raw), 10), 1)

BD_TRIGGER_RULES = [
    ('预算窗口', [
        'raises', 'raised', 'funding', 'series ', 'seed round', 'investment',
        'valuation', 'valued at', 'revenue', 'profit', 'earnings', 'growth',
        'ipo', 'listing', 'goes public',
    ]),
    ('扩张窗口', [
        'expands', 'expansion', 'launches in', 'launches ', 'rolls out',
        'enters', 'entering', 'international', 'overseas', 'global',
        'new market', 'available in',
    ]),
    ('降本窗口', [
        'loss', 'losses', 'layoff', 'layoffs', 'cuts jobs', 'shutdown',
        'restructure', 'turnaround', 'cost', 'profitability',
    ]),
    ('合规窗口', [
        'regulator', 'regulatory', 'license', 'licence', 'compliance',
        'probe', 'investigation', 'ban', 'privacy', 'data protection',
    ]),
    ('整合窗口', [
        'acquires', 'acquired', 'acquisition', 'merger', 'merges',
        'stake in', 'buyout', 'integration', 'spins off',
    ]),
    ('生态窗口', [
        'partners with', 'partnership', 'strategic partnership',
        'joint venture', 'ecosystem', 'platform', 'developer', 'merchant',
        'channel', 'mou',
    ]),
    ('竞争窗口', [
        'rival', 'competition', 'competes', 'market share', 'overtakes',
        'beats', 'challenges', 'versus', 'vs ',
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

def infer_bd_context(item, score=None):
    """从事件标题/类型推断业务拓展触发器，先做确定性字段，后续可由 AI 精修。"""
    title = item.get('title', '')
    text = ' '.join([
        title,
        item.get('summary_short', ''),
        item.get('reason', ''),
        item.get('impact', ''),
    ]).lower()
    ev_type = (item.get('event_types') or ['other'])[0]
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
        for item_name in OPPORTUNITY_BY_TRIGGER.get(trigger, []):
            if item_name not in opportunities:
                opportunities.append(item_name)
    for item_name in OPPORTUNITY_BY_TYPE.get(ev_type, []):
        if item_name not in opportunities:
            opportunities.append(item_name)

    priority = classify_bd_priority(item)
    window = follow_up_window_for_priority(priority)

    if not triggers:
        triggers = ['持续观察']
    return {
        'bd_triggers': triggers[:3],
        'opportunity_direction': ' / '.join(opportunities[:4] or ['持续观察']),
        'follow_up_window': window,
        'bd_priority': priority,
    }

def attach_business_context(event, item, score):
    event['source_tier'] = item.get('source_tier', 'L3 区域生态源')
    event['source_role'] = item.get('source_role', 'regional_ecosystem')
    for key in (
        'source_type',
        'access_method',
        'source_id',
        'credibility_score',
        'noise_level',
        'origin_source_id',
        'observation_entity_id',
        'discovery_source',
        'publisher_source',
        'scope_enforced',
        'scope_status',
        'scope_reason',
        'scope_layer',
        'scope_industries',
        'scope_regions',
        'scope_match_basis',
        'source_url_original',
        'source_url_repaired',
        'source_url_repair_reason',
        'source_excerpt',
        'original_title',
        'evidence_refs',
        'origin_region',
        'impact_regions',
        'publisher_type',
        'authority_domains',
        'claim_roles',
        'access_level',
        'report_access_level',
        'methodology_visibility',
        'report_methodology_visible',
        'report_published_at',
        'model_card_url',
        'interpretation_basis',
        'claim_type',
        'content_type',
        'subject_type',
    ):
        if item.get(key) not in (None, '', []):
            event[key] = item.get(key)
    if item.get('signal_types'):
        event['source_signal_types'] = item.get('signal_types')
    if item.get('vertical'):
        event['vertical'] = item.get('vertical')
    event.update(infer_bd_context({**item, **event}, score))
    event['signal_taxonomy'] = infer_signal_taxonomy({**item, **event})
    return event


def attach_date_context(event, item):
    for key in (
        'published_at',
        'observed_at',
        'date_source',
        'date_confidence',
        'date_parse_warning',
        'scheduled_at',
    ):
        if item.get(key) not in (None, ''):
            event[key] = item[key]
    return apply_event_date_metadata(event, fallback_observed_at=_cn_now())


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


def build_event(item, analysis=None, analysis_source=None, analysis_status=None):
    """构建事件对象：程序评分始终生效，AI 只补充 reason/impact/insight_label"""
    # 程序评分（确定性，始终运行）
    score = _calc_score(item)
    level = 'A' if score >= 8 else 'B' if score >= 6 else 'C' if score >= 4 else 'D'
    # 有 AI 分析时（必须是 dict 类型，防止列表或其他异常类型）
    if analysis and isinstance(analysis, dict):
        event = {
            'title': item['title'],
            'url': item['url'],
            'source': item['source'],
            'region': item['region'],
            'event_types': _ai_event_types(analysis.get('event_types'), item.get('event_types') or ['other']),
            'level': level,
            'score': score,
            'summary_short': analysis.get('summary_short', item['title'][:25]),
            'content_overview': analysis.get('content_overview', ''),
            'reason': analysis.get('reason', '待分析'),
            'impact': analysis.get('impact', '未知'),
            'insight_label': analysis.get('insight_label', '背景补充'),
            'trend_topic': analysis.get('trend_topic', ''),
            'companies': analysis.get('companies', []) or [],
            'is_company': item.get('is_company', False),
            'company_name': item.get('company_name', ''),
            'canonical_company': _normalize_company_key(analysis.get('canonical_company', '')),
            'canonical_key': _normalize_canonical_key(analysis.get('canonical_key', '')),
            'article_date': item.get('article_date', ''),
            'date': item.get('article_date', _cn_today()),
            'source_detail': item.get('source_detail', ''),
            'publisher': item.get('publisher', ''),
            'image_url': item.get('image_url', ''),
        }
        attach_date_context(event, item)
        attach_business_context(event, item, score)
        return prepare_event_contract(annotate_event_quality(
            event,
            source=analysis_source or 'ai',
            status=analysis_status,
        ))
    # 无 AI 分析时的 fallback：reason 留空，模板不显示点评行。
    # 不编造类型化文案，也不显示"AI 分析暂不可用"这类吓人的提示。
    event = {
        'title': item['title'],
        'url': item['url'],
        'source': item['source'],
        'region': item['region'],
        'event_types': item['event_types'],
        'level': level,
        'score': score,
        'summary_short': item['title'][:25],
        'content_overview': '',
        'reason': '',
        'impact': '未知',
        'insight_label': '背景补充',
        'trend_topic': '',
        'companies': [],
        'is_company': item.get('is_company', False),
        'company_name': item.get('company_name', ''),
        'canonical_company': '',
        'canonical_key': '',
        'article_date': item.get('article_date', ''),
        'date': item.get('article_date', _cn_today()),
        'source_detail': item.get('source_detail', ''),
        'publisher': item.get('publisher', ''),
        'image_url': item.get('image_url', ''),
    }
    attach_date_context(event, item)
    attach_business_context(event, item, score)
    return prepare_event_contract(annotate_event_quality(
        event,
        source=analysis_source or 'program',
        status=analysis_status or 'fallback',
    ))

# ============================================================
# og:image 补抓 — 为没有 RSS 图片的事件获取文章配图
# ============================================================

def fill_event_images(events):
    """并发获取事件文章的 og:image，只处理没有 image_url 的事件"""
    batch = [e for e in events if not e.get('image_url') and e.get('url') and not e['url'].startswith('https://news.google.com')]
    if not batch:
        return
    print(f"  🖼️  补抓 og:image（{len(batch)} 条无图片）...")
    import asyncio
    async def fetch_one(session, ev):
        try:
            async with session.get(ev['url'], timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status != 200:
                    return
                html = await resp.text()
                for m in ["og:image", "twitter:image"]:
                    for pattern in [f'<meta property="{m}" content="', f'<meta name="{m}" content="']:
                        idx = html.find(pattern)
                        if idx >= 0:
                            start = idx + len(pattern)
                            end = html.find('"', start)
                            if end > start:
                                url = html[start:end]
                                if url.startswith('http'):
                                    ev['image_url'] = url
                                    return
        except Exception:
            pass
    async def run():
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            tasks = [fetch_one(session, ev) for ev in batch]
            await asyncio.gather(*tasks, return_exceptions=True)
    try:
        asyncio.run(run())
    except Exception:
        pass
    filled = sum(1 for e in batch if e.get('image_url'))
    print(f"    → 成功获取 {filled}/{len(batch)} 张")

# ============================================================
# 主函数
# ============================================================

def main():
    today = _cn_today()
    run_started = _cn_now()
    run_metrics = {
        'run_id': run_started.strftime('%Y%m%d-%H%M%S'),
        'date': today,
        'started_at': run_started.isoformat(),
        'environment': 'github_actions' if os.environ.get('GITHUB_ACTIONS') == 'true' else 'local',
    }
    source_funnel = {}
    ON_GHA = os.environ.get('GITHUB_ACTIONS') == 'true'
    if ON_GHA:
        print("  🤖 GHA 环境检测：DeepSeek 为主，失败后自动用豆包兜底")
    print(f"\n🌍 全球互联网动态情报站")
    print(f"   {_cn_now().strftime('%Y-%m-%d %H:%M')} | 目标：融资/并购/财报/战略\n")

    os.makedirs('data', exist_ok=True)
    _load_fact_ledger()
    try:
        with open('data/events.json', 'r', encoding='utf-8') as f:
            all_events = json.load(f)
        if isinstance(all_events, list): all_events = {}
    except: all_events = {}

    # 采集（并行优化）
    _clear_old_cache()  # 清理旧缓存，确保���次都真实抓取
    registry_rss_sources, registry_html_sources = load_registry_sources()
    effective_rss_sources = RSS_SOURCES + registry_rss_sources
    effective_html_sources = HTML_SOURCES + registry_html_sources
    if registry_rss_sources or registry_html_sources:
        print(f"🧭 Source Registry 启用：RSS {len(registry_rss_sources)} 个 | HTML {len(registry_html_sources)} 个")

    print("📡 采集 RSS 信源（并行）...")
    t0 = time.time()

    # Step 1: 并行抓取所有 RSS 源文本
    rss_urls = [cfg['url'] for cfg in effective_rss_sources]
    fetched = asyncio.run(fetch_all_parallel(rss_urls))

    # Step 2: 解析每个返回的文本
    raw = []
    cache_hits = sum(1 for _, (_, cached) in fetched.items() if cached)
    source_stats = {}  # human-readable source stats for logs
    source_metrics = {}
    for cfg in effective_rss_sources:
        body, cached = fetched.get(cfg['url'], (None, False))
        if not body:
            print(f"  ✗ [{cfg['name']}] 失败（{cfg['region']}）")
            source_stats[cfg['name']] = '✗'
            source_metrics[cfg['name']] = {
                'method': 'rss',
                'region': cfg.get('region', ''),
                'status': 'failed',
                'fetch_status': 'failed',
                'count': 0,
                'signal_count': 0,
                'cached': cached,
            }
            continue
        cfg_copy = cfg.copy()
        items = _parse_rss_text(cfg_copy, body)
        scope_stats = cfg_copy.get('_scope_stats') or {}
        mark = "📦" if cached else "🌐"
        sig = _qualified_signal_count(items)
        print(f"  {mark} [{cfg['name']}] {len(items)} 条（信号{sig} | {cfg['region']}）")
        source_stats[cfg['name']] = f'{len(items)} 条'
        source_metrics[cfg['name']] = {
            'method': 'rss',
            'region': cfg.get('region', ''),
            'status': 'ok',
            'fetch_status': 'success',
            'count': len(items),
            'signal_count': sig,
            'cached': cached,
            'scope_stats': scope_stats,
        }
        raw.extend(items)

    print(f"\n  ⏱  采集耗时 {time.time()-t0:.1f}s | 缓存命中 {cache_hits}/{len(rss_urls)}")
    print(f"  📊 信源统计（{len(raw)} 条）：{' | '.join(f'{k}: {v}' for k, v in source_stats.items() if v != '✗')}")
    run_metrics['rss'] = {
        'source_count': len(effective_rss_sources),
        'raw_count': len(raw),
        'cache_hits': cache_hits,
        'source_stats': source_metrics,
    }

    # HTML 备用采集（降级方案）
    if effective_html_sources:
        print("\n🌐 HTML 降级采集...")
        html_source_metrics = {}
        html_raw_count = 0
        for cfg in effective_html_sources:
            _apply_company_scope_contract(cfg)
            items = fetch_html(cfg)
            for item in items:
                apply_scope_contract(item)
            sig = _qualified_signal_count(items)
            if items:
                print(f"  ⚡ [{cfg['name']}] {len(items)} 条（信号{sig} | {cfg.get('region', '未知')}）")
            else:
                print(f"  – [{cfg['name']}] 无内容")
            html_source_metrics[cfg['name']] = {
                'method': 'html',
                'region': cfg.get('region', ''),
                'status': 'ok' if items else ('failed' if cfg.get('_last_fetch_status') == 'failed' else 'empty'),
                'fetch_status': cfg.get('_last_fetch_status') or 'unknown',
                'count': len(items),
                'signal_count': sig,
            }
            html_raw_count += len(items)
            raw.extend(items)
            time.sleep(REQUEST_DELAY)
        run_metrics['html'] = {
            'source_count': len(effective_html_sources),
            'raw_count': html_raw_count,
            'source_stats': html_source_metrics,
        }
    else:
        run_metrics['html'] = {
            'source_count': 0,
            'raw_count': 0,
            'source_stats': {},
        }

    # 27家公司监控（限当天/昨日，每公司最多3条）
    print("\n🏢 采集公司动态（限当天/昨日，每公司最多3条）...")
    t1 = time.time()
    company_raw = []
    company_source_metrics = {}
    for cfg in COMPANY_SOURCES:
        items = fetch_company_news(cfg)
        for item in items:
            apply_scope_contract(item)
        sig = _qualified_signal_count(items)
        if items:
            print(f"  🌐 [{cfg['name']}] {len(items)} 条（信号{sig}）")
        else:
            print(f"  – [{cfg['name']}] 无今日动态")
        company_source_metrics[cfg['name']] = {
            'method': 'company',
            'region': cfg.get('region', ''),
            'status': 'ok' if items else ('failed' if cfg.get('_last_fetch_status') == 'failed' else 'empty'),
            'fetch_status': cfg.get('_last_fetch_status') or 'unknown',
            'count': len(items),
            'signal_count': sig,
        }
        company_raw.extend(items)
        time.sleep(0.5)  # 避免请求过快

    company_unique = company_raw  # fetch_company_news 内部已去重
    print(f"  ⏱  公司采集耗时 {time.time()-t1:.1f}s | {len(company_unique)} 条")
    run_metrics['company'] = {
        'source_count': len(COMPANY_SOURCES),
        'raw_count': len(company_raw),
        'unique_count': len(company_unique),
        'source_stats': company_source_metrics,
    }

    try:
        from job_observation import (
            collect_job_observations,
            write_job_observation_metrics,
            write_job_snapshots,
            write_signal_candidates,
        )
    except ImportError:
        from scripts.job_observation import (
            collect_job_observations,
            write_job_observation_metrics,
            write_job_snapshots,
            write_signal_candidates,
        )
    jobs_metrics, job_snapshots, signal_candidates = collect_job_observations(observed_at=run_started.isoformat())
    promoted_job_events = jobs_metrics.pop('promoted_events', [])
    jobs_metrics['promoted_count'] = len(promoted_job_events)
    write_job_snapshots(job_snapshots)
    write_job_observation_metrics(jobs_metrics)
    write_signal_candidates(signal_candidates)
    run_metrics['jobs'] = jobs_metrics
    print(
        f"  🧑‍💻 Jobs 快照：{jobs_metrics['source_count']} 个对象 | "
        f"{jobs_metrics['raw_count']} 个职位 | "
        f"{len(jobs_metrics['candidate_signals'])} 个结构变化候选"
    )

    # 合并：公司新闻 + 通用新闻，按事件级指纹去重
    all_raw = company_unique + raw
    _merge_source_funnel(source_funnel, _source_funnel_stage(all_raw, 'raw'))
    unique = []
    for it in all_raw:
        if any(_is_same_event(it, existing) for existing in unique):
            continue
        unique.append(it)
    same_run_duplicate_skipped = len(all_raw) - len(unique)
    _merge_source_funnel(source_funnel, _source_funnel_stage(unique, 'unique'))

    # 统计（event_type 会随 AI 输出出现 industry_report/model_release 等扩展类型，用 get 容错）
    types = {}
    for it in unique:
        t = (it.get('event_types') or ['other'])[0]
        types[t] = types.get(t, 0) + 1
    regions = {}
    for it in unique: regions[it['region']] = regions.get(it['region'],0) + 1
    company_count = sum(1 for it in unique if it.get('is_company'))

    print(f"\n📊 采集：{len(unique)} 条（融资{types.get('funding',0)} | 并购{types.get('ma',0)} | 财报{types.get('earnings',0)} | 战略{types.get('strategy',0)} | 其他{types.get('other',0)}）")
    print(f"   区域：{regions} | 公司动态：{company_count} 条")
    run_metrics['collection'] = {
        'raw_count': len(all_raw),
        'unique_count': len(unique),
        'same_run_duplicate_skipped': same_run_duplicate_skipped,
        'company_count': company_count,
        'type_counts': types.copy(),
        'region_counts': regions.copy(),
    }

    # 范围准入先于价值评分。候选仅留在运行指标中，不送 AI、不入事件库。
    scope_qualified, scope_candidates, scope_filtered = [], [], []
    for item in unique:
        apply_scope_contract(item)
        if item.get('scope_status') == 'qualified':
            if (item.get('event_types') or ['other'])[0] == 'other':
                item['event_types'] = ['strategy']
            scope_qualified.append(item)
        elif item.get('scope_status') == 'candidate':
            scope_candidates.append(item)
        else:
            scope_filtered.append(item)
    _merge_source_funnel(source_funnel, _source_funnel_stage(scope_qualified, 'scope_qualified'))
    _merge_source_funnel(source_funnel, _source_funnel_stage(scope_candidates, 'scope_candidate'))
    _merge_source_funnel(source_funnel, _source_funnel_stage(scope_filtered, 'scope_filtered'))

    # 传统银行主体过滤：Fintech 源编辑视野含整个金融业，排除商业银行事件
    bank_filtered = []
    kept_after_bank = []
    for it in scope_qualified:
        if _is_traditional_bank_item(it):
            bank_filtered.append(it)
        else:
            kept_after_bank.append(it)
    scope_qualified = kept_after_bank
    _merge_source_funnel(source_funnel, _source_funnel_stage(bank_filtered, 'bank_filtered'))
    run_metrics['bank_filtered_count'] = len(bank_filtered)

    # 智能过滤（公司新闻单独处理，不做 smart_filter）
    filtered = smart_filter(scope_qualified)
    _merge_source_funnel(source_funnel, _source_funnel_stage(filtered, 'smart_kept'))
    smart_filtered_count = len(filtered)
    types2 = {}
    for it in filtered:
        t = (it.get('event_types') or ['other'])[0]
        types2[t] = types2.get(t, 0) + 1
    print(f"   过滤后：{len(filtered)} 条（融资{types2.get('funding',0)} | 并购{types2.get('ma',0)} | 财报{types2.get('earnings',0)} | 战略{types2.get('strategy',0)} | 其他{types2.get('other',0)}）")
    run_metrics['filtering'] = {
        'smart_filtered_count': smart_filtered_count,
        'smart_filter_dropped': len(scope_qualified) - smart_filtered_count,
        'scope_qualified_count': len(scope_qualified),
        'scope_candidate_count': len(scope_candidates),
        'scope_filtered_count': len(scope_filtered),
        'scope_reason_counts': {
            reason: sum(1 for item in scope_candidates + scope_filtered if item.get('scope_reason') == reason)
            for reason in sorted({item.get('scope_reason') for item in scope_candidates + scope_filtered if item.get('scope_reason')})
        },
        'ai_filtered_count': smart_filtered_count,
        'ai_filter_dropped': 0,
        'type_counts_after_smart_filter': types2.copy(),
    }

    # AI 情报价值评分：对 other 类事件豆包评分，过滤低价值
    if any((it.get('event_types') or ['other'])[0] == 'other' and not it.get('is_company') for it in filtered):
        before_ai_filter = len(filtered)
        filtered = ai_quality_judge(filtered)
        _merge_source_funnel(source_funnel, _source_funnel_stage(filtered, 'ai_quality_kept'))
        print(f"   AI评分过滤后：{len(filtered)} 条")
        run_metrics['filtering']['ai_filtered_count'] = len(filtered)
        run_metrics['filtering']['ai_filter_dropped'] = before_ai_filter - len(filtered)

    # 评分前置：每个事件程序评分，分层决定是否送 AI
    print(f"\n  📊 评分前置，分层处理...")
    for it in filtered:
        it['_prescore'] = _calc_score(it)

    # 分层：所有合格事件一律先送 AI 分析（2026-08-13 用户决策），
    # 丢弃仍按分数（<4 且非公司视为无价值边缘事件，不送 AI 省成本）。
    ai_tier, prog_tier = [], []
    drop_count = 0
    for it in filtered:
        score = it['_prescore']
        if score < 4 and not it.get('is_company'):
            drop_count += 1
        else:
            ai_tier.append(it)
    _merge_source_funnel(source_funnel, _source_funnel_stage(ai_tier, 'score_ai_tier'))
    kept_score_ids = {id(it) for it in ai_tier}
    dropped_items = [it for it in filtered if id(it) not in kept_score_ids]
    _merge_source_funnel(source_funnel, _source_funnel_stage(dropped_items, 'score_dropped'))

    print(f"    AI深度分析：{len(ai_tier)} 条 | 丢弃：{drop_count} 条")
    run_metrics['scoring'] = {
        'ai_tier_count': len(ai_tier),
        'program_tier_count': 0,
        'dropped_count': drop_count,
    }

    # AI深度分析（所有合格事件先送 AI，失败才程序兜底）
    today_events = []
    if ai_tier:
        fill_event_images(ai_tier)
        use_ark = configure_ark()
        ark_dead = not use_ark
        use_deepseek = configure_deepseek()
        deepseek_dead = not use_deepseek
        use_doubao = False

        for i in range(0, len(ai_tier), 8):
            batch = ai_tier[i:i+8]
            results = None
            result_source = None
            batch_idx = (i // 8) + 1
            total_batches = (len(ai_tier) + 7) // 8

            # 方舟 V4 Flash 主力（降本 6 倍）；连续失败后降级 DeepSeek → 豆包
            if not ark_dead:
                results = analyze_events_ark(batch)
                if results is None:
                    print(f"  批次 {batch_idx}/{total_batches} 方舟失败→降级...")
                    ark_dead = True
                else:
                    result_source = 'ark'
                    print(f"  批次 {batch_idx}/{total_batches} 方舟 ✅")

            # DeepSeek 二级；连续失败后本轮后续批次直接走豆包兜底
            if results is None and not deepseek_dead:
                results = analyze_events_deepseek(batch)
                if results is None:
                    print(f"  批次 {batch_idx}/{total_batches} DeepSeek 失败→降级...")
                    deepseek_dead = True
                else:
                    result_source = 'deepseek'
                    print(f"  批次 {batch_idx}/{total_batches} DeepSeek ✅")

            # 豆包兜底
            if results is None:
                if not use_doubao:
                    use_doubao = configure_doubao()
                if use_doubao:
                    results = analyze_events_doubao(batch)
                    if results:
                        result_source = 'doubao'
                        print(f"  批次 {batch_idx}/{total_batches} 豆包 ✅")
                    else:
                        # 批量失败后逐条兜底（应对间歇性超时）
                        print(f"  批次 {batch_idx}/{total_batches} 豆包批量失败→逐条兜底...")
                        results = []
                        for item in batch:
                            single = analyze_single_event_doubao(item)
                            if single:
                                results.extend(single)
                        if results:
                            result_source = 'doubao'
                            print(f"  逐条兜底成功：{len(results)}/{len(batch)} 条 ✅")
                        else:
                            print(f"  逐条兜底全部失败，程序生成")

            # 构建事件（有AI结果则合并，否则程序生成）
            if results:
                result_map = _results_by_url(results)
                for item in batch:
                    r = result_map.get(item['url'])
                    if r:
                        ev = build_event(
                            item,
                            r,
                            analysis_source=result_source or 'ai',
                        )
                        _apply_fact_score_rules(ev)
                        today_events.append(ev)
                    else:
                        today_events.append(
                            build_event(
                                item,
                                analysis_source='program',
                                analysis_status='failed',
                            )
                        )
            else:
                for item in batch:
                    today_events.append(
                        build_event(
                            item,
                            analysis_source='program',
                            analysis_status='failed',
                        )
                    )
            time.sleep(0.5)

    today_events.extend(prepare_event_contract(event) for event in promoted_job_events)

    # AI标题改写：对程序层中仍为泛化描述的事件用豆包改写
    rewrite_titles_for_display(today_events)
    _merge_source_funnel(source_funnel, _source_funnel_stage(today_events, 'analysis_events'))
    for event in today_events:
        annotate_event_quality(event)
    # 重新冻结展示资格：annotate 可能把 needs_repair 从 False 翻 True，
    # 不重冻结会让 needs_repair=True 的事件仍以 view_status='main' 混入日报
    for event in today_events:
        prepare_event_contract(event)

    q = summarize_quality(today_events)
    if q['total']:
        print(
            f"  🧪 分析质量：需修复 {q['needs_repair']}/{q['total']} 条"
            f"（高分需修复 {q['high_score_needs_repair']} 条，"
            f"兜底/失败 {q['fallback_or_failed']} 条）"
        )

    # 按文章实际发布日期分组（而非脚本运行时间）
    # 同一批次抓到的文章可能有不同的发布日期
    # 全局去重：按事件级指纹 + URL 双重控制，避免多次运行重复追加
    existing_events = [e for events in all_events.values() for e in events]
    pubdate_ok, pubdate_fallback = 0, 0
    added_events = []
    for event in today_events:
        apply_event_date_metadata(event, fallback_observed_at=run_started)
        dup = next((e for e in existing_events
                    if (event.get('url') and e.get('url') == event['url']) or _is_same_event(event, e)), None)
        if dup is not None:
            # 跨批次去重：重复事件按信息完整度合并，新报道更具体时升级已入库事件
            if _is_more_complete(event, dup):
                _upgrade_event(dup, event)
            continue
        existing_events.append(event)
        date_key = event.get('date') or today
        if event.get('published_at'):
            pubdate_ok += 1
            all_events.setdefault(date_key, []).append(event)
        else:
            pubdate_fallback += 1
            all_events.setdefault(date_key, []).append(event)
        added_events.append(event)
    # 确保今日槽位存在（即使 0 条也记录空日期，保持历史完整性）
    all_events.setdefault(today, [])

    # 输出统计
    company_added = sum(1 for e in added_events if e.get('is_company'))
    _merge_source_funnel(source_funnel, _source_funnel_stage(added_events, 'added'))
    added_event_dates = {}
    added_source_tiers = {}
    for event in added_events:
        event_date = (event.get('date') or today)[:10]
        source_tier = event.get('source_tier') or '未标注'
        added_event_dates[event_date] = added_event_dates.get(event_date, 0) + 1
        added_source_tiers[source_tier] = added_source_tiers.get(source_tier, 0) + 1
    print(f"  📅 pubDate 解析：{pubdate_ok} 条有日期 | {pubdate_fallback} 条无日期（归入今日）")
    print(f"  🏢 新增公司动态：{company_added} 条 | 新增通用热点：{len(added_events) - company_added} 条")
    print(f"  🚫 历史重复跳过：{len(today_events) - len(added_events)} 条")
    run_metrics['analysis'] = {
        'event_count': len(today_events),
        'quality': q,
    }
    run_metrics['storage'] = {
        'added_count': len(added_events),
        'duplicate_skipped': len(today_events) - len(added_events),
        'company_added': company_added,
        'generic_added': len(added_events) - company_added,
        'pubdate_ok': pubdate_ok,
        'pubdate_fallback': pubdate_fallback,
        'added_event_dates': added_event_dates,
        'added_source_tiers': added_source_tiers,
    }
    run_metrics['source_funnel'] = source_funnel

    # 事件库不再按时间裁剪；首页、周报和月报分别使用展示窗口。
    all_events = apply_event_storage_policy(all_events)
    all_events, removed_dups, removed_reasons = dedupe_events_by_day(all_events)
    if removed_dups:
        print(f"  🧹 历史去重：清理 {removed_dups} 条同日重复事件")

    with open('data/events.json', 'w', encoding='utf-8') as f:
        json.dump(all_events, f, ensure_ascii=False, indent=2)
    _save_fact_ledger()
    print(f"  📒 事实评分账本：{len(_fact_ledger)} 个指纹")
    run_metrics['finished_at'] = _cn_now().isoformat()
    run_metrics['history'] = {
        'total_events': sum(len(v) for v in all_events.values()),
        'company_total': sum(1 for v in all_events.values() for e in v if e.get('is_company')),
        'day_count': len(all_events),
        'removed_same_day_duplicates': removed_dups,
        'removed_reasons': removed_reasons,
    }
    metrics_path = write_run_metrics(run_metrics)
    print(f"  🧾 Run metrics：{metrics_path}")
    try:
        from entity_observation_ledger import build_entity_observation_ledger, write_entity_observation_ledger
    except ImportError:
        from scripts.entity_observation_ledger import build_entity_observation_ledger, write_entity_observation_ledger
    ledger = build_entity_observation_ledger(as_of=today)
    ledger_path = write_entity_observation_ledger(ledger)
    print(f"  🧭 观察点账本：{ledger_path} | {ledger['status_counts']}")

    # 输出每个日期的分桶统计
    for date_key in sorted(all_events.keys(), reverse=True):
        events = all_events[date_key]
        regions = {}
        company_n = 0
        for e in events:
            regions[e['region']] = regions.get(e['region'], 0) + 1
            if e.get('is_company'): company_n += 1
        print(f"  ✅ {date_key}：{len(events)} 条（公司{company_n}）| 区域：{regions}")
    total = sum(len(v) for v in all_events.values())
    company_total = sum(1 for v in all_events.values() for e in v if e.get('is_company'))
    print(f"\n  共 {total} 条历史事件（公司 {company_total} 条），跨 {len(all_events)} 天）")

    # P0 Agent：每日AI趋势分析（生成2-4句专业判断）
    summary_groups = {}
    for event in added_events:
        summary_date = (event.get('date') or today)[:10]
        if event in all_events.get(summary_date, []):
            summary_groups.setdefault(summary_date, []).append(event)
    if summary_groups:
        for summary_date, summary_events in sorted(summary_groups.items()):
            build_daily_ai_summary(summary_events, summary_date)
    else:
        print("  📊 今日无新增入库事件，跳过 AI 趋势分析")

if __name__ == '__main__':
    main()
