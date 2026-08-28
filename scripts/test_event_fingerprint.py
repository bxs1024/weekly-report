"""AI 事件指纹去重测试：canonical_company + canonical_key 合并（入库层 + 展示层）。

背景：2026-08-22 日报 feed 出现 Starcloud 融资 2.5 亿美元双报道重复——
入库层 0.4 相似度守卫拒绝合并（相似度 0.364），展示层正则主体提取错位（"us space data center"）。
本测试验证 AI 指纹路径三层修复：归一化、入库合并、展示层兜底、防误并、存量降级。
"""

from fetch_news import _is_same_event, _normalize_canonical_key, _fingerprint_match
from generate_html import dedupe_display_events


def event(**overrides):
    base = {
        'title': 'Starcloud Raises $250 Million To Build AI Data Centers in Space',
        'url': 'https://ventureburn.com/starcloud-raises-250-million-for-ai-data-centers-in-space/',
        'source': 'Ventureburn',
        'region': '非洲',
        'event_types': ['funding'],
        'summary_short': 'Starcloud获$250M建太空AI数据中心',
        'content_overview': 'Starcloud完成2.5亿美元融资，用于建设太空AI数据中心。',
        'reason': '非洲太空AI基础设施获巨额融资，卫星计算供应商迎合作机会',
        'impact': '卫星通信公司、太空计算硬件商',
        'insight_label': '资金流向',
        'trend_topic': '太空AI数据中心兴起',
        'companies': [],
        'is_company': False,
        'company_name': '',
        'canonical_company': 'Starcloud',
        'canonical_key': '250m',
        'date': '2026-08-22',
        'article_date': '2026-08-22',
    }
    base.update(overrides)
    return base


def test_normalize_amount_variants():
    """金额写法归一：不同币种/单位/语言 → 统一 m 单位"""
    assert _normalize_canonical_key('$250M') == '250m'
    assert _normalize_canonical_key('$250 Million') == '250m'
    assert _normalize_canonical_key('250m') == '250m'
    assert _normalize_canonical_key('2.5亿美元') == '250m'
    assert _normalize_canonical_key('€5M') == '5m'
    assert _normalize_canonical_key('$2.8B') == '2800m'
    assert _normalize_canonical_key('0.83b') == '830m'
    assert _normalize_canonical_key('8500万美元') == '85m'
    assert _normalize_canonical_key('') == ''


def test_normalize_non_amount():
    """非金额锚点：公司名小写去标点、人数/百分比保持"""
    assert _normalize_canonical_key('Readly') == 'readly'
    assert _normalize_canonical_key('Qistas') == 'qistas'
    assert _normalize_canonical_key('2000') == '2000'
    assert _normalize_canonical_key('34%') == '34%'
    assert _normalize_canonical_key(None) == ''


def test_starcloud_merges_in_storage_layer():
    """Starcloud 真实案例：标题写法差异大，指纹路径应合并（旧路径返回 False）"""
    e1 = event(  # Tech in Asia 视角
        title='US space data center startup Starcloud raises $250m',
        url='https://www.techinasia.com/news/space-data-center-startup-starcloud-raises-250m',
        canonical_company='Starcloud', canonical_key='$250M',
        summary_short='Starcloud获$250M建太空数据中心',
    )
    e2 = event()  # Ventureburn 视角
    assert _is_same_event(e1, e2) is True


def test_starcloud_merges_in_display_layer():
    """展示层兜底：任意顺序输入，两条 Starcloud 合并为 1 条"""
    e1 = event(
        title='US space data center startup Starcloud raises $250m',
        url='https://www.techinasia.com/news/space-data-center-startup-starcloud-raises-250m',
        canonical_company='Starcloud', canonical_key='$250M',
        summary_short='Starcloud获$250M建太空数据中心',
    )
    e2 = event()
    kept = dedupe_display_events([e1, e2])
    assert len(kept) == 1, f'期望合并为 1 条，实际 {len(kept)} 条'
    assert e2['url'] in kept[0].get('merged_from', []), '被合并事件 url 应记入 merged_from'


def test_no_merge_when_key_differs():
    """防误并：同主体同类型，融资额不同（250m vs 500m）→ 不合并"""
    e1 = event(canonical_key='250m')
    e2 = event(
        title='Starcloud secures another $500M round',
        url='https://example.com/starcloud-500m',
        canonical_key='500m',
        summary_short='Starcloud再获$500M融资',
    )
    assert _is_same_event(e1, e2) is False
    assert len(dedupe_display_events([e1, e2])) == 2


def test_no_merge_when_type_differs():
    """防误并：同主体同锚点字面，事件类型不同（funding vs strategy）→ 不合并"""
    e1 = event()
    e2 = event(
        title='Starcloud announces $250m expansion plan',
        url='https://example.com/starcloud-plan',
        event_types=['strategy'],
        canonical_key='250m',
        summary_short='Starcloud宣布$250M扩张计划',
    )
    assert _is_same_event(e1, e2) is False


def test_legacy_events_fall_back():
    """存量零影响：无指纹字段的事件走旧规则——Starcloud 原型在旧逻辑下仍返回 False（现状），不崩"""
    e1 = event(canonical_company='', canonical_key='')
    e2 = event(
        title='US space data center startup Starcloud raises $250m',
        url='https://www.techinasia.com/news/space-data-center-startup-starcloud-raises-250m',
        canonical_company='', canonical_key='',
    )
    assert _is_same_event(e1, e2) is False  # 旧规则行为保持不变（允许漏并，不允许误并）
    assert len(dedupe_display_events([e1, e2])) == 2


def test_type_drift_with_similar_titles_merges():
    """类型漂移仲裁（2026-08-28 马云增持真实案例）：同主体同锚点但 AI 前后判型
    funding/strategy 漂移，标题相同 → 指纹路径合并，两条都入过库时展示层也应合并"""
    e1 = event(
        title='Tech billionaire Jack Ma purchases $76.5M worth of Alibaba shares in vote of AI confidence',
        url='https://e.vnexpress.net/jack-ma-shares.html',
        canonical_company='Alibaba', canonical_key='76.5m',
        summary_short='马云斥资$76.5M增持阿里股票',
    )
    e2 = event(
        title='Jack Ma purchases $76.5M worth of Alibaba shares in vote of AI confidence',
        url='https://example.com/jack-ma-shares-again.html',
        canonical_company='Alibaba', canonical_key='76.5m',
        event_types=['strategy'],
        summary_short='马云增持阿里股票7650万美元',
    )
    assert _fingerprint_match(e1, e2) is True
    assert _is_same_event(e1, e2) is True
    kept = dedupe_display_events([e1, e2])
    assert len(kept) == 1
    assert e2['url'] in kept[0].get('merged_from', [])


def test_type_drift_with_dissimilar_titles_falls_back():
    """类型漂移仲裁防误并：同主体同锚点、判型漂移但标题完全不像 → 指纹不判同
    （返回 None 交回旧规则），旧规则按类型不同拒绝合并"""
    e1 = event(
        title='Tech billionaire Jack Ma purchases $76.5M worth of Alibaba shares in vote of AI confidence',
        url='https://example.com/jack-ma-shares.html',
        canonical_company='Alibaba', canonical_key='76.5m',
    )
    e2 = event(
        title='Alibaba reshuffles cloud unit leadership after quarterly review',
        url='https://example.com/alibaba-cloud-reshuffle.html',
        canonical_company='Alibaba', canonical_key='76.5m',
        event_types=['strategy'],
    )
    assert _fingerprint_match(e1, e2) is None
    assert _is_same_event(e1, e2) is False


def test_dedupe_display_preserves_other_events():
    """展示层指纹兜底不误伤：同一天无关事件（无指纹或指纹不同）都保留"""
    e1 = event()
    e2 = event(
        title='Cloudflare adds OAuth scope options for developer tools',
        url='https://developers.cloudflare.com/changelog/post/2026-08-22-wrangler-mcp-optional-oauth-scopes/',
        canonical_company='Cloudflare', canonical_key='',
        summary_short='Cloudflare为开发者工具增加OAuth作用域选项',
    )
    kept = dedupe_display_events([e1, e2])
    assert len(kept) == 2