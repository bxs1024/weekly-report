"""信源标注与 HTML 降级采集的判据测试。

P4 后这些函数住在 sources/meta.py、sources/html_fallback.py、content/*.py。
测试一律**直接引真实模块**：转发层只给外部脚本兜底，测试若打在 fetch_news 上，
`fetch_news.X = fake` 会静默打空（打不到真实调用点），比报错危险得多。
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bs4 import BeautifulSoup  # noqa: E402

try:
    from content import util as _util
    from content.classify import _get_company_aliases, _title_mentions_aliases
    from sources import html_fallback
    from sources.meta import _registry_source_to_cfg, _with_source_meta
except ImportError:
    from scripts.content import util as _util
    from scripts.content.classify import _get_company_aliases, _title_mentions_aliases
    from scripts.sources import html_fallback
    from scripts.sources.meta import _registry_source_to_cfg, _with_source_meta


def test_official_company_title_gets_entity_prefix():
    item = _with_source_meta(
        {
            'title': 'Financial Results for Fiscal Year Ended March 31, 2026',
            'url': 'https://example.com/square-enix',
            'source': 'Square Enix',
            'region': '亚太',
            'event_types': ['earnings'],
            'is_company': True,
            'company_name': 'Square Enix',
        },
        {
            'name': 'Square Enix IR News',
            'source_tier': 'L1 官方/IR源',
            'source_role': 'official_ir',
        },
    )
    assert item['title'].startswith('Square Enix: ')
    assert _title_mentions_aliases(item['title'], _get_company_aliases('Square Enix'))


def test_l1_changelog_infers_entity_name_without_changelog_suffix():
    cfg = _registry_source_to_cfg({
        'id': 'stripe-changelog',
        'name': 'Stripe Changelog',
        'url': 'https://stripe.com/changelog',
        'tier': 'L1',
        'source_type': 'changelog',
        'access_method': 'html',
        'region': '全球',
    })
    assert cfg['company_name'] == 'Stripe'
    assert cfg['is_company'] is True


def test_official_article_date_extracted_from_title_and_url():
    assert html_fallback._extract_official_article_date(
        'Square Enix: May 14, 2026 Results Briefing Session',
        'https://www.hd.square-enix.com/eng/ir/pdf/26q4slides.pdf',
    ) == '2026-05-14'
    assert html_fallback._extract_official_article_date(
        'Notice of Revisions to Full-Year Consolidated Financial Forecasts',
        'https://www.hd.square-enix.com/eng/ir/pdf/20260205_02_en.pdf',
    ) == '2026-02-05'


def test_official_date_prefers_dashed_url_over_future_body_date():
    meta = html_fallback._extract_official_article_date_meta(
        'Cloudflare: New options to manage AI traffic',
        'https://developers.cloudflare.com/changelog/post/2026-07-01-ai-traffic-options/',
        'This option becomes effective on September 15, 2026.',
        observed_at='2026-07-15T14:30:00+08:00',
    )
    assert meta['published_at'] == '2026-07-01'
    assert meta['date_source'] == 'url_path'
    assert meta['date_parse_warning'] == ''


def test_future_body_date_is_not_used_as_publication_date():
    meta = html_fallback._extract_official_article_date_meta(
        'Cloudflare Tunnel API',
        'https://developers.cloudflare.com/api/resources/zero_trust/subresources/tunnels/',
        'The change is scheduled for October 5, 2026.',
        observed_at='2026-07-15T14:30:00+08:00',
    )
    assert meta['published_at'] == ''
    assert meta['scheduled_at'] == '2026-10-05'
    assert meta['date_parse_warning'] == 'future_published_at_reclassified'


def test_official_html_skips_stale_ir_items():
    # 补丁必须打在 html_fallback 上：fetch_html 解析的是本模块的全局 fetch_url。
    old_fetch_url = html_fallback.fetch_url
    try:
        html_fallback.fetch_url = lambda url: """
        <html><body>
          <a href="/eng/ir/pdf/26q4slides.pdf">
            May 14, 2026 Results Briefing Session for the Fiscal Year ended March 31, 2026
          </a>
        </body></html>
        """
        items = html_fallback.fetch_html({
            'name': 'Square Enix IR News',
            'url': 'https://www.hd.square-enix.com/eng/ir/irnews/',
            'source': 'Square Enix',
            'region': '亚太',
            'priority': 3,
            'source_tier': 'L1 官方/IR源',
            'source_role': 'official_ir',
            'company_name': 'Square Enix',
            'is_company': True,
            'max': 4,
        })
    finally:
        html_fallback.fetch_url = old_fetch_url

    assert items == []


def test_changelog_items_extract_direct_dated_links():
    soup = BeautifulSoup(
        """
        <html><body>
          <nav><a href="/pricing">Pricing</a></nav>
          <article>
            <time>June 28, 2026</time>
            <a href="/posts/custom-draft-order-discounts">
              Custom draft order line item discounts now use presentment currency
            </a>
          </article>
          <article>
            <time>June 10, 2025</time>
            <a href="/posts/old-api-update">Old API update</a>
          </article>
        </body></html>
        """,
        'html.parser',
    )
    cfg = _registry_source_to_cfg({
        'id': 'shopify-changelog',
        'name': 'Shopify Changelog',
        'url': 'https://changelog.shopify.com/',
        'tier': 'L1',
        'source_type': 'changelog',
        'access_method': 'html',
        'region': '全球',
        'priority': 3,
    })
    # _cn_now 住在 content/util.py，_recent_article_date 在 util 命名空间解析它；
    # html_fallback 只是 import 了 _recent_article_date，因此补丁打在 util 上即可。
    frozen = lambda: datetime.fromisoformat('2026-06-29T12:00:00+08:00')  # noqa: E731
    old_util_cn_now = _util._cn_now
    try:
        _util._cn_now = frozen
        items = html_fallback._select_changelog_items(soup, cfg)
    finally:
        _util._cn_now = old_util_cn_now
    assert len(items) == 1
    assert items[0]['company_name'] == 'Shopify'
    assert items[0]['article_date'] == '2026-06-28'
    assert items[0]['event_types'] == ['strategy']
    assert 'developer_change' in items[0]['signal_taxonomy']


if __name__ == '__main__':
    test_official_company_title_gets_entity_prefix()
    test_l1_changelog_infers_entity_name_without_changelog_suffix()
    test_official_article_date_extracted_from_title_and_url()
    test_official_date_prefers_dashed_url_over_future_body_date()
    test_future_body_date_is_not_used_as_publication_date()
    test_official_html_skips_stale_ir_items()
    test_changelog_items_extract_direct_dated_links()
    print('fetch news source meta tests passed')
