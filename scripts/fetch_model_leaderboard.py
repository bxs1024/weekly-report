#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取 AIHOT 大模型排行榜 → data/model_leaderboard.json

用法:
    python scripts/fetch_model_leaderboard.py

输出结构（保持展示层字段兼容）:
    data/model_leaderboard.json
    - source: 来源信息
    - ranking: 主榜（共识分排名），每条含
      rank / name / provider / release_date / completeness
      / cache_price / input_price / output_price / consensus_score / detail_url
    - official_sources: 12 家官方评测榜单明细

2026-10 源站改版适配说明
    1. 编码：源站 header 不再声明 charset，requests 默认猜 ISO-8859-1 → 中文乱码，
       显式按 utf-8 解码。
    2. 结构：主榜从 <div class="lb-row"> 改成标准 <table>（8 列），
       价格从 2 列变 3 列（缓存 / 输入 / 输出）。
    3. 官方榜单：源站框架从 Next.js 迁移到 React Router，methodology 页的
       self.__next_f RSC payload 已消失，数据改为 React Router stream 编码。
       本次抓不到时**保留上一次的 official_sources**并报警，不用空值覆盖。
    4. 失败可见：主榜抓 0 条 → 非零退出且不覆盖旧数据（见 docs/ARCHITECTURE.md 规则 B）。
"""

import json
import os
import re
import sys
from datetime import datetime

import requests
from bs4 import BeautifulSoup

LB_URL = "https://aihot.virxact.com/leaderboard"
METH_URL = "https://aihot.virxact.com/leaderboard/methodology"
BASE_URL = "https://aihot.virxact.com"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
}

MIN_EXPECTED_RANKING = 10  # 主榜低于此条数视为异常（打印警告，但不失败）

# 官方榜单来源说明（硬编码中文版；AIHOT SSR 中该字段为乱码不可用）
SOURCE_NOTES = {
    "artificial-analysis": "只使用以统一环境复跑多项当前评测形成的 Intelligence Index；价格只作背景信息。",
    "epoch-eci": "将五十多项跨时期评测拟合到同一能力尺度。",
    "livebench-general": "以持续更新、答案可验证的七类任务衡量当前模型。",
    "arena-text": "用匿名两两盲选补充客观题库无法覆盖的整体回答体验。",
    "arena-webdev": "用匿名两两盲选衡量 Web 开发实战能力。",
    "eqbench-4": "观察对话中的情绪理解；创意写作、长文和 Judgemark 不合并成一张通用票。",
    "vals-index": "综合金融与编码任务；只把 Overall 作为一张来源票。",
    "mercor-apex-agents": "衡量投行、咨询和法律长任务；配置明细保留，但成绩归到基础模型。",
    "agents-last-exam": "保存 full split 的 harness 与推理档位明细，成绩按基础模型归并。",
    "deepswe-v1-1": "在统一 mini-swe-agent harness 下比较代码模型；成绩按基础模型归并。",
    "llm2014-agentic": "保留八个任务的等级与 scaffold 明细；成绩按基础模型归并。",
    "llm2014-reasoning": "专项推理评测，成绩按基础模型归并。",
}

TOP_N = 80  # 每张官方榜单保留前 N 名


def _session():
    """忽略系统代理（环境含 SOCKS 代理配置，requests 缺少 socks 依赖），直连。"""
    s = requests.Session()
    s.trust_env = False
    return s


def _decode_response(resp):
    """按页面真实编码解码（源站 2026-10 起不再在 header 声明 charset）。"""
    ctype = (resp.headers.get('content-type') or '').lower()
    if 'charset=' in ctype:
        return resp.text
    return resp.content.decode('utf-8', errors='replace')


def _fetch(session, url):
    resp = session.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return _decode_response(resp)


def _to_float(s):
    s = (s or "").strip().replace(",", "").replace("%", "").replace("¥", "")
    try:
        return float(s)
    except ValueError:
        return None


def _fmt_score(v):
    """把原始分数格式化为展示值：整数去 .0，>=1 保留 1 位小数，<1 保留 3 位有效小数。"""
    if v is None:
        return None
    if v == int(v):
        return str(int(v))
    if v >= 1:
        return f"{v:.1f}"
    return f"{v:.3f}"


def _norm(text):
    return re.sub(r'\s+', ' ', (text or '').strip())


def _parse_ranking(html):
    """从 leaderboard 页表格提取主榜。

    新版是标准 <table>：名次 / 模型 / 上线日期 / 评测证据 / 缓存价 / 输入价 / 输出价 / 评分。
    只认「有 8 个 td」的数据行，表头行（th）自然跳过。
    """
    soup = BeautifulSoup(html, "html.parser")
    ranking = []
    for tr in soup.find_all('tr'):
        cells = tr.find_all('td')
        if len(cells) < 8:
            continue
        rank_m = re.search(r'\d+', cells[0].get_text(strip=True))
        if not rank_m:
            continue
        link = cells[1].find('a', href=True)
        strong = cells[1].find('strong')
        name = _norm(strong.get_text() if strong else '')
        if not name:
            continue
        detail = link.get('href', '') if link else ''
        if detail.startswith('/'):
            detail = BASE_URL + detail
        # 厂商：模型单元格内第一个 <small>（桌面端那份纯厂商名；第二个是移动端含日期）
        smalls = cells[1].find_all('small')
        provider = _norm(smalls[0].get_text()) if smalls else ''
        score_el = cells[7].find('strong')
        ranking.append({
            "rank": int(rank_m.group(0)),
            "name": name,
            "provider": provider,
            "release_date": _norm(cells[2].get_text()),
            "completeness": _norm(cells[3].get_text(" ")),
            "cache_price": _norm(cells[4].get_text()),
            "input_price": _norm(cells[5].get_text()),
            "output_price": _norm(cells[6].get_text()),
            "consensus_score": _to_float(score_el.get_text(strip=True)) if score_el else None,
            "detail_url": detail,
        })
    return ranking


def _extract_sources_from_stream(html):
    """从 methodology 页 React Router stream 提取 sources 数组。

    源站 2026-10 从 Next.js 迁到 React Router，数据由
    window.__reactRouterContext.streamController.enqueue("...") 承载（turbo-stream 扁平编码）。
    这里尝试还原；拿不到就抛异常，由调用方保留旧值。
    """
    blocks = re.findall(r'streamController\.enqueue\("((?:[^"\\]|\\.)*)"\)', html)
    if not blocks:
        raise ValueError("未找到 React Router stream 数据块")
    merged = "".join(json.loads('"' + b + '"') for b in blocks)
    if '"sources"' not in merged and '"leaderboard-sources"' not in merged:
        raise ValueError("stream 中未找到 sources 字段")
    raise ValueError("turbo-stream 编码解析尚未实现（保留旧数据）")


def _build_official_sources(sources):
    out = []
    for s in sources:
        models = s.get("models") or []
        default_mk = s.get("defaultMetricKey")
        metric_names = {m.get("key"): m.get("name") for m in (s.get("metrics") or [])}
        filtered = [m for m in models if m.get("metricKey") == default_mk]
        if not filtered:
            filtered = models
        filtered.sort(key=lambda m: (m.get("sourceRank") or 99999))
        top = filtered[:TOP_N]
        out.append({
            "key": s.get("key"),
            "name": s.get("name"),
            "short_name": s.get("shortName"),
            "operator": s.get("operator"),
            "note": SOURCE_NOTES.get(s.get("key"), ""),
            "fetched_at": s.get("fetchedAt", ""),
            "metric_name": metric_names.get(default_mk, ""),
            "total_count": len(filtered),
            "models": [
                {
                    "rank": m.get("sourceRank"),
                    "name": m.get("sourceModelName"),
                    "provider": m.get("provider"),
                    "score": _fmt_score(m.get("rawScore")),
                }
                for m in top
            ],
        })
    return out


def _load_existing(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def main():
    s = _session()
    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "model_leaderboard.json")
    old = _load_existing(out_path)

    lb_html = _fetch(s, LB_URL)
    ranking = _parse_ranking(lb_html)

    if not ranking:
        print("ERROR | 主榜未解析到任何条目（源站结构可能再次变化）。"
              "保留旧数据不覆盖，退出码 2。", file=sys.stderr)
        sys.exit(2)
    if len(ranking) < MIN_EXPECTED_RANKING:
        print(f"⚠️ 主榜仅 {len(ranking)} 条（预期约 30 条），源站结构可能部分变化")

    # 官方榜单：源站改版后数据改走 React Router stream，本次抓不到则保留旧值，不覆盖
    official = old.get("official_sources") or []
    try:
        meth_html = _fetch(s, METH_URL)
        sources = _extract_sources_from_stream(meth_html)
        official = _build_official_sources(sources)
    except Exception as exc:
        print(f"⚠️ 官方榜单抓取失败（{exc}），保留上一次的 {len(official)} 家数据")

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    payload = {
        "generated_at": now,
        "fetched_date": now[:10],
        "source": {
            "name": "AIHOT 大模型排行榜",
            "leaderboard_url": LB_URL,
            "methodology_url": METH_URL,
            "rules_url": BASE_URL + "/leaderboard/rules",
            "note": "汇总多家公开模型评测榜单，用统一方法计算 AIHOT 共识分。共识分来自 AIHOT 算法，本页仅复制其展示结果。",
        },
        "ranking": ranking,
        "official_sources": official,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    n_models = sum(len(o["models"]) for o in official)
    print(f"OK | 主榜 {len(ranking)} 名 | 官方榜单 {len(official)} 家 | 榜单模型 {n_models} 行 | {out_path}")


if __name__ == "__main__":
    main()
