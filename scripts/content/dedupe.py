"""事实评分账本与事件判重。

同一件事（指纹）只打一次分，跨批次/跨 run 复用，治「同一事实多版本分数漂移」。
_is_same_event 是入库判重的主判据，采集侧（fetch_company_news）也会用到。"""

import json
import re
from pathlib import Path

try:
    from constants import TITLE_STOPWORDS
    from repo_paths import data_path
    from internet_relevance import assess_internet_relevance
    from content.classify import (
        _dates_adjacent, _entity_key_info, _event_similarity, _financial_direction_consistent,
        _fingerprint_match, _GENERIC_ALIAS_TOKENS, _has_financial_anchor, _has_funding_signal,
        _has_ma_signal, _normalize_canonical_key, _normalize_company_key, _primary_event_type,
        _SINGULAR_EVENT_TYPES,
    )
    from content.util import _normalize_text, _parse_iso_date, _title_tokens
except ImportError:
    from scripts.constants import TITLE_STOPWORDS
    from scripts.repo_paths import data_path
    from scripts.internet_relevance import assess_internet_relevance
    from scripts.content.classify import (
        _dates_adjacent, _entity_key_info, _event_similarity, _financial_direction_consistent,
        _fingerprint_match, _GENERIC_ALIAS_TOKENS, _has_financial_anchor, _has_funding_signal,
        _has_ma_signal, _normalize_canonical_key, _normalize_company_key, _primary_event_type,
        _SINGULAR_EVENT_TYPES,
    )
    from scripts.content.util import _normalize_text, _parse_iso_date, _title_tokens


# 事实评分账本：进程内可变状态，由 _load_fact_ledger() 就地重建（会重新绑定本名）。
_fact_ledger = {}


_FACT_LEDGER_PATH = Path(data_path('fact_score_ledger.json'))

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

def _first_title_entity(title):
    """标题开头的主要实体词（如 "Apple overhauls ..." → "apple"）。

    实体键提取失败时的最后一级弱锚：只认标题最开头的实体，避免中途
    任意单词造成误并。中文标题返回空（不做分词，交给其他路径）。"""
    match = re.match(r"[^A-Za-z0-9一-鿿]*([A-Za-z0-9][A-Za-z0-9\.\-'&’]{2,})", str(title or ''))
    if not match:
        return ''
    word = match.group(1).lower().strip('’\'-&.')
    if word in _GENERIC_ALIAS_TOKENS or word in TITLE_STOPWORDS:
        return ''
    return word

def _title_numbers(title):
    """标题中的数字集合（金额/百分比/人数），供同事件判定与数字守卫共用。"""
    return {round(float(m.group()), 2) for m in re.finditer(r'\d+(?:\.\d+)?', str(title or ''))}

def _numeric_conflict_titles(title_a, title_b):
    """两条标题的数字集合均非空、无交集且量级差异显著（>3 倍）→ 判不同事件。

    治「同公司两笔不同金额融资/两个不同百分比」的误并；$7B 与 $7.5bn
    这类同事件近似表述（比值 1.07）不视为冲突。"""
    na, nb = _title_numbers(title_a), _title_numbers(title_b)
    if not na or not nb or (na & nb):
        return False
    max_a, max_b = max(na), max(nb)
    if not max_a or not max_b:
        return False
    return max(max_a / max_b, max_b / max_a) > 3.0

def _is_same_event(candidate, existing):
    if candidate.get('url') and candidate.get('url') == existing.get('url'):
        return True

    if _fingerprint_match(candidate, existing):
        return True

    # 强指纹跨周报道窗口：实体键来自 company/alias（权威来源）、单实例类型、
    # 双方锚点词齐全时，报道窗口 3→7 天（Kakao Mobility 上市案：传闻发酵
    # 跨 4 天，8-17 批准 ADR / 8-21 提交申请是同一件事的连续进展）。
    key_a, src_a = _entity_key_info(candidate)
    key_b, src_b = _entity_key_info(existing)
    type_a = _primary_event_type(candidate)
    type_b = _primary_event_type(existing)
    if (type_a == type_b and type_a in _SINGULAR_EVENT_TYPES
            and key_a and key_a == key_b and 'title' not in (src_a, src_b)
            and _dates_adjacent(candidate, existing, window_days=7)):
        # 锚点判定：财报/融资/并购 常规词之外，上市类词（ipo/listing/filing）是
        # 单实例事件的最强锚——Kakao Mobility 上市案 AI 误判为 earnings，标题却
        # 无财报词，只有 ADR Listing/IPO；上市与财报同样是一次性事件，接受它。
        def _anchored(t):
            if type_a == 'earnings':
                return _has_financial_anchor(t) or re.search(r'\b(ipo|listing|filing)\b', str(t or '').lower())
            if type_a == 'funding':
                return _has_funding_signal(t)
            if type_a == 'ma':
                return _has_ma_signal(t)
            return True
        # 锚点词表本身有洞（'series ' 误命中剧集、'$' 误命中任何美元符），
        # 跨周合并不能只靠锚点——必须叠加相似度下限，否则同名公司的
        # 不相关报道（同为 funding 类型）会被误并（Rakuten 股价 vs K-pop 剧）。
        anchor_ok = _anchored(candidate.get('title', '')) and _anchored(existing.get('title', ''))
        if (anchor_ok
                and not _numeric_conflict_titles(candidate.get('title', ''), existing.get('title', ''))
                and _event_similarity(candidate, existing) >= 0.25):
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
            title_weak_ok = False
            if src_a == 'title' or src_b == 'title':
                # 标题提取的实体是弱信号，需相似度防误并（如两个同名小公司同日出融资）。
                # 例外：金额锚点一致（一边数字集是另一边子集，如同 $400M + 新报道多提
                # 估值细节）时豁免——金额是融资事件最强身份锚（Higgsfield 案）。
                weak_ok = _event_similarity(candidate, existing) >= 0.4
                if not weak_ok:
                    nums_a = _title_numbers(candidate.get('title', ''))
                    nums_b = _title_numbers(existing.get('title', ''))
                    if nums_a and nums_b and (nums_a <= nums_b or nums_b <= nums_a):
                        weak_ok = True
                if not weak_ok:
                    return False
                title_weak_ok = True
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
            # 同实体同类型同锚点仍需相似度下限：同一公司财报季/融资季每天有多篇独立
            # 评论（MercadoLibre 8-02 盘前 vs 8-05 财报点评），同锚点不代表同一事件。
            # title 弱键已在上方守过（0.4 或金额子集豁免），此处只约束 company/alias 键。
            if not title_weak_ok and _event_similarity(candidate, existing) < 0.3:
                return False
            # company/alias 来源可靠：同实体同日同类且锚点一致即同一事件
            return True
        if type_a == 'strategy':
            # 一实体一日可有多个策略事件，仍以标题相似度防误并
            return _event_similarity(candidate, existing) >= 0.42

    # 兜底一：同主类型 + 标题首主体词一致 + 相似度达标 + 数字不冲突。
    # 实体键提取不到或提取成垃圾键（Apple EU App Store 案键空、Nvidia 案键为
    # "nvidia agrees to" 垃圾串）时，用「标题开头是同一家公司」这一弱锚确认主体。
    # 单实例类型且双方锚点词均在（同为公司收购/融资/财报）时，措辞差异可再放宽：
    # 锚点+首实体+数字不冲突三重保护下 0.35 已足够（Nvidia/Hugging Face 三报道案）。
    sim = _event_similarity(candidate, existing)
    same_first_entity = (
        _first_title_entity(candidate.get('title', ''))
        and _first_title_entity(candidate.get('title', '')) == _first_title_entity(existing.get('title', ''))
    )
    no_num_conflict = not _numeric_conflict_titles(candidate.get('title', ''), existing.get('title', ''))
    if type_a == type_b and same_first_entity and no_num_conflict:
        anchor_ok = {
            'earnings': _has_financial_anchor(candidate.get('title', '')) and _has_financial_anchor(existing.get('title', '')),
            'funding': _has_funding_signal(candidate.get('title', '')) and _has_funding_signal(existing.get('title', '')),
            'ma': _has_ma_signal(candidate.get('title', '')) and _has_ma_signal(existing.get('title', '')),
        }.get(type_a, True)
        threshold = 0.35 if (type_a in _SINGULAR_EVENT_TYPES and anchor_ok) else 0.42
        if sim >= threshold:
            return True

    # 兜底二：无实体键或实体不同时，仅高标题相似度合并
    return sim >= 0.72

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
