"""共享常量表：信源配置与分类词表。

纯数据，无逻辑、无外部依赖。从 fetch_news.py 抽出，供 sources/content 各层
共同引用——这些表被多个域使用（信源配置被采集与判型共用，公司别名/词表被
判型、去重、过滤共用），集中一处可避免「搬迁时到处复制数据表」。

fetch_news.py 仍 re-export 这些名字，保持既有 import 不变。"""

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122.0 Safari/537.36',
    'Accept': 'application/rss+xml,application/atom+xml,application/xml;q=0.9,text/html;q=0.8,*/*;q=0.7',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.google.com/',
}

REQUEST_DELAY = 1.2  # 避免被封（仅用于重试，非采集）

REQUEST_TIMEOUT = 8   # 单次请求超时（秒），降级提速

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
