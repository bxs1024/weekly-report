"""事件归组与热度：把「同一件事的多篇报道」归成一个事件，并按独立来源计算热度。

对应 AIHOT 的 `events/relate.ts` + `group.ts`（见 docs/grouping.md）：
- 两两判断关系：SAME_OCCURRENCE / SAME_STORY / UNRELATED / ROUNDUP
- 同一次发生 → 合并为同一事件；同一事件的后续进展 → 挂在同一事件下
- 热度按**事件**算，不按文章算：每个独立来源只算一次，随时间衰减

本站差异（方案决策 + 历史教训）：
1. **拿不准不合并**（AIHOT 是拿不准就合并）。本站误并伤害大于漏并。
2. **规则优先**：canonical 指纹（canonical_company + canonical_key）仍是第一道，
   命中即合并，不调模型。AI 只处理指纹没命中、但可能是同事件的情况。
3. 有 `GROUP_ENABLED` 安全阀，关闭时行为与改动前完全一致。
4. 不改变既有展示逻辑：归组结果先落库（group_id / event_heat），
   由后续阶段决定是否接入排序。
"""

import os
import re
from collections import defaultdict
from datetime import datetime, timedelta

try:
    from prompt_loader import load_prompt, prompt_version
    from ai_receipts import receipt_key, receipt_get, receipt_put
except ImportError:
    from scripts.prompt_loader import load_prompt, prompt_version
    from scripts.ai_receipts import receipt_key, receipt_get, receipt_put

GROUP_ENABLED_ENV = 'GROUP_ENABLED'
RELATIONS = ('SAME_OCCURRENCE', 'SAME_STORY', 'UNRELATED', 'ROUNDUP')
# 热度时间窗：48 小时内每个独立来源只算一次（与 AIHOT 一致）
HEAT_WINDOW_HOURS = 48
# 发布超过该小时数的报道，热度减半
HEAT_HALF_LIFE_HOURS = 24
DEFAULT_MIN_CONFIDENCE = 0.75


def group_enabled():
    """安全阀：没设或设成 true/1/yes 时开启；显式关掉则完全跳过 AI 归组。"""
    raw = (os.environ.get(GROUP_ENABLED_ENV) or '').strip().lower()
    if raw == '':
        return True
    return raw in ('1', 'true', 'yes', 'on')


# ============================================================
# 规则层：canonical 指纹（第一道，不调模型）
# ============================================================

def fingerprint_of(event):
    """事件指纹：(规范主体, 量化锚点)。两者都非空才算有效指纹。"""
    company = (event.get('canonical_company') or '').strip().lower()
    key = (event.get('canonical_key') or '').strip().lower()
    if not company or not key:
        return None
    return (company, key)


def _title_similarity(a, b):
    """标题词集 Jaccard 相似度，用于仲裁主类型漂移的情况。"""
    def tokens(text):
        return set(re.findall(r'[a-z0-9\u4e00-\u9fff]+', (text or '').lower()))
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def rule_merge(a, b):
    """规则判定：是否同一事件。返回 True/False，None 表示规则无法判断（交给 AI）。

    沿用入库层既有口径（docs/plans/2026-08-24-event-dedup-ai-fingerprint.md）：
    指纹两项全匹配即合并；主类型不一致时用标题相似度 ≥0.42 仲裁，防 AI 判型漂移漏并。
    """
    fa, fb = fingerprint_of(a), fingerprint_of(b)
    if fa and fb and fa == fb:
        type_a = _primary_type(a)
        type_b = _primary_type(b)
        if type_a == type_b:
            return True
        return _title_similarity(a.get('title'), b.get('title')) >= 0.42
    return None


def _primary_type(event):
    types = event.get('event_types') or []
    if isinstance(types, str):
        return types.strip().lower()
    return (types[0] or '').strip().lower() if types else ''


# ============================================================
# AI 层：四关系判断
# ============================================================

def build_pair_material(a, b):
    """组装两篇报道的对照材料。刻意不传信源名气与旧分数。"""
    def view(event):
        return {
            'title': (event.get('title') or '').strip(),
            'overview': (event.get('content_overview') or event.get('summary_short') or '').strip(),
            'date': (event.get('date') or event.get('published_at') or '')[:19],
            'company': (event.get('canonical_company') or '').strip(),
            'key': (event.get('canonical_key') or '').strip(),
        }
    return {'a': view(a), 'b': view(b)}


def parse_relation(text):
    """解析模型回答：{'relation', 'confidence', 'difference'}；不合法返回 None。"""
    import json

    if not text:
        return None
    cleaned = re.sub(r'^```(?:json)?\s*', '', text.strip()).strip().rstrip('`').strip()
    try:
        data = json.loads(cleaned)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    relation = (data.get('relation') or '').strip().upper()
    if relation not in RELATIONS:
        return None
    try:
        confidence = float(data.get('confidence', 0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return {
        'relation': relation,
        'confidence': confidence,
        'difference': (data.get('difference') or '').strip()[:200],
    }


def _default_api():
    """取当前可用的 AI 通道；没有（未配 key 或总闸关闭）时返回 None。

    单独抽出来，是为了让 assign_groups 在进循环前就能知道「有没有通道」。
    否则每一对都会去问一次、问不到又记一次 ai_calls，统计会把
    「AI 跑了但一条没并」和「AI 根本没跑」混成一件事——排查时会往
    「提示词不行」的方向查，而真实原因是 key 没配或总闸被关了。
    """
    try:
        from providers.llm import _chat_api_candidates
        candidates = _chat_api_candidates()
        return candidates[0] if candidates else None
    except Exception:
        return None


def judge_pair(a, b, api=None, model_name=None):
    """判断两篇报道的关系。走回执，重复运行不重复付费。"""
    import json

    if api is None:
        api = _default_api()
    if api is None:
        return None
    if model_name is None:
        model_name = api.get('name') or 'default'

    system = load_prompt('group-pair')
    material = build_pair_material(a, b)
    key = receipt_key('group-pair', model_name, material)
    cached = receipt_get(key)
    if cached is not None:
        return cached

    from providers.llm import _post_chat  # 直连真实模块（P4）；不走 fetch_news 转发层

    body = system + "\n\n报道 A：\n" + json.dumps(material['a'], ensure_ascii=False) \
        + "\n\n报道 B：\n" + json.dumps(material['b'], ensure_ascii=False) + "\n\n只返回 JSON。"
    resp = _post_chat(api, body, max_tokens=220, temperature=0.0, timeout=(10, 60))
    if resp.status_code != 200:
        return None
    parsed = parse_relation(resp.json()['choices'][0]['message']['content'])
    if parsed is None:
        return None
    receipt_put(key, parsed, prompt_kind='group-pair', model=model_name)
    return parsed


def should_group(verdict, min_confidence=DEFAULT_MIN_CONFIDENCE):
    """是否合并成同一事件。

    SAME_STORY 也算「同一事件」（进展挂同一事件下，方案 P3 要求）；
    ROUNDUP 与 UNRELATED 不合并；置信度不足时不合并（本站保守口径）。
    """
    if not verdict:
        return False
    if verdict['relation'] not in ('SAME_OCCURRENCE', 'SAME_STORY'):
        return False
    return verdict['confidence'] >= min_confidence


# ============================================================
# 归组主流程
# ============================================================

def assign_groups(events, model_name=None, min_confidence=DEFAULT_MIN_CONFIDENCE,
                  candidate_window_days=14, max_ai_pairs=200):
    """给一批事件分配 group_id，并标注归组方式。

    先在近 candidate_window_days 天内按规则层找候选（指纹命中直接合并），
    规则无法判断的再按时间邻近逐个问 AI（受 max_ai_pairs 限制，防费用失控）。

    原地写入：group_id、group_role（origin/followup）、group_method（rule/ai/single）。
    返回统计。
    """
    stats = {'groups': 0, 'rule_merges': 0, 'ai_merges': 0, 'ai_calls': 0,
             'followups': 0, 'enabled': group_enabled()}
    if not events:
        return stats

    # 通道先探一次：没通道就直接跳过 AI 层，ai_calls 保持 0，
    # 免得把「没跑」记成「跑了没并」。
    api = _default_api()
    stats['ai_channel'] = api is not None

    ordered = sorted(events, key=lambda e: (e.get('date') or '', e.get('event_id') or ''))
    groups = {}           # group_id -> 代表事件
    group_events = defaultdict(list)

    for event in ordered:
        merged = False

        # 第一道：规则指纹，与已有各组的代表事件比对
        for gid, rep in groups.items():
            verdict = rule_merge(event, rep)
            if verdict is True:
                event['group_id'] = gid
                event['group_role'] = 'origin'
                event['group_method'] = 'rule'
                group_events[gid].append(event)
                stats['rule_merges'] += 1
                merged = True
                break

        if not merged and api is not None and stats['enabled'] \
                and stats['ai_calls'] < max_ai_pairs:
            # 第二道：AI 判断与最近事件的 SAME_STORY 关系（进展）
            for gid, rep in _recent_groups(groups, event, candidate_window_days):
                verdict = judge_pair(rep, event, api=api, model_name=model_name)
                stats['ai_calls'] += 1
                if should_group(verdict, min_confidence):
                    event['group_id'] = gid
                    event['group_role'] = 'followup' if verdict['relation'] == 'SAME_STORY' else 'origin'
                    event['group_method'] = 'ai'
                    group_events[gid].append(event)
                    stats['ai_merges'] += 1
                    if verdict['relation'] == 'SAME_STORY':
                        stats['followups'] += 1
                    merged = True
                    break
                if stats['ai_calls'] >= max_ai_pairs:
                    break

        if not merged:
            gid = event.get('event_id') or f'g{len(groups) + 1}'
            event['group_id'] = gid
            event['group_role'] = 'origin'
            event['group_method'] = 'single'
            groups[gid] = event
            group_events[gid].append(event)

    stats['groups'] = len(groups)
    for event in events:
        event['group_size'] = len(group_events.get(event.get('group_id'), []))
    return stats


def _recent_groups(groups, event, window_days):
    """按时间邻近筛出值得问 AI 的组，避免与全部历史比对。"""
    current = _parse_date(event.get('date') or event.get('published_at'))
    if current is None:
        return []
    result = []
    for gid, rep in groups.items():
        rep_date = _parse_date(rep.get('date') or rep.get('published_at'))
        if rep_date is None:
            continue
        if abs((current - rep_date).days) <= window_days:
            result.append((gid, rep))
    return result


def _parse_date(value):
    if not value:
        return None
    text = str(value)[:10]
    try:
        return datetime.strptime(text, '%Y-%m-%d')
    except ValueError:
        return None


# ============================================================
# 热度：按事件算，不按文章算
# ============================================================

def compute_heat(events, now):
    """计算事件热度：48 小时内每个独立来源只算一次，24 小时以上减半。

    热度 = Σ(来源权重) ，来源权重 = 1.0（48h 内）/ 0.5（超过 24h 的衰减档）。

    来源要把**同日被合并的兄弟报道**算进来。本站的去重层比 AIHOT 先跑，同一事件
    的多家报道在入库时就被合并成一个事件了，只剩 merged_from（URL）与
    merged_sources（信源名）。只看幸存事件的 source 字段，任何事件都会数成 1 家，
    热度就退化成一个常数——这不是热度，是恒等式。

    `now` 是必填锚点，由调用方给（见 fetch_news._data_now）。这里刻意不回落
    datetime.now()：拿机器时钟去减数据日期，数据停更一段时间后所有来源都会
    超出 48h 窗口、热度集体归零，而且同一份数据每天跑出的值都不同、无法复现。
    少传参数应当当场报错，而不是悄悄换一个「现在」。

    返回 {group_id: {'heat', 'sources', 'size', 'reports'}}，并原地写
    event_heat / event_source_count / event_report_count。
    """
    per_group = defaultdict(lambda: {'sources': set(), 'size': 0, 'reports': 0,
                                     'per_source': {}})

    for event in events:
        gid = event.get('group_id')
        if not gid:
            continue
        published = _parse_date(event.get('date') or event.get('published_at'))
        age_hours = (now - published).total_seconds() / 3600 if published else 0

        bucket = per_group[gid]
        bucket['size'] += 1
        # 报道篇数：幸存事件本身 1 篇，加上同日被并进来的每条 URL。
        # 这个数在存量数据里就有（merged_from），不依赖 AI。
        bucket['reports'] += 1 + len(event.get('merged_from') or [])

        if age_hours > HEAT_WINDOW_HOURS:
            continue
        weight = 1.0 if age_hours <= HEAT_HALF_LIFE_HOURS else 0.5
        # 同一来源只算一次：取该来源出现过的最大权重
        for source in _event_sources(event):
            bucket['sources'].add(source)
            bucket['per_source'][source] = max(bucket['per_source'].get(source, 0.0), weight)

    result = {}
    for gid, bucket in per_group.items():
        result[gid] = {
            'heat': round(sum(bucket['per_source'].values()), 3),
            'sources': len(bucket['sources']),
            'size': bucket['size'],
            'reports': bucket['reports'],
        }

    for event in events:
        gid = event.get('group_id')
        if gid in result:
            event['event_heat'] = result[gid]['heat']
            event['event_source_count'] = result[gid]['sources']
            event['event_report_count'] = result[gid]['reports']
    return result


def _event_sources(event):
    """一条事件背后出现过的全部媒体信源（含同日被合并的兄弟报道），已归一化去重。

    只认「谁发的稿」：publisher 是原始媒体（本站经 Google News 转载时，source
    会写成聚合器 "Google News"，真实媒体落在 publisher），其余情况下 source
    本身就是媒体。实测数据里 publisher 非空 ⟺ source == 'Google News'，所以
    「publisher or source」即可，不会漏也不会重。

    刻意排除 origin_source_id：它是**被报道的公司**（Adyen、Naver 这类），
    不是信源。把它算进来，任何一条公司新闻都会凭空多出一个「来源」。
    """
    raw = [event.get('publisher') or event.get('source')]
    raw.extend(event.get('merged_sources') or [])
    seen = []
    for value in raw:
        name = str(value or '').strip().lower()
        if name and name not in seen:
            seen.append(name)
    return seen


def heat_rank(events):
    """按热度排序的事件列表（热度同分时按来源数、再按日期）。"""
    ordered = sorted(
        events,
        key=lambda e: (e.get('event_heat') or 0, e.get('event_source_count') or 0, e.get('date') or ''),
        reverse=True,
    )
    return ordered
