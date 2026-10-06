"""P0 Agent 系列：标题改写、每日 AI 摘要、质量判定、BD 上下文与事件组装。

这一层消费 providers/llm 的 AI 通道，产出面向读者的文案与业务上下文。"""

import json
import os
import re

try:
    from repo_paths import DATA_DIR, data_path
    from analysis_quality import annotate_event_quality
    from content.classify import (
        _ai_event_types, _is_chinese_outbound_title, _normalize_canonical_key,
        _normalize_company_key, infer_signal_taxonomy,
    )
    from content.util import _cn_now, _cn_today, _is_http_url
    from event_contract import prepare_event_contract
    from event_dates import apply_event_date_metadata
    from event_value import classify_bd_priority, follow_up_window_for_priority
    from providers.llm import _chat_api_candidates, _post_chat
    from scope_gate import apply_scope_contract
except ImportError:
    from scripts.repo_paths import DATA_DIR, data_path
    from scripts.analysis_quality import annotate_event_quality
    from scripts.content.classify import (
        _ai_event_types, _is_chinese_outbound_title, _normalize_canonical_key,
        _normalize_company_key, infer_signal_taxonomy,
    )
    from scripts.content.util import _cn_now, _cn_today, _is_http_url
    from scripts.event_contract import prepare_event_contract
    from scripts.event_dates import apply_event_date_metadata
    from scripts.event_value import classify_bd_priority, follow_up_window_for_priority
    from scripts.providers.llm import _chat_api_candidates, _post_chat
    from scripts.scope_gate import apply_scope_contract


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
            os.makedirs(DATA_DIR, exist_ok=True)
            summary_data = {}
            try:
                with open(data_path('summary.json'), 'r', encoding='utf-8') as f:
                    summary_data = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            summary_data[today] = text
            with open(data_path('summary.json'), 'w', encoding='utf-8') as f:
                json.dump(summary_data, f, ensure_ascii=False, indent=2)

            print(f"  📊 AI趋势分析已生成（{api['name']}，{len(text)}字）: {text[:60]}...")
            return text
        except Exception as e:
            print(f"  ⚠️  趋势分析 {api['name']} 失败: {type(e).__name__}")
            continue
    return None

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
