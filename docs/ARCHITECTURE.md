# 架构契约

> 本项目的不变规则与目标结构。对标 AIHOT 开源框架（见 `docs/plans/2026-10-06-aihot-methodology-refactor.md`），移植其工程规则，保留本站产品资产。新增代码与重构都以此为准；调整规则时先改本文件，再改代码。

## 一句话架构

采集信源 → 入库判重 → AI 分析（结构化 + 双评分 + 写作）→ 事件归组与热度 → 公开读取层 → 日报/周报/月报/公司索引/RSS/Agent 出口。

## 不变规则

| 规则 | 说明 | 落地位置 |
|---|---|---|
| **页面不调模型** | 读者打开页面只读已落库结果；模型只在采集/生成任务里调用 | `generate_html.py` 不得 import AI 通道 |
| **一个公开读取层** | 首页、RSS、复核区、公司索引、周期报告只从 `view_selectors` / `publication` 取数，资格判断不在渲染层 | `scripts/view_selectors.py`、`view_selectors.py` 的 selector 入口 |
| **付费请求有回执** | 每次 AI 调用先记回执（输入指纹 + prompt 版本 + 模型），重试复用已付过钱的结果 | `scripts/ai_receipts.py`（P2 新增），现有 `editorial_cache.json` 为同类机制 |
| **提示词改标准不改代码** | 全部 AI 提示词放 `scripts/prompts/*.md`；版本 = 文件内容 sha256；缓存键含版本 | `scripts/prompt_loader.py`（P1 新增） |
| **评分只排序不过滤** | 资格由四道闸门（观察范围/互联网相关度/事件价值/行动性）决定；分数只影响顺序 | `scope_gate.py`、`internet_relevance.py`、`event_value.py` |
| **闸门先于评分** | 事件先过闸门拿资格，再参与评分排序；高分不救边界外内容 | `event_contract.py::apply_view_contract()` |
| **资格冻结一次** | `view_status`/`view_reason`/`view_priority` 由契约层统一生成，页面、RSS、报表消费同一结果 | `event_contract.py` |
| **安全阀只关不绕** | `AI_CALLS_ENABLED`、`AI_SCORE_ENABLED`、`GROUP_ENABLED` 只决定发不发出去，不改变逻辑分支；默认开，出问题可单独关停新链路 | 各新模块入口 |
| **热度锚点来自数据** | 事件热度的「现在」取窗口内最新事件日期（`_data_now`），不取机器时钟。数据停更后机器时钟跑在前面，所有来源都会超出 48h 窗口、热度集体归零；且同一份数据今天跑和明天跑结果不同，无法复现、无法做回归比对 | `fetch_news.py::_data_now()` → `event_grouping.compute_heat(now=…)` |
| **信源口径** | 「谁发的稿」= `publisher or source`：`source` 常是聚合器（`Google News`），真实媒体落在 `publisher`；`origin_source_id` 是**被报道的公司**（Adyen 这类），不是信源，计入会让每条公司新闻凭空多一家来源 | `event_grouping.py::_event_sources()`、`content/filter.py` 合并记录 |
| **统计要区分「没跑」与「跑了没结果」** | `ai_calls` 只数真的发起的判定；没通道时整层跳过并记 `ai_channel=False`。否则 `ai_calls=N / ai_merges=0` 会把排查引向「提示词不行」，真实原因却是 key 没配或总闸被关 | `event_grouping.py::assign_groups()` |
| **覆盖面角标按实际证据说话** | 首页事件卡与公司索引的「N 家来源 / N 篇报道」：优先独立来源数，信源名缺失时退回报道篇数（存量数据只有 `merged_from` 的 URL）。两种都只表示覆盖面，不夸大 | `publication/display.py::heat_label()` |
| **数据先落库，渲染后提交** | 采集数据（`data/`）与生成页面（`docs/`）分属两个 job：采集 job 一结束就提交数据，渲染 job 再提交页面。job 级 `timeout-minutes` 到点会杀进程，但**已完成的步骤仍然生效**——所以只要提交排在渲染之前，渲染超时就只丢页面、不丢数据。旧结构把 `git add data/ docs/` 排在渲染之后，渲染一超时当天数据全丢，这才是数据断档的直接原因 | `.github/workflows/update.yml` 的 `collect` / `render` job |
| **已封档周期只读缓存** | 周报/月报一旦封档（`status == 'closed'`）就不再重算：编辑层只用缓存（旧版也接受），不发起付费调用；完全没有缓存时才退回一次 AI，避免整页生成失败。编辑层的输入指纹随事件累计持续漂移，不设这条规则会让每次渲染都为 18 个历史周 + 5 个历史月重发请求 | `reports/period.py::build_period_report()` → `editorial/editorial.py` 的 `allow_ai` 参数 |
| **展示层判重按日期分桶** | `dedupe_display_events` 是 O(n²) 热点，按「日期 ±7 天」（`_SAME_EVENT_MAX_WINDOW_DAYS`，取自 `_dates_adjacent` 的最长窗口）分桶。但 `_is_same_event` 里有两条判同路径**不看日期**——URL 相等、`_fingerprint_match`（只比 `canonical_company` + `canonical_key`），另有空日期走 `_dates_adjacent` 的 True 分支；这三类必须分别用 url 索引 / company 索引 / undated 集合显式补上，否则分桶会漏并。候选按 kept 下标升序回放，保证与全量扫描结果逐条一致 | `publication/display_dedupe.py`，回归见 `test_display_dedupe_window.py` |
| **渲染只装配一次** | 整站展示上下文（`build_display_context`）在一次渲染里只跑一遍：HTML 生成后把 RSS 需要的字段落到 `data/.cache/rss_context.json` 并附 `events.json` 指纹，`generate_feed.py` 指纹相符就直接复用，否则重算。RSS 原先自己再跑一遍装配，等于每次渲染付两遍 | `generate_html.py::dump_rss_context()` → `generate_feed.py::_load_cached_display_context()` |
| **来源可追溯** | 每条事件保留原始链接、来源层级、`source_url_original/repaired/reason`；不猜测替换 | `fetch_news.py` 链接修复逻辑 |
| **旧文不刷屏** | 已发布超 48 小时的存量按原文时间归档，不进今日批次 | `select_mature_main_date()` |
| **迁移不可改写** | 已落库数据结构的语义变更必须向后兼容（存量事件无新字段时走旧路径） | 全项目 |
| **不提交密钥与缓存** | `.env`、`data/.cache/`、`.data/`、`data/ai_receipts.json`、`__pycache__` 不入库 | `.gitignore` |
| **路径锚定仓库根** | 读写 `data/` 等仓库内路径必须基于 `__file__` 推导，禁止用相对路径（相对路径随调用进程 CWD 漂移，会把同一份状态写成两份）。统一从 `repo_paths.py` 取 `REPO_ROOT`/`DATA_DIR`/`DOCS_DIR`/`repo_path()`/`data_path()`/`docs_path()`，不要各文件重复推导 `_REPO_ROOT` | `repo_paths.py` 唯一入口 |
| ↳ 豁免 | 一次性/离线诊断脚本（`cleanup_2026-*.py`、`verify_signal_score.py`、`audit_scope_misfits.py`、`calibrate_weights.py`）保持裸相对路径，约定只在仓库根手动执行 | 上述文件 |
| ↳ `git_ref` 例外 | 经 `git show <ref>:<path>` 读取时必须用仓库相对路径，此时不要传绝对路径 | `source_health_report.py`、`source_conversion_report.py` 的 `_load_json` |

## 目标目录结构

```text
scripts/
  repo_paths.py # 仓库内路径的唯一锚点（REPO_ROOT/DATA_DIR/DOCS_DIR/repo_path/data_path/docs_path）
  sources/      # 信源读取：HTTP 底座、RSS/HTML/官方源采集、公司观察契约、信源元数据
  content/      # 内容处理：通用工具、判型、判重与事实评分账本、智能过滤、og:image 补抓
  editorial/    # 编辑部：agents（改写/摘要/裁判/BD 上下文）+ editorial（周月报成稿与缓存）
  providers/    # AI 通道：llm（四家供应商、结构化分析、共享会话）
  publication/  # 渲染出版域：score/entity/loaders/bd/display_dedupe/review/
                #            display/opportunity/clusters/summary/date_panel/context
  reports/      # 周期报告：period.py（AIHOT 档案、周月归档、周期报告构建）
  prompts/      # 全部 AI 提示词（.md，版本 = 内容 sha256）

  fetch_news.py     # P4 后只剩 main 入口 + re-export 转发层（4221 → 899 行）
  generate_html.py  # 同上（2903 → 607 行）
```

迁移纪律：P4 拆包**行为不变**，硬验收三条——
① `_api_snapshot.py --verify --mod <模块>`：顶层名字的源码/取值哈希逐项一致；
② `verify_render_output.py --check`：离线渲染与基线逐字节一致；
③ `run_all_tests.sh`：全绿。任一不满足不合并。

### re-export 转发层的四条铁律（P4 血泪）

`fetch_news.py` / `generate_html.py` 保留 re-export 给外部脚本兜底，但
`from X import f` 是**值绑定**，不是别名。凡是下面四类，转发层一律失效：

1. **会重新绑定的模块级状态**：`_load_fact_ledger()` 用 `global` 重绑
   `_fact_ledger`，转发层那份只是 import 时的空 dict 快照。读它必须走
   `content.dedupe._fact_ledger`。
2. **被 monkeypatch 的函数**：测试/脚本打补丁**必须打在真实模块**上。
   打在转发层上会静默失效——测试仍然 PASS，但实际跑的是真实逻辑。
3. **函数体内的延迟 import**：`from fetch_news import _post_chat` 每次调用
   都重新取值，patch `providers.llm._post_chat` 对它无效。调用方一律改模块
   引用：`from providers import llm` + `llm._post_chat(...)`。
4. **隐式副作用依赖**：搬出前调用方靠 `import fetch_news` 顺带触发
   `load_dotenv()`，AI 通道才拿到 key。依赖一断，`_chat_api_candidates()`
   返回空，AI 分支整段静默跳过。模块自己要加载自己需要的环境。

**验证补丁是否真的命中**：把被 patch 的函数改成抛异常跑一遍，确认真的抛了。
看测试是否 PASS 不算数——失效的补丁也会 PASS。

## 六步流水线（目标态）

| 步骤 | 输入 | 输出 | 提示词 |
|---|---|---|---|
| 采集 | 信源 | 原始条目 | — |
| 预筛 | 原始条目 | PASS/BLOCK/UNKNOWN | `prefilter.md`（AI 层兜底；规则闸门仍为主） |
| 评分 | 资料 | 0-100 ×2 → 均值 | `score.md` |
| 结构化 | 资料 | 事件类型/主体/动作/领域/锚点 | `structure.md` |
| 写作 | 资料 | 中文标题/概要/点评/影响 | `understand.md` |
| 归组 | 入库事件 | SAME_OCCURRENCE/SAME_STORY/UNRELATED/ROUNDUP + 事件热度 | `group-pair.md`、`group-definitions.md` |

### 归组规则层为什么比同日去重更严

同日去重（`content/classify.py::_fingerprint_match`）有一条**锚点缺失放宽**：
`canonical_key` 为空时，只要主体相同 + 主类型相同 + 标题相似度 ≥0.42 就判同。

归组规则层（`event_grouping.py::rule_merge`）**刻意不要这条放宽**，只认完整指纹
（`canonical_company` + `canonical_key` 都非空且相等）。差别在作用范围：去重只在
**同一天**内比，放宽的误并风险被日期天然兜住；归组要跨 30 天比，同样的放宽会把
同公司不同时间的两件事（如两次独立融资）并成一件。跨日的「同事件不同阶段」是
AI 层 `SAME_STORY` 的职责，规则层只做保守预筛。

实测：存量 30 天窗口 1235 条事件里 `canonical_key` 填充率仅 4.6%，规则层合并 0 条
（放宽后也只多 1 条）——这不是 bug，而是同日报的多来源报道已被去重层先合并掉了。
所以归组的产出主要来自 AI 层，规则层只兜底。

## 四道闸门（本站独有，AIHOT 无对应物，全部保留）

1. **观察范围** `scope_gate.py` — 是否在对象池/信源契约范围内
2. **互联网相关度** `internet_relevance.py` — core/adjacent/edge/out，主展示要求 ≥2
3. **事件价值** `event_value.py` + `analysis_quality.py` — 证据可信度与文案质量
4. **行动性** `bd_priority` — 预算、扩张、采购、生态合作、区域进入

## 边界检查

`scripts/test_architecture.py` 强制以下边界，违反即测试失败：

- 渲染层（`generate_html.py`）不得 import AI/网络通道模块
- 选择器层（`view_selectors.py`）不得调用模型
- 采集层（`fetch_news.py`）不得 import 渲染层
- 新增模块必须落在上表目标目录内

调整边界时，先改本文件与 `test_architecture.py`，再改代码。
