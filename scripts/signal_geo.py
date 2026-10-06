"""Signal geo tagging: attach country tags to events on read.

This implements the "Signal = existing event model + country dimension"
decision. It is a read-side annotation layer: it never writes back to
events.json, so the frozen stored facts stay untouched. Statistics scripts
use these helpers to report country x sector x source coverage.
"""

import json
import re

# 仓库路径锚定 __file__，不依赖调用进程 CWD（见 docs/ARCHITECTURE.md「路径锚定仓库根」）。
try:
    from repo_paths import data_path
except ImportError:
    from scripts.repo_paths import data_path


def load_entity_pool(path=None):
    with open(path or data_path('entity_pool.json'), encoding='utf-8') as f:
        return json.load(f)


# Country keyword maps. Keep them explicit and small: they are hints for a
# first statistical pass, not a perfect geocoder. Prefer company primary_markets
# over title keywords whenever the event has a known company.
COUNTRY_KEYWORDS = {
    '越南': ['vietnam', 'vietnamese', 'hanoi', 'ho chi minh', 'saigon', '越南'],
    '印尼': ['indonesia', 'indonesian', 'jakarta', 'jabodetabek', 'bali', '印尼', '雅加达'],
    '日本': ['japan', 'japanese', 'tokyo', 'osaka', '日本', '东京', '大阪'],
    '韩国': ['korea', 'korean', 'seoul', 'south korea', '韩国', '首尔'],
    '沙特': ['saudi', 'riyadh', 'jeddah', '沙特', '利雅得'],
    '阿联酋': ['uae', 'dubai', 'abu dhabi', 'emirates', '阿联酋', '迪拜', '阿布扎比'],
    '巴西': ['brazil', 'brazilian', 'sao paulo', 'são paulo', 'brasil', '巴西', '圣保罗'],
    '墨西哥': ['mexico', 'mexican', 'mexico city', 'cdmx', '墨西哥'],
    '新加坡': ['singapore', 'singapura', '新加坡'],
    '马来西亚': ['malaysia', 'malaysian', 'kuala lumpur', '马来西亚', '吉隆坡'],
    '泰国': ['thailand', 'thai', 'bangkok', '泰国', '曼谷'],
    '菲律宾': ['philippines', 'filipino', 'manila', '菲律宾', '马尼拉'],
    '印度': ['india', 'indian', 'mumbai', 'delhi', 'bengaluru', 'bangalore', '印度'],
    '香港': ['hong kong', 'hktv', '香港'],
    '台湾': ['taiwan', 'taipei', '台湾', '台北'],
    '阿根廷': ['argentina', 'argentinian', 'buenos aires', '阿根廷', '布宜诺斯艾利斯'],
    '哥伦比亚': ['colombia', 'colombian', 'bogota', 'bogotá', '哥伦比亚'],
    '乌拉圭': ['uruguay', 'montevideo', '乌拉圭'],
    '智利': ['chile', 'santiago', '智利', '圣地亚哥'],
    '尼日利亚': ['nigeria', 'nigerian', 'lagos', 'abuja', '尼日利亚', '拉各斯'],
    '肯尼亚': ['kenya', 'kenyan', 'nairobi', '肯尼亚', '内罗毕'],
    '埃及': ['egypt', 'egyptian', 'cairo', '埃及', '开罗'],
    '荷兰': ['netherlands', 'dutch', 'amsterdam', '荷兰', '阿姆斯特丹'],
    '美国': ['united states', ' usa', ' us ', 'u.s.', 'america', 'american', 'silicon valley', '美国'],
    '加拿大': ['canada', 'canadian', 'toronto', '加拿大'],
    '英国': ['united kingdom', ' uk', ' u.k.', 'britain', 'british', 'london', '英国', '伦敦'],
    '德国': ['germany', 'german', 'berlin', 'munich', '德国', '柏林'],
    '法国': ['france', 'french', 'paris', '法国', '巴黎'],
    '巴基斯坦': ['pakistan', 'pakistani', 'karachi', '巴基斯坦'],
    '土耳其': ['turkey', 'turkish', 'istanbul', 'istanbûl', '土耳其'],
    '澳大利亚': ['australia', 'australian', 'sydney', 'melbourne', '澳大利亚'],
    '加纳': ['ghana', 'accra', '加纳'],
    '坦桑尼亚': ['tanzania', 'tanzanian', 'dar es salaam', '坦桑尼亚'],
    '埃塞俄比亚': ['ethiopia', 'ethiopian', 'addis ababa', '埃塞俄比亚'],
    '摩洛哥': ['morocco', 'moroccan', 'casablanca', '摩洛哥'],
    '南非': ['south africa', 'south african', 'johannesburg', 'cape town', '南非'],
}

# Compile once.
_COUNTRY_PATTERNS = {
    country: [re.compile(r'\b' + re.escape(kw.lower()) + r'\b') for kw in kws]
    for country, kws in COUNTRY_KEYWORDS.items()
}

# Explicit market->country aliases for company primary_markets values.
MARKET_TO_COUNTRY = {
    '越南': '越南', '印尼': '印尼', '日本': '日本', '韩国': '韩国',
    '沙特': '沙特', '阿联酋': '阿联酋', '巴西': '巴西', '墨西哥': '墨西哥',
    '新加坡': '新加坡', '马来西亚': '马来西亚', '泰国': '泰国',
    '菲律宾': '菲律宾', '香港': '香港', '台湾': '台湾',
    '阿根廷': '阿根廷', '哥伦比亚': '哥伦比亚', '乌拉圭': '乌拉圭',
    '智利': '智利', '尼日利亚': '尼日利亚', '肯尼亚': '肯尼亚',
    '埃及': '埃及', '荷兰': '荷兰', '巴基斯坦': '巴基斯坦',
    '美国': '美国', '加拿大': '加拿大', '欧洲': None, '全球': None,
    '东南亚': None, '拉美各国': None, '非洲多国': None, '非洲': None,
    '拉美': None,
}

# Market labels that are not single countries: skip them as primary_country
# but keep as broad market context.
REGION_MARKETS = {'全球', '欧洲', '东南亚', '拉美各国', '拉美', '非洲多国', '非洲'}


def entity_country_map(pool=None):
    """Return {entity_id: {'hq': str, 'markets': [str,...]}}."""
    pool = pool or load_entity_pool()
    out = {}
    for e in pool.get('entities', []):
        out[e['id']] = {
            'hq': e.get('hq_country', ''),
            'markets': e.get('primary_markets') or [],
        }
    return out


def company_to_countries(entity_map, company_name):
    """Resolve a company_name to country set via entity_pool primary_markets."""
    if not company_name:
        return set()
    c = company_name.strip()
    # direct id match
    ent = entity_map.get(c) or entity_map.get(c.lower())
    if not ent:
        # try case-insensitive + substring
        for eid, info in entity_map.items():
            if eid.lower() == c.lower() or eid in c or c in eid:
                ent = info
                break
    if not ent:
        return set()
    countries = set()
    for m in ent.get('markets', []):
        if m in MARKET_TO_COUNTRY and MARKET_TO_COUNTRY[m]:
            countries.add(MARKET_TO_COUNTRY[m])
    if ent.get('hq') and ent['hq']:
        countries.add(ent['hq'])
    return countries


def _title_countries(title):
    if not title:
        return set()
    t = title.lower()
    found = set()
    for country, patterns in _COUNTRY_PATTERNS.items():
        if any(p.search(t) for p in patterns):
            found.add(country)
    return found


def tag_event_country(event, entity_map=None, pool=None):
    """Return {'primary_country': str or '', 'countries': [str,...],
               'tag_method': 'company_market'|'title_keyword'|''}

    Order: company primary_markets first, then title keywords. company wins
    because it is authoritative; title is a hint.
    """
    entity_map = entity_map or entity_country_map(pool)
    # company_name or companies list
    companies = []
    if event.get('company_name'):
        companies.append(event['company_name'])
    companies.extend(event.get('companies') or [])
    comp_countries = set()
    for comp in companies:
        comp_countries |= company_to_countries(entity_map, comp)
    if comp_countries:
        # prefer hq if we can resolve it for the primary company
        prim = ''
        if event.get('company_name'):
            for eid, info in entity_map.items():
                if eid.lower() == event['company_name'].lower() or event['company_name'].lower() in eid.lower():
                    if info.get('hq'):
                        prim = info['hq']
                        break
        if not prim:
            prim = sorted(comp_countries)[0]
        return {
            'primary_country': prim,
            'countries': sorted(comp_countries),
            'tag_method': 'company_market',
        }
    title_countries = _title_countries(event.get('title'))
    if title_countries:
        return {
            'primary_country': sorted(title_countries)[0],
            'countries': sorted(title_countries),
            'tag_method': 'title_keyword',
        }
    return {'primary_country': '', 'countries': [], 'tag_method': ''}
