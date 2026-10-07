"""
全球互联网动态情报站 — 数据采集
目标：融资 | 并购 | 财报披露 | 重大战略 — 发现 ICT 合作机会点
"""

import json, os, time, re, hashlib
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlparse
import feedparser

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import warnings; warnings.filterwarnings('ignore')
import requests
from bs4 import BeautifulSoup

# 提示词外置：全部 AI 提示词在 scripts/prompts/*.md（P1，见 docs/ARCHITECTURE.md）
try:
    from prompt_loader import load_prompt, prompt_version
except ImportError:
    from scripts.prompt_loader import load_prompt, prompt_version

# P2/P3 新链路：AI 双评分与事件归组。两者各自带安全阀（默认开，可单独关停回退）。
# 直连真实模块——走 fetch_news 转发层会让 monkeypatch 静默打空（见 ARCHITECTURE.md 四条铁律）。
try:
    import ai_scoring
    import event_grouping
except ImportError:
    from scripts import ai_scoring, event_grouping

# 仓库路径锚定 __file__，不依赖调用进程 CWD（见 docs/ARCHITECTURE.md「路径锚定仓库根」）。
# 裸相对路径 'data/...' 只在 CWD=仓库根 时正确；从 scripts/ 直接运行脚本或测试时
# 会指向 scripts/data/，读出空数据或抛 FileNotFoundError。
try:
    from repo_paths import REPO_ROOT, DATA_DIR, data_path, docs_path
except ImportError:
    from scripts.repo_paths import REPO_ROOT, DATA_DIR, data_path, docs_path

# 共享常量表（信源配置与分类词表）已外置到 constants.py（P4）。
# 此处 re-export，保持既有 `from fetch_news import RSS_SOURCES` 等 import 不变。
try:
    from constants import (
        HEADERS, REQUEST_DELAY, REQUEST_TIMEOUT,
        RSS_SOURCES, HTML_SOURCES, COMPANY_SOURCES, COMPANY_ALIASES, COMPANY_BLACKLIST,
        COMPANY_LOW_SIGNAL_PATTERNS, CHINESE_OUTBOUND_PATTERNS,
        TRADITIONAL_BANKS, BANK_PROTECTED_FINTECH,
        TITLE_STOPWORDS, EVENT_ENTITY_STOPWORDS, SECTOR_SCOPE_MAP,
    )
except ImportError:
    from scripts.constants import (
        HEADERS, REQUEST_DELAY, REQUEST_TIMEOUT,
        RSS_SOURCES, HTML_SOURCES, COMPANY_SOURCES, COMPANY_ALIASES, COMPANY_BLACKLIST,
        COMPANY_LOW_SIGNAL_PATTERNS, CHINESE_OUTBOUND_PATTERNS,
        TRADITIONAL_BANKS, BANK_PROTECTED_FINTECH,
        TITLE_STOPWORDS, EVENT_ENTITY_STOPWORDS, SECTOR_SCOPE_MAP,
    )

# 通用工具已外置到 content/util.py（P4）。此处 re-export 保持既有 import 不变。
try:
    from content.util import (
        _cn_now, _cn_today, _parse_date, _recent_article_date,
        _normalize_text, _title_tokens, _strip_title_source,
        _extract_title_publisher, _parse_iso_date, _is_http_url, _same_host_url,
        _extract_date_from_url,
    )
except ImportError:
    from scripts.content.util import (
        _cn_now, _cn_today, _parse_date, _recent_article_date,
        _normalize_text, _title_tokens, _strip_title_source,
        _extract_title_publisher, _parse_iso_date, _is_http_url, _same_host_url,
        _extract_date_from_url,
    )

# 判型/身份识别与信源元数据已外置到 content/classify.py 与 content/source_meta.py（P4）。
try:
    from content.classify import (
        detect_event_types, SIGNAL_TAXONOMY, infer_signal_taxonomy,
        REGION_TITLE_KEYWORDS, infer_event_region,
        BLACKLIST_COMPANIES, BLACKLIST_PATTERNS, is_blacklisted,
        _normalize_event_subject, _title_subject_key, _event_subject_key,
        _FINANCIAL_NEGATIVE_WORDS, _has_negative_financial_word,
        _financial_direction_consistent, _get_company_aliases,
        _title_mentions_aliases, _title_mentions_company,
        _is_low_signal_company_title, _is_traditional_bank_item,
        _is_chinese_outbound_title, _is_official_company_source,
        _is_vertical_source, _is_high_signal_vertical_title,
        _event_similarity, _COMPANY_KEY_SUFFIXES, _GENERIC_ALIAS_TOKENS,
        _SINGULAR_EVENT_TYPES, _EVENT_TYPE_PRIORITY, _normalize_company_key,
        _entity_key_info, _entity_key_info_cached, _primary_event_type,
        _FINANCIAL_ANCHOR_WORDS, _FUNDING_SIGNAL_WORDS, _MA_SIGNAL_WORDS,
        _has_financial_anchor, _has_funding_signal, _has_ma_signal,
        _dates_adjacent, _normalize_canonical_key, _fingerprint_match,
        _VALID_EVENT_TYPES, _ai_event_types,
    )
    from sources.meta import (
        _source_meta, _with_source_meta, REGISTRY_TIER_MAP, REGISTRY_ROLE_MAP,
        _registry_source_to_cfg, _source_entity_name, _source_metric_key,
        _count_by_source, _source_funnel_stage, _merge_source_funnel,
        load_registry_sources, _is_official_cfg,
    )
except ImportError:
    from scripts.content.classify import (
        detect_event_types, SIGNAL_TAXONOMY, infer_signal_taxonomy,
        REGION_TITLE_KEYWORDS, infer_event_region,
        BLACKLIST_COMPANIES, BLACKLIST_PATTERNS, is_blacklisted,
        _normalize_event_subject, _title_subject_key, _event_subject_key,
        _FINANCIAL_NEGATIVE_WORDS, _has_negative_financial_word,
        _financial_direction_consistent, _get_company_aliases,
        _title_mentions_aliases, _title_mentions_company,
        _is_low_signal_company_title, _is_traditional_bank_item,
        _is_chinese_outbound_title, _is_official_company_source,
        _is_vertical_source, _is_high_signal_vertical_title,
        _event_similarity, _COMPANY_KEY_SUFFIXES, _GENERIC_ALIAS_TOKENS,
        _SINGULAR_EVENT_TYPES, _EVENT_TYPE_PRIORITY, _normalize_company_key,
        _entity_key_info, _entity_key_info_cached, _primary_event_type,
        _FINANCIAL_ANCHOR_WORDS, _FUNDING_SIGNAL_WORDS, _MA_SIGNAL_WORDS,
        _has_financial_anchor, _has_funding_signal, _has_ma_signal,
        _dates_adjacent, _normalize_canonical_key, _fingerprint_match,
        _VALID_EVENT_TYPES, _ai_event_types,
    )
    from scripts.sources.meta import (
        _source_meta, _with_source_meta, REGISTRY_TIER_MAP, REGISTRY_ROLE_MAP,
        _registry_source_to_cfg, _source_entity_name, _source_metric_key,
        _count_by_source, _source_funnel_stage, _merge_source_funnel,
        load_registry_sources, _is_official_cfg,
    )

# AI 通道（含共用的 _LLM_SESSION）已外置到 providers/llm.py（P4）。
# fetch_news 仍 re-export，给**外部脚本与测试**兜底；主链路一律直连 providers.llm。
try:
    from providers.llm import (
        _LLM_SESSION,
        configure_minimax, analyze_events_minimax,
        configure_doubao, configure_deepseek, configure_ark,
        analyze_events_ark, analyze_events_deepseek, analyze_events_doubao,
        analyze_single_event_minimax, analyze_single_event_doubao,
        ANALYSIS_PROMPT_FILES, build_analysis_prompt, analysis_prompt_versions,
        _results_by_url, _chat_api_candidates, _post_chat,
    )
except ImportError:
    from scripts.providers.llm import (
        _LLM_SESSION,
        configure_minimax, analyze_events_minimax,
        configure_doubao, configure_deepseek, configure_ark,
        analyze_events_ark, analyze_events_deepseek, analyze_events_doubao,
        analyze_single_event_minimax, analyze_single_event_doubao,
        ANALYSIS_PROMPT_FILES, build_analysis_prompt, analysis_prompt_versions,
        _results_by_url, _chat_api_candidates, _post_chat,
    )

try:
    from zoneinfo import ZoneInfo
    SHANGHAI_TZ = ZoneInfo('Asia/Shanghai')
except Exception:
    SHANGHAI_TZ = timezone(timedelta(hours=8))

try:
    from analysis_quality import annotate_event_quality, summarize_quality
    from event_dates import apply_event_date_metadata, publication_metadata
    from event_contract import prepare_event_contract
    from event_value import classify_bd_priority, follow_up_window_for_priority
    from run_metrics import write_run_metrics
    from scope_gate import apply_scope_contract
    from internet_relevance import assess_internet_relevance
except ImportError:
    from scripts.analysis_quality import annotate_event_quality, summarize_quality
    from scripts.event_dates import apply_event_date_metadata, publication_metadata
    from scripts.event_contract import prepare_event_contract
    from scripts.event_value import classify_bd_priority, follow_up_window_for_priority
    from scripts.run_metrics import write_run_metrics
    from scripts.scope_gate import apply_scope_contract
    from scripts.internet_relevance import assess_internet_relevance

# HTTP 抓取底座（含 aiohttp 引导与响应缓存）已外置到 sources/http.py（P4）。
# 此处 re-export，保持既有 `from fetch_news import fetch_url` / `HAS_AIOHTTP` 不变；
# 本文件剩余的 aiohttp/asyncio 用法（fill_event_images、main）也从这里取。
try:
    from sources.http import (
        HAS_AIOHTTP, aiohttp, asyncio,
        fetch_url, fetch_url_async, fetch_all_parallel,
        CACHE_DIR, CACHE_TTL, _cache_key, _cache_get, _cache_set, _clear_old_cache,
    )
except ImportError:
    from scripts.sources.http import (
        HAS_AIOHTTP, aiohttp, asyncio,
        fetch_url, fetch_url_async, fetch_all_parallel,
        CACHE_DIR, CACHE_TTL, _cache_key, _cache_get, _cache_set, _clear_old_cache,
    )


# ============================================================
# ���源：重点标注是否为融资专属源
# ============================================================


# ============================================================
# 27家重点公司监控 — Google News RSS
# ============================================================


for _company_cfg in COMPANY_SOURCES:
    _company_cfg.setdefault('source_tier', 'L5 Google News 补漏源')
    _company_cfg.setdefault('source_role', 'company_radar')
    _company_cfg.setdefault('max', 2)
    _company_cfg.setdefault('max_other', 0)


# 27 家重点公司的观察范围契约已外置到 sources/company_watch.py（P4）。
try:
    from sources.company_watch import (
        _load_company_scope_contracts, COMPANY_SCOPE_CONTRACTS, _apply_company_scope_contract,
    )
except ImportError:
    from scripts.sources.company_watch import (
        _load_company_scope_contracts, COMPANY_SCOPE_CONTRACTS, _apply_company_scope_contract,
    )

for _company_cfg in COMPANY_SOURCES:
    _apply_company_scope_contract(_company_cfg)

# ============================================================
# 关键词检测（宽松模式，宁多不漏）
# ============================================================


# 事实评分账本与事件判重已外置到 content/dedupe.py（P4）。
# 此处 re-export，保持既有 `from fetch_news import _is_same_event` 等不变。
#
# ⚠️ `_fact_ledger` 是会**重新绑定**的模块级可变状态：`_load_fact_ledger()`
# 用 `global` 重绑它。转发层这份绑定只是 import 时的快照（空 dict），读实时值
# 必须走 `dedupe._fact_ledger`。本文件内部与测试已全部改用实时引用。
try:
    from content.dedupe import (
        _FACT_LEDGER_PATH, _fact_ledger,
        _load_fact_ledger, _save_fact_ledger, _fact_ledger_key, _apply_fact_score_rules,
        _first_title_entity, _title_numbers, _numeric_conflict_titles,
        _is_same_event, _event_info_score, _is_more_complete, _upgrade_event,
    )
    from content import dedupe
except ImportError:
    from scripts.content.dedupe import (
        _FACT_LEDGER_PATH, _fact_ledger,
        _load_fact_ledger, _save_fact_ledger, _fact_ledger_key, _apply_fact_score_rules,
        _first_title_entity, _title_numbers, _numeric_conflict_titles,
        _is_same_event, _event_info_score, _is_more_complete, _upgrade_event,
    )
    from scripts.content import dedupe


# RSS 采集（原「工具函数」+「采集」两段）已外置到 sources/collect.py（P4）。
# 此处 re-export，保持既有 `from fetch_news import _parse_rss_text` 等不变。
try:
    from sources.collect import (
        _parse_rss_date, _rss_date_metadata, RSS_URL_STOPWORDS,
        _rss_candidate_links, _rss_url_match_score, _select_rss_entry_link,
        _parse_rss_text, _qualified_signal_count, fetch_rss,
    )
except ImportError:
    from scripts.sources.collect import (
        _parse_rss_date, _rss_date_metadata, RSS_URL_STOPWORDS,
        _rss_candidate_links, _rss_url_match_score, _select_rss_entry_link,
        _parse_rss_text, _qualified_signal_count, fetch_rss,
    )

# HTML 备用采集已外置到 sources/html_fallback.py（P4）。
# 此处 re-export，保持既有 `from fetch_news import fetch_html` 等不变。
try:
    from sources.html_fallback import (
        HTML_SKIP_URL_PATTERNS, HTML_SKIP_TITLE_PATTERNS,
        OFFICIAL_SOURCE_LINK_PATTERNS, OFFICIAL_SOURCE_TITLE_PATTERNS,
        CHANGELOG_SOURCE_TITLE_PATTERNS, OFFICIAL_SOURCE_SKIP_URL_PATTERNS,
        OFFICIAL_SOURCE_NAV_TITLES, OFFICIAL_SOURCE_NAV_PREFIXES,
        _is_changelog_cfg, _official_link_allowed, _official_title_allowed,
        _select_official_articles, _EN_MONTHS, _format_date_parts,
        _extract_date_from_text, _extract_recent_month_day_date,
        _extract_official_article_date_meta, _extract_official_article_date,
        _select_changelog_items, fetch_company_news, fetch_html,
    )
except ImportError:
    from scripts.sources.html_fallback import (
        HTML_SKIP_URL_PATTERNS, HTML_SKIP_TITLE_PATTERNS,
        OFFICIAL_SOURCE_LINK_PATTERNS, OFFICIAL_SOURCE_TITLE_PATTERNS,
        CHANGELOG_SOURCE_TITLE_PATTERNS, OFFICIAL_SOURCE_SKIP_URL_PATTERNS,
        OFFICIAL_SOURCE_NAV_TITLES, OFFICIAL_SOURCE_NAV_PREFIXES,
        _is_changelog_cfg, _official_link_allowed, _official_title_allowed,
        _select_official_articles, _EN_MONTHS, _format_date_parts,
        _extract_date_from_text, _extract_recent_month_day_date,
        _extract_official_article_date_meta, _extract_official_article_date,
        _select_changelog_items, fetch_company_news, fetch_html,
    )


# 智能过滤与存储策略已外置到 content/filter.py（P4）。
try:
    from content.filter import (
        MAX_DAILY, MAX_PER_REGION, smart_filter, dedupe_events_by_day,
        apply_event_storage_policy,
    )
except ImportError:
    from scripts.content.filter import (
        MAX_DAILY, MAX_PER_REGION, smart_filter, dedupe_events_by_day,
        apply_event_storage_policy,
    )


# ============================================================
# MiniMax API（主力）
# ============================================================


# ============================================================
# 豆包分析（备份）
# ============================================================


# ============================================================
# AI 分析 Prompt（已外置到 scripts/prompts/，改提示词不用改代码）
#   analysis-system.md   系统提示词与 9 字段契约
#   analysis-examples.md Few-shot 示例
# 版本 = 文件内容 sha256 前 12 位，见 prompt_loader.prompt_version()
# ============================================================


# ============================================================
# P0 Agent：AI 标题改写 — 对程序层泛化事件用 AI 改写描述
# ============================================================

# P0 Agent 系列已外置到 editorial/agents.py（P4）。
try:
    from editorial.agents import (
        rewrite_titles_for_display, build_daily_ai_summary, ai_quality_judge, _calc_score,
        BD_TRIGGER_RULES, OPPORTUNITY_BY_TRIGGER, OPPORTUNITY_BY_TYPE,
        infer_bd_context, attach_business_context, attach_date_context, build_event,
    )
except ImportError:
    from scripts.editorial.agents import (
        rewrite_titles_for_display, build_daily_ai_summary, ai_quality_judge, _calc_score,
        BD_TRIGGER_RULES, OPPORTUNITY_BY_TRIGGER, OPPORTUNITY_BY_TYPE,
        infer_bd_context, attach_business_context, attach_date_context, build_event,
    )


# ============================================================
# P0 Agent：每日AI趋势分析 — 基于今日信号事件生成专业判断
# ============================================================


# ============================================================
# P0 Agent：情报价值评分 — AI过滤低价值 other 事件
# ============================================================


# ============================================================
# og:image 补抓 — 为没有 RSS 图片的事件获取文章配图
# ============================================================

# og:image 补抓已外置到 content/og_image.py（P4）。
try:
    from content.og_image import fill_event_images
except ImportError:
    from scripts.content.og_image import fill_event_images

# ============================================================
# 主函数
# ============================================================

# ============================================================
# P2/P3 新链路接线：AI 双评分 + 事件归组与热度
# ============================================================

# 归组/热度的处理窗口（天）。不设窗口就要每天重扫全部历史——3796 条约 20 秒 CPU，
# 外加最多 200 次 AI 调用；而归组的候选窗口本身只有 14 天，30 天足够覆盖。
# 历史事件的 group_id 在它们「当新」的那几次运行里已定，不需要每天重算。
GROUP_WINDOW_DAYS = 30

# 评分补漏窗口（天）。每天只评「窗口内且还没有 ai_score_avg」的事件——幂等，
# 重跑不重复付费（回执再兜一层）。对齐方案「日均 60 事件」的成本模型。
SCORE_WINDOW_DAYS = 7


def _window_events(all_events, days):
    """取最近 N 天的事件。日期键是 YYYY-MM-DD，字典序即时间序。"""
    keys = sorted(all_events.keys())[-days:]
    return [e for key in keys for e in all_events[key]]


def _run_ai_scoring(all_events, run_metrics):
    """P2：对窗口内还没评过的事件跑 AI 双评分，并排落库（不替换程序分）。

    安全阀 AI_SCORE_ENABLED 默认开；关掉则整层跳过，一个字段都不写。
    评分只排序不过滤，失败也不影响事件入库。
    """
    if not ai_scoring.ai_score_enabled():
        return
    targets = [e for e in _window_events(all_events, SCORE_WINDOW_DAYS)
               if e.get('ai_score_avg') is None]
    if not targets:
        return
    stats = ai_scoring.score_events(targets)
    run_metrics['ai_scoring'] = stats
    if stats['scored'] == 0 and stats['failed']:
        print(f"  🧮 AI 双评分：跳过 {stats['failed']} 条（AI 通道不可用）")
    else:
        print(f"  🧮 AI 双评分：{stats['scored']} 条"
              f"（回执复用 {stats['reused']}，失败 {stats['failed']}）")


def _run_event_grouping(all_events, run_metrics):
    """P3：窗口内事件做四关系归组 + 独立来源热度，原地写 group_* / event_heat。

    安全阀 GROUP_ENABLED 默认开；关掉则整层跳过。规则层（指纹合并）不依赖 AI，
    所以即使 AI 通道不可用，group_id 与热度仍然成立。
    """
    if not event_grouping.group_enabled():
        return
    window = _window_events(all_events, GROUP_WINDOW_DAYS)
    if not window:
        return
    stats = event_grouping.assign_groups(window)
    event_grouping.compute_heat(window)
    run_metrics['event_grouping'] = stats
    print(f"  🧩 事件归组：{stats['groups']} 组"
          f"（规则并 {stats['rule_merges']} / AI 并 {stats['ai_merges']}，AI 调用 {stats['ai_calls']}）")


def main():
    today = _cn_today()
    run_started = _cn_now()
    run_metrics = {
        'run_id': run_started.strftime('%Y%m%d-%H%M%S'),
        'date': today,
        'started_at': run_started.isoformat(),
        'environment': 'github_actions' if os.environ.get('GITHUB_ACTIONS') == 'true' else 'local',
    }
    source_funnel = {}
    ON_GHA = os.environ.get('GITHUB_ACTIONS') == 'true'
    if ON_GHA:
        print("  🤖 GHA 环境检测：DeepSeek 为主，失败后自动用豆包兜底")
    print(f"\n🌍 全球互联网动态情报站")
    print(f"   {_cn_now().strftime('%Y-%m-%d %H:%M')} | 目标：融资/并购/财报/战略\n")

    os.makedirs(DATA_DIR, exist_ok=True)
    _load_fact_ledger()
    try:
        with open(data_path('events.json'), 'r', encoding='utf-8') as f:
            all_events = json.load(f)
        if isinstance(all_events, list): all_events = {}
    except: all_events = {}

    # 采集（并行优化）
    _clear_old_cache()  # 清理旧缓存，确保���次都真实抓取
    registry_rss_sources, registry_html_sources = load_registry_sources()
    effective_rss_sources = RSS_SOURCES + registry_rss_sources
    effective_html_sources = HTML_SOURCES + registry_html_sources
    if registry_rss_sources or registry_html_sources:
        print(f"🧭 Source Registry 启用：RSS {len(registry_rss_sources)} 个 | HTML {len(registry_html_sources)} 个")

    print("📡 采集 RSS 信源（并行）...")
    t0 = time.time()

    # Step 1: 并行抓取所有 RSS 源文本
    rss_urls = [cfg['url'] for cfg in effective_rss_sources]
    fetched = asyncio.run(fetch_all_parallel(rss_urls))

    # Step 2: 解析每个返回的文本
    raw = []
    cache_hits = sum(1 for _, (_, cached) in fetched.items() if cached)
    source_stats = {}  # human-readable source stats for logs
    source_metrics = {}
    for cfg in effective_rss_sources:
        body, cached = fetched.get(cfg['url'], (None, False))
        if not body:
            print(f"  ✗ [{cfg['name']}] 失败（{cfg['region']}）")
            source_stats[cfg['name']] = '✗'
            source_metrics[cfg['name']] = {
                'method': 'rss',
                'region': cfg.get('region', ''),
                'status': 'failed',
                'fetch_status': 'failed',
                'count': 0,
                'signal_count': 0,
                'cached': cached,
            }
            continue
        cfg_copy = cfg.copy()
        items = _parse_rss_text(cfg_copy, body)
        scope_stats = cfg_copy.get('_scope_stats') or {}
        mark = "📦" if cached else "🌐"
        sig = _qualified_signal_count(items)
        print(f"  {mark} [{cfg['name']}] {len(items)} 条（信号{sig} | {cfg['region']}）")
        source_stats[cfg['name']] = f'{len(items)} 条'
        source_metrics[cfg['name']] = {
            'method': 'rss',
            'region': cfg.get('region', ''),
            'status': 'ok',
            'fetch_status': 'success',
            'count': len(items),
            'signal_count': sig,
            'cached': cached,
            'scope_stats': scope_stats,
        }
        raw.extend(items)

    print(f"\n  ⏱  采集耗时 {time.time()-t0:.1f}s | 缓存命中 {cache_hits}/{len(rss_urls)}")
    print(f"  📊 信源统计（{len(raw)} 条）：{' | '.join(f'{k}: {v}' for k, v in source_stats.items() if v != '✗')}")
    run_metrics['rss'] = {
        'source_count': len(effective_rss_sources),
        'raw_count': len(raw),
        'cache_hits': cache_hits,
        'source_stats': source_metrics,
    }

    # HTML 备用采集（降级方案）
    if effective_html_sources:
        print("\n🌐 HTML 降级采集...")
        html_source_metrics = {}
        html_raw_count = 0
        for cfg in effective_html_sources:
            _apply_company_scope_contract(cfg)
            items = fetch_html(cfg)
            for item in items:
                apply_scope_contract(item)
            sig = _qualified_signal_count(items)
            if items:
                print(f"  ⚡ [{cfg['name']}] {len(items)} 条（信号{sig} | {cfg.get('region', '未知')}）")
            else:
                print(f"  – [{cfg['name']}] 无内容")
            html_source_metrics[cfg['name']] = {
                'method': 'html',
                'region': cfg.get('region', ''),
                'status': 'ok' if items else ('failed' if cfg.get('_last_fetch_status') == 'failed' else 'empty'),
                'fetch_status': cfg.get('_last_fetch_status') or 'unknown',
                'count': len(items),
                'signal_count': sig,
            }
            html_raw_count += len(items)
            raw.extend(items)
            time.sleep(REQUEST_DELAY)
        run_metrics['html'] = {
            'source_count': len(effective_html_sources),
            'raw_count': html_raw_count,
            'source_stats': html_source_metrics,
        }
    else:
        run_metrics['html'] = {
            'source_count': 0,
            'raw_count': 0,
            'source_stats': {},
        }

    # 27家公司监控（限当天/昨日，每公司最多3条）
    print("\n🏢 采集公司动态（限当天/昨日，每公司最多3条）...")
    t1 = time.time()
    company_raw = []
    company_source_metrics = {}
    for cfg in COMPANY_SOURCES:
        items = fetch_company_news(cfg)
        for item in items:
            apply_scope_contract(item)
        sig = _qualified_signal_count(items)
        if items:
            print(f"  🌐 [{cfg['name']}] {len(items)} 条（信号{sig}）")
        else:
            print(f"  – [{cfg['name']}] 无今日动态")
        company_source_metrics[cfg['name']] = {
            'method': 'company',
            'region': cfg.get('region', ''),
            'status': 'ok' if items else ('failed' if cfg.get('_last_fetch_status') == 'failed' else 'empty'),
            'fetch_status': cfg.get('_last_fetch_status') or 'unknown',
            'count': len(items),
            'signal_count': sig,
        }
        company_raw.extend(items)
        time.sleep(0.5)  # 避免请求过快

    company_unique = company_raw  # fetch_company_news 内部已去重
    print(f"  ⏱  公司采集耗时 {time.time()-t1:.1f}s | {len(company_unique)} 条")
    run_metrics['company'] = {
        'source_count': len(COMPANY_SOURCES),
        'raw_count': len(company_raw),
        'unique_count': len(company_unique),
        'source_stats': company_source_metrics,
    }

    try:
        from job_observation import (
            collect_job_observations,
            write_job_observation_metrics,
            write_job_snapshots,
            write_signal_candidates,
        )
    except ImportError:
        from scripts.job_observation import (
            collect_job_observations,
            write_job_observation_metrics,
            write_job_snapshots,
            write_signal_candidates,
        )
    jobs_metrics, job_snapshots, signal_candidates = collect_job_observations(observed_at=run_started.isoformat())
    promoted_job_events = jobs_metrics.pop('promoted_events', [])
    jobs_metrics['promoted_count'] = len(promoted_job_events)
    write_job_snapshots(job_snapshots)
    write_job_observation_metrics(jobs_metrics)
    write_signal_candidates(signal_candidates)
    run_metrics['jobs'] = jobs_metrics
    print(
        f"  🧑‍💻 Jobs 快照：{jobs_metrics['source_count']} 个对象 | "
        f"{jobs_metrics['raw_count']} 个职位 | "
        f"{len(jobs_metrics['candidate_signals'])} 个结构变化候选"
    )

    # 合并：公司新闻 + 通用新闻，按事件级指纹去重
    all_raw = company_unique + raw
    _merge_source_funnel(source_funnel, _source_funnel_stage(all_raw, 'raw'))
    unique = []
    for it in all_raw:
        if any(_is_same_event(it, existing) for existing in unique):
            continue
        unique.append(it)
    same_run_duplicate_skipped = len(all_raw) - len(unique)
    _merge_source_funnel(source_funnel, _source_funnel_stage(unique, 'unique'))

    # 统计（event_type 会随 AI 输出出现 industry_report/model_release 等扩展类型，用 get 容错）
    types = {}
    for it in unique:
        t = (it.get('event_types') or ['other'])[0]
        types[t] = types.get(t, 0) + 1
    regions = {}
    for it in unique: regions[it['region']] = regions.get(it['region'],0) + 1
    company_count = sum(1 for it in unique if it.get('is_company'))

    print(f"\n📊 采集：{len(unique)} 条（融资{types.get('funding',0)} | 并购{types.get('ma',0)} | 财报{types.get('earnings',0)} | 战略{types.get('strategy',0)} | 其他{types.get('other',0)}）")
    print(f"   区域：{regions} | 公司动态：{company_count} 条")
    run_metrics['collection'] = {
        'raw_count': len(all_raw),
        'unique_count': len(unique),
        'same_run_duplicate_skipped': same_run_duplicate_skipped,
        'company_count': company_count,
        'type_counts': types.copy(),
        'region_counts': regions.copy(),
    }

    # 范围准入先于价值评分。候选仅留在运行指标中，不送 AI、不入事件库。
    scope_qualified, scope_candidates, scope_filtered = [], [], []
    for item in unique:
        apply_scope_contract(item)
        if item.get('scope_status') == 'qualified':
            if (item.get('event_types') or ['other'])[0] == 'other':
                item['event_types'] = ['strategy']
            scope_qualified.append(item)
        elif item.get('scope_status') == 'candidate':
            scope_candidates.append(item)
        else:
            scope_filtered.append(item)
    _merge_source_funnel(source_funnel, _source_funnel_stage(scope_qualified, 'scope_qualified'))
    _merge_source_funnel(source_funnel, _source_funnel_stage(scope_candidates, 'scope_candidate'))
    _merge_source_funnel(source_funnel, _source_funnel_stage(scope_filtered, 'scope_filtered'))

    # 传统银行主体过滤：Fintech 源编辑视野含整个金融业，排除商业银行事件
    bank_filtered = []
    kept_after_bank = []
    for it in scope_qualified:
        if _is_traditional_bank_item(it):
            bank_filtered.append(it)
        else:
            kept_after_bank.append(it)
    scope_qualified = kept_after_bank
    _merge_source_funnel(source_funnel, _source_funnel_stage(bank_filtered, 'bank_filtered'))
    run_metrics['bank_filtered_count'] = len(bank_filtered)

    # 智能过滤（公司新闻单独处理，不做 smart_filter）
    filtered = smart_filter(scope_qualified)
    _merge_source_funnel(source_funnel, _source_funnel_stage(filtered, 'smart_kept'))
    smart_filtered_count = len(filtered)
    types2 = {}
    for it in filtered:
        t = (it.get('event_types') or ['other'])[0]
        types2[t] = types2.get(t, 0) + 1
    print(f"   过滤后：{len(filtered)} 条（融资{types2.get('funding',0)} | 并购{types2.get('ma',0)} | 财报{types2.get('earnings',0)} | 战略{types2.get('strategy',0)} | 其他{types2.get('other',0)}）")
    run_metrics['filtering'] = {
        'smart_filtered_count': smart_filtered_count,
        'smart_filter_dropped': len(scope_qualified) - smart_filtered_count,
        'scope_qualified_count': len(scope_qualified),
        'scope_candidate_count': len(scope_candidates),
        'scope_filtered_count': len(scope_filtered),
        'scope_reason_counts': {
            reason: sum(1 for item in scope_candidates + scope_filtered if item.get('scope_reason') == reason)
            for reason in sorted({item.get('scope_reason') for item in scope_candidates + scope_filtered if item.get('scope_reason')})
        },
        'ai_filtered_count': smart_filtered_count,
        'ai_filter_dropped': 0,
        'type_counts_after_smart_filter': types2.copy(),
    }

    # AI 情报价值评分：对 other 类事件豆包评分，过滤低价值
    if any((it.get('event_types') or ['other'])[0] == 'other' and not it.get('is_company') for it in filtered):
        before_ai_filter = len(filtered)
        filtered = ai_quality_judge(filtered)
        _merge_source_funnel(source_funnel, _source_funnel_stage(filtered, 'ai_quality_kept'))
        print(f"   AI评分过滤后：{len(filtered)} 条")
        run_metrics['filtering']['ai_filtered_count'] = len(filtered)
        run_metrics['filtering']['ai_filter_dropped'] = before_ai_filter - len(filtered)

    # 评分前置：每个事件程序评分，分层决定是否送 AI
    print(f"\n  📊 评分前置，分层处理...")
    for it in filtered:
        it['_prescore'] = _calc_score(it)

    # 分层：所有合格事件一律先送 AI 分析（2026-08-13 用户决策），
    # 丢弃仍按分数（<4 且非公司视为无价值边缘事件，不送 AI 省成本）。
    ai_tier, prog_tier = [], []
    drop_count = 0
    for it in filtered:
        score = it['_prescore']
        if score < 4 and not it.get('is_company'):
            drop_count += 1
        else:
            ai_tier.append(it)
    _merge_source_funnel(source_funnel, _source_funnel_stage(ai_tier, 'score_ai_tier'))
    kept_score_ids = {id(it) for it in ai_tier}
    dropped_items = [it for it in filtered if id(it) not in kept_score_ids]
    _merge_source_funnel(source_funnel, _source_funnel_stage(dropped_items, 'score_dropped'))

    print(f"    AI深度分析：{len(ai_tier)} 条 | 丢弃：{drop_count} 条")
    run_metrics['scoring'] = {
        'ai_tier_count': len(ai_tier),
        'program_tier_count': 0,
        'dropped_count': drop_count,
    }

    # AI深度分析（所有合格事件先送 AI，失败才程序兜底）
    today_events = []
    if ai_tier:
        fill_event_images(ai_tier)
        use_ark = configure_ark()
        ark_dead = not use_ark
        use_deepseek = configure_deepseek()
        deepseek_dead = not use_deepseek
        use_doubao = False

        for i in range(0, len(ai_tier), 8):
            batch = ai_tier[i:i+8]
            results = None
            result_source = None
            batch_idx = (i // 8) + 1
            total_batches = (len(ai_tier) + 7) // 8

            # 方舟 V4 Flash 主力（降本 6 倍）；连续失败后降级 DeepSeek → 豆包
            if not ark_dead:
                results = analyze_events_ark(batch)
                if results is None:
                    print(f"  批次 {batch_idx}/{total_batches} 方舟失败→降级...")
                    ark_dead = True
                else:
                    result_source = 'ark'
                    print(f"  批次 {batch_idx}/{total_batches} 方舟 ✅")

            # DeepSeek 二级；连续失败后本轮后续批次直接走豆包兜底
            if results is None and not deepseek_dead:
                results = analyze_events_deepseek(batch)
                if results is None:
                    print(f"  批次 {batch_idx}/{total_batches} DeepSeek 失败→降级...")
                    deepseek_dead = True
                else:
                    result_source = 'deepseek'
                    print(f"  批次 {batch_idx}/{total_batches} DeepSeek ✅")

            # 豆包兜底
            if results is None:
                if not use_doubao:
                    use_doubao = configure_doubao()
                if use_doubao:
                    results = analyze_events_doubao(batch)
                    if results:
                        result_source = 'doubao'
                        print(f"  批次 {batch_idx}/{total_batches} 豆包 ✅")
                    else:
                        # 批量失败后逐条兜底（应对间歇性超时）
                        print(f"  批次 {batch_idx}/{total_batches} 豆包批量失败→逐条兜底...")
                        results = []
                        for item in batch:
                            single = analyze_single_event_doubao(item)
                            if single:
                                results.extend(single)
                        if results:
                            result_source = 'doubao'
                            print(f"  逐条兜底成功：{len(results)}/{len(batch)} 条 ✅")
                        else:
                            print(f"  逐条兜底全部失败，程序生成")

            # 构建事件（有AI结果则合并，否则程序生成）
            if results:
                result_map = _results_by_url(results)
                for item in batch:
                    r = result_map.get(item['url'])
                    if r:
                        ev = build_event(
                            item,
                            r,
                            analysis_source=result_source or 'ai',
                        )
                        _apply_fact_score_rules(ev)
                        today_events.append(ev)
                    else:
                        today_events.append(
                            build_event(
                                item,
                                analysis_source='program',
                                analysis_status='failed',
                            )
                        )
            else:
                for item in batch:
                    today_events.append(
                        build_event(
                            item,
                            analysis_source='program',
                            analysis_status='failed',
                        )
                    )
            time.sleep(0.5)

    today_events.extend(prepare_event_contract(event) for event in promoted_job_events)

    # AI标题改写：对程序层中仍为泛化描述的事件用豆包改写
    rewrite_titles_for_display(today_events)
    _merge_source_funnel(source_funnel, _source_funnel_stage(today_events, 'analysis_events'))
    for event in today_events:
        annotate_event_quality(event)
    # 重新冻结展示资格：annotate 可能把 needs_repair 从 False 翻 True，
    # 不重冻结会让 needs_repair=True 的事件仍以 view_status='main' 混入日报
    for event in today_events:
        prepare_event_contract(event)

    q = summarize_quality(today_events)
    if q['total']:
        print(
            f"  🧪 分析质量：需修复 {q['needs_repair']}/{q['total']} 条"
            f"（高分需修复 {q['high_score_needs_repair']} 条，"
            f"兜底/失败 {q['fallback_or_failed']} 条）"
        )

    # 按文章实际发布日期分组（而非脚本运行时间）
    # 同一批次抓到的文章可能有不同的发布日期
    # 全局去重：按事件级指纹 + URL 双重控制，避免多次运行重复追加
    existing_events = [e for events in all_events.values() for e in events]
    pubdate_ok, pubdate_fallback = 0, 0
    added_events = []
    for event in today_events:
        apply_event_date_metadata(event, fallback_observed_at=run_started)
        dup = next((e for e in existing_events
                    if (event.get('url') and e.get('url') == event['url']) or _is_same_event(event, e)), None)
        if dup is not None:
            # 跨批次去重：重复事件按信息完整度合并，新报道更具体时升级已入库事件
            if _is_more_complete(event, dup):
                _upgrade_event(dup, event)
            continue
        existing_events.append(event)
        date_key = event.get('date') or today
        if event.get('published_at'):
            pubdate_ok += 1
            all_events.setdefault(date_key, []).append(event)
        else:
            pubdate_fallback += 1
            all_events.setdefault(date_key, []).append(event)
        added_events.append(event)
    # 确保今日槽位存在（即使 0 条也记录空日期，保持历史完整性）
    all_events.setdefault(today, [])

    # 输出统计
    company_added = sum(1 for e in added_events if e.get('is_company'))
    _merge_source_funnel(source_funnel, _source_funnel_stage(added_events, 'added'))
    added_event_dates = {}
    added_source_tiers = {}
    for event in added_events:
        event_date = (event.get('date') or today)[:10]
        source_tier = event.get('source_tier') or '未标注'
        added_event_dates[event_date] = added_event_dates.get(event_date, 0) + 1
        added_source_tiers[source_tier] = added_source_tiers.get(source_tier, 0) + 1
    print(f"  📅 pubDate 解析：{pubdate_ok} 条有日期 | {pubdate_fallback} 条无日期（归入今日）")
    print(f"  🏢 新增公司动态：{company_added} 条 | 新增通用热点：{len(added_events) - company_added} 条")
    print(f"  🚫 历史重复跳过：{len(today_events) - len(added_events)} 条")
    run_metrics['analysis'] = {
        'event_count': len(today_events),
        'quality': q,
    }
    run_metrics['storage'] = {
        'added_count': len(added_events),
        'duplicate_skipped': len(today_events) - len(added_events),
        'company_added': company_added,
        'generic_added': len(added_events) - company_added,
        'pubdate_ok': pubdate_ok,
        'pubdate_fallback': pubdate_fallback,
        'added_event_dates': added_event_dates,
        'added_source_tiers': added_source_tiers,
    }
    run_metrics['source_funnel'] = source_funnel

    # 事件库不再按时间裁剪；首页、周报和月报分别使用展示窗口。
    all_events = apply_event_storage_policy(all_events)
    all_events, removed_dups, removed_reasons = dedupe_events_by_day(all_events)
    if removed_dups:
        print(f"  🧹 历史去重：清理 {removed_dups} 条同日重复事件")

    # P2/P3：先双评分（并排落库），再归组与热度（热度按归组后的组算）。
    # 两者都有安全阀，关掉即完全不改 all_events。
    _run_ai_scoring(all_events, run_metrics)
    _run_event_grouping(all_events, run_metrics)

    with open(data_path('events.json'), 'w', encoding='utf-8') as f:
        json.dump(all_events, f, ensure_ascii=False, indent=2)
    _save_fact_ledger()
    # _fact_ledger 由 _load_fact_ledger() 重新绑定，必须读模块内的实时值，
    # 不能用转发层那份 import 时快照（见上方 dedupe re-export 说明）。
    print(f"  📒 事实评分账本：{len(dedupe._fact_ledger)} 个指纹")
    run_metrics['finished_at'] = _cn_now().isoformat()
    run_metrics['history'] = {
        'total_events': sum(len(v) for v in all_events.values()),
        'company_total': sum(1 for v in all_events.values() for e in v if e.get('is_company')),
        'day_count': len(all_events),
        'removed_same_day_duplicates': removed_dups,
        'removed_reasons': removed_reasons,
    }
    metrics_path = write_run_metrics(run_metrics)
    print(f"  🧾 Run metrics：{metrics_path}")
    try:
        from entity_observation_ledger import build_entity_observation_ledger, write_entity_observation_ledger
    except ImportError:
        from scripts.entity_observation_ledger import build_entity_observation_ledger, write_entity_observation_ledger
    ledger = build_entity_observation_ledger(as_of=today)
    ledger_path = write_entity_observation_ledger(ledger)
    print(f"  🧭 观察点账本：{ledger_path} | {ledger['status_counts']}")

    # 输出每个日期的分桶统计
    for date_key in sorted(all_events.keys(), reverse=True):
        events = all_events[date_key]
        regions = {}
        company_n = 0
        for e in events:
            regions[e['region']] = regions.get(e['region'], 0) + 1
            if e.get('is_company'): company_n += 1
        print(f"  ✅ {date_key}：{len(events)} 条（公司{company_n}）| 区域：{regions}")
    total = sum(len(v) for v in all_events.values())
    company_total = sum(1 for v in all_events.values() for e in v if e.get('is_company'))
    print(f"\n  共 {total} 条历史事件（公司 {company_total} 条），跨 {len(all_events)} 天）")

    # P0 Agent：每日AI趋势分析（生成2-4句专业判断）
    summary_groups = {}
    for event in added_events:
        summary_date = (event.get('date') or today)[:10]
        if event in all_events.get(summary_date, []):
            summary_groups.setdefault(summary_date, []).append(event)
    if summary_groups:
        for summary_date, summary_events in sorted(summary_groups.items()):
            build_daily_ai_summary(summary_events, summary_date)
    else:
        print("  📊 今日无新增入库事件，跳过 AI 趋势分析")

if __name__ == '__main__':
    main()
