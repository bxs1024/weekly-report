# 去重改 AI 事件指纹（V5.2）

> 2026-08-24 实施 · commit `249e0c2`（未推送，与后续改动一并）。承接 `2026-08-20-event-dedup-label-scope-fix.md` 的"身份卡 + AI 判重"方向，落地其中去重部分。

## 问题

去重修了好几轮仍反复漏。2026-08-22 日报 feed 实测：Starcloud 融资 2.5 亿美元被 Ventureburn（非洲）和 Tech in Asia（北美）各报一篇，两条都进 feed 没合并。

根因（三层全断，均用真实数据实跑确认）：
1. **入库层** `fetch_news.py:_is_same_event`：两条主体、类型、融资锚点全对上，但标题相似度 0.364 < 0.4 弱信号守卫 → 拒绝合并。
2. **展示层** `generate_html.py:dedupe_display_events`：`_display_subject_key` 对 "US space data center startup Starcloud raises $250m" 错提取主体 "us space data center"（修饰语当主体），另一条提取 "starcloud" → subject_key 不一致 → 不进合并分支。
3. **共性根因**：两层都依赖"正则提主体 + 标题相似度阈值"。新闻表达方式无限（raises $250m / secures $250 Million / 2.5 亿美元融资），正则和阈值永远有缝。每修一轮 = 补一类表达的词表，打地鼠。

## 方案

管线已有 AI（DeepSeek 主力 → 豆包降级）在分析每条事件。让 AI 认"同一件事"，规则只做机械归一化：

1. **AI 输出指纹字段**（`AI_SYSTEM_PROMPT` / `AI_EXAMPLES`）：每条事件新增
   - `canonical_company`：主体规范名（去修饰语、统一大小写；行业报告等无主体填空串）
   - `canonical_key`：量化锚点（融资/财报金额统一 m 单位如 250m；并购填被收购方；合作填对象；裁员填人数；无锚点填空串）
2. **入库层** `_is_same_event` 新增 `_fingerprint_match`：canonical_company + 主类型 + canonical_key 三项全匹配 → 合并。
3. **展示层** `dedupe_display_events` 加同指纹分支兜底（绕过正则主体提取与标题相似度）。
4. **归一化** `_normalize_canonical_key`：金额样式归一（`$250M`/`250 Million`/`2.5亿美元` → `250m`；`$2.8B` → `2.8b`）。
5. **测试** `scripts/test_event_fingerprint.py`：Starcloud 案例、金额归一、防误并、存量降级。

## 防误并设计

三项全匹配才合并：
- 同名公司同日不同额度融资（250m vs 500m）→ canonical_key 不同 → 不并
- 同公司财报日发游戏新闻（earnings vs strategy）→ 类型不同 → 不并
- 存量事件无指纹字段 → 自动降级旧规则，零影响

## 不做

- 不改存量 3000+ 事件、不做历史回填（旧路径兼容）
- 不删旧相似度/锚点规则（并存作降级安全网）
- 不碰 workflow / schema 结构（只加字段）
