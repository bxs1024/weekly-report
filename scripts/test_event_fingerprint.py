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
    """无指纹事件走旧规则兜底：同实体键（title 弱键）+同类型+措辞相近仍合并。
    2026-09-02 起规则层放宽后，无指纹但同实体同类型的同一事件也能并（Starcloud 案）。"""
    e1 = event(canonical_company='', canonical_key='')
    e2 = event(
        title='US space data center startup Starcloud raises $250m',
        url='https://www.techinasia.com/news/space-data-center-startup-starcloud-raises-250m',
        canonical_company='', canonical_key='',
    )
    assert _is_same_event(e1, e2) is True  # 新规则：同实体键+同类型+措辞相近 → 合并
    assert len(dedupe_display_events([e1, e2])) == 1


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


def _pair(title_a, title_b, date_a, date_b, url_a, url_b, **kw):
    """构造两条真实标题的候选对，用于黄金案例回归。b 继承 a 的 event_types 并清指纹。"""
    a = event(title=title_a, url=url_a, date=date_a, article_date=date_a, **kw)
    b = event(title=title_b, url=url_b, date=date_b, article_date=date_b,
              event_types=kw.get('event_types', a['event_types']),
              canonical_company='', canonical_key='')
    return a, b


def test_golden_cases_merge_wording_different_same_event():
    """黄金回归：措辞差异大的同一事件必须合并（Stripe/Apple/Higgsfield/Nvidia/
    支付宝/Kakao）。这些是 2026-09-02 全库审计确认漏并的真实案例，措辞字面
    相似度 0.15-0.46，靠锚点词表+别名/实体键+金额子集豁免+上市锚点才判同。"""
    cases = [
        _pair(
            'Stripe will reportedly acquire AI gateway startup OpenRouter for $7B+',
            'Stripe reportedly acquires OpenRouter, the AI model router, for over $7bn',
            '2026-08-16', '2026-08-17', 'https://a/1', 'https://b/1',
            event_types=['ma'], canonical_company='', canonical_key='',
        ),
        _pair(
            'Apple overhauls its EU App Store fees, loosens rules for alternative stores',
            'Apple overhauls EU App Store fees to settle its DMA dispute',
            '2026-08-18', '2026-08-18', 'https://c/1', 'https://d/1',
            event_types=['strategy'], canonical_company='', canonical_key='',
        ),
        _pair(
            'Higgsfield Raises $400M to Scale AI Visual Creation',
            'Higgsfield raises $400M Series B, quadrupling its valuation in 8 months',
            '2026-08-17', '2026-08-17', 'https://e/1', 'https://f/1',
            event_types=['funding'], canonical_company='', canonical_key='',
        ),
        _pair(
            'Nvidia closes in on Hugging Face acquisition',
            'Nvidia agrees to buy Hugging Face for $12.9BN, says report',
            '2026-08-27', '2026-08-27', 'https://g/1', 'https://h/1',
            event_types=['ma'], canonical_company='', canonical_key='',
        ),
        _pair(
            'Alipay launches full stack agentic commerce platform in China',
            'Alipay launches agentic commerce platform in China to bring AI tools to merchants',
            '2026-08-17', '2026-08-18', 'https://i/1', 'https://j/1',
            event_types=['strategy'], canonical_company='', canonical_key='',
        ),
        _pair(
            'Kakao Mobility Board Approves U.S. ADR Listing as TPG Takes the Lead',
            'Kakao Mobility Eyes US Listing via ADRs',
            '2026-08-17', '2026-08-21', 'https://k/1', 'https://l/1',
            event_types=['earnings'], canonical_company='', canonical_key='',
        ),
    ]
    for a, b in cases:
        assert _is_same_event(a, b), f'应判为同一事件: {a["title"]} || {b["title"]}'


def test_golden_sentinels_no_false_merge():
    """黄金哨兵：不同事件绝不误并（不同报道/不同金额/不同合作方）。"""
    cases = [
        _pair(
            'Instagram growth continues to outpace Facebook in EU',
            'Instagram updates tags for AI profiles',
            '2026-08-31', '2026-08-31', 'https://m/1', 'https://n/1',
            event_types=['strategy'], canonical_company='', canonical_key='',
        ),
        _pair(
            'Tencent Cloud Partners with Logistics Platform TruKKer',
            'Tencent Cloud partners with TruKKer in Saudi Arabia ahead of expansion',
            '2026-08-27', '2026-08-31', 'https://o/1', 'https://p/1',
            event_types=['strategy'], canonical_company='', canonical_key='',
        ),
        _pair(
            'Starcloud Raises $250 Million To Build AI Data Centers in Space',
            'Starcloud secures another $500M round',
            '2026-08-22', '2026-08-23', 'https://q/1', 'https://r/1',
            event_types=['funding'], canonical_company='Starcloud', canonical_key='250m',
        ),
    ]
    for a, b in cases:
        assert not _is_same_event(a, b), f'不应误并: {a["title"]} || {b["title"]}'

def _run_all():
    test_normalize_amount_variants()
    test_normalize_non_amount()
    test_starcloud_merges_in_storage_layer()
    test_starcloud_merges_in_display_layer()
    test_no_merge_when_key_differs()
    test_no_merge_when_type_differs()
    test_legacy_events_fall_back()
    test_type_drift_with_similar_titles_merges()
    test_type_drift_with_dissimilar_titles_falls_back()
    test_dedupe_display_preserves_other_events()
    test_golden_cases_merge_wording_different_same_event()
    test_golden_sentinels_no_false_merge()
    print('event fingerprint tests passed')


if __name__ == '__main__':
    _run_all()
