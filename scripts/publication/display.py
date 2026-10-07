"""前端展示字段：标题清洗、判断拆分、趋势主题、公司卡片。"""

import re
from datetime import datetime, timedelta

try:
    from event_value import is_company_quality_signal
    from view_selectors import is_main_view_event
    from publication.entity import REGION_ORDER
except ImportError:
    from scripts.event_value import is_company_quality_signal
    from scripts.view_selectors import is_main_view_event
    from scripts.publication.entity import REGION_ORDER

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

def heat_label(event):
    """「不止一家在报」的角标文案；只有一个信源时返回空串，不占版面。

    优先说独立来源数，退而用报道篇数。差别来自本站的流水线顺序：去重层比归组层
    先跑，同一事件的多家报道入库时就被合并成一个事件。存量数据只留了被并 URL
    （merged_from），信源名已丢失，只能说「N 篇报道」；新数据起信源名会随合并
    一起记进 merged_sources，就能说「N 家来源」。两种都只表示覆盖面，不夸大。
    """
    try:
        sources = int(event.get('event_source_count') or 0)
    except (TypeError, ValueError):
        sources = 0
    if sources >= 2:
        return f'{sources} 家来源'
    try:
        reports = int(event.get('event_report_count') or 0)
    except (TypeError, ValueError):
        reports = 0
    if reports >= 2:
        return f'{reports} 篇报道'
    return ''


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
        event['heat_label'] = heat_label(event)
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
        # 公司热度：近 30 天里覆盖面最广的那条事件的角标（多家在报比单篇更值得先看）
        _heat_rows = sorted(
            (e for e in recent_30 if heat_label(e)),
            key=lambda e: (e.get('event_source_count') or 0, e.get('event_report_count') or 0),
            reverse=True,
        )
        company_heat_label = heat_label(_heat_rows[0]) if _heat_rows else ''

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
            'heat_label': company_heat_label,
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
