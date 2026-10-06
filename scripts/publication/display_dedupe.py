"""展示层判重：标题主体归一、相似标题聚合、展示事件去重。"""

import re
from datetime import datetime, timedelta

try:
    from content.dedupe import _fingerprint_match, _is_same_event
    from publication.bd import _extract_subject
except ImportError:
    from scripts.content.dedupe import _fingerprint_match, _is_same_event
    from scripts.publication.bd import _extract_subject

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

        # 采集层规则兜底：复用 _is_same_event（与入库判定一致），治展示层正则
        # 主体提取错位导致的无指纹同事件漏并（"US space data center startup
        # Starcloud" 被 _display_subject_key 错提为 "us space data center"）。
        # 仅非 strategy 且在 3 天窗口内启用，与下方语义窗口一致，避免误删连续战略动作。
        event_type = (event.get('event_types') or ['other'])[0]
        if event_type != 'strategy':
            match = next(
                (ev for ev in kept if _is_same_event(event, ev)),
                None,
            )
            if match is not None:
                if event.get('url'):
                    match.setdefault('merged_from', [])
                    if event['url'] not in match['merged_from']:
                        match['merged_from'].append(event['url'])
                continue

        date_key = (event.get('date') or '')[:10]
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
