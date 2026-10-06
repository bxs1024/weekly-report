# 情报站架构重构方案（对标 AIHOT 开源框架）

> 依据：卡兹克《开源100万月活级产品AIHOT》+ github.com/KKKKhazix/AIHOT（architecture.md / customize.md / selection.md / grouping.md / sources.md / AGENTS.md 及 industry/prompts/ 全部提示词）。基线：本仓库 2026-09-02 数据、scripts/ 约 20k 行。

## 结论（先看这里）

1. **推荐方案 B：移植 AIHOT 方法论，渐进重构现有 Python 架构；不直接换 AIHOT 框架（方案 A）**。A 需 7×24 服务器 + PostgreSQL + Docker（月成本 ¥30+，运维转移），且 AIHOT 的产品逻辑是「行业热点站」，本站是「BD 行动情报站」，观察账本、四道闸门、BD priority 在 AIHOT 无对应物，强行迁移等于换产品。
2. AIHOT 真正值钱的是**方法论与工程规则**，与语言无关，可 100% 移植：提示词外置+内容哈希版本化、AI 双评分+gold 样本校准、事件四关系归组+独立来源热度、付费回执+预算熔断、「页面不调模型」等不变规则。
3. 现状四大差距：①提示词硬编码在 `fetch_news.py:2844`（AI_SYSTEM_PROMPT/AI_EXAMPLES）；②评分是程序规则单源（signal_scoring.py），无 AI 评分、无校准闭环；③去重只做「同事件合并」（canonical 指纹），无 SAME_STORY 进展归组、无事件级热度；④AI 调用无回执，仅编辑层有缓存（editorial_cache.json）。
4. 重构分 **6 个阶段**，每阶段独立验证、独立 commit、可随时停在任一阶段。顺序：提示词外置 → 双评分+回执 → 事件归组+热度 → 巨石拆包 → 校准工具 → 出口增强。
5. 成本影响：每事件 AI 调用 1 次 → 约 3 次（结构化+写作 1 次，评分独立 2 次）；方舟 V4 Flash 下按当前日均事件量，**每日新增成本 < ¥0.2**。
6. 4 个决策点见第六节，拍板后即可开工。

## 一、为什么不直接换框架（A vs B）

| 维度 | A：直接采用 AIHOT 框架 | B：方法论重构（推荐） |
|---|---|---|
| 成本 | VPS ¥30+/月 + PostgreSQL + Docker 运维 | 维持 $0（GHA + Pages） |
| 产品定位 | 热点资讯站，观察账本/闸门/BD priority 需重写为 modules/ | 保留全部现有产品资产与 VIEW_CONTRACT |
| 数据迁移 | 3796 事件 + registry + entity_pool 需转格式 | 原地演进，无损 |
| 护栏 | 丢弃现有 30 个离线测试 | 每阶段全量测试门控 |
| 上游同步 | 可持续合并 AIHOT 更新 | 方法论一次性吸收，之后独立演进 |
| 工期 | 全量重建，长并行期 | 渐进，每阶段可用 |

A 保留为远期选项：若未来想关停 Python 站、全托管给开源框架，再评估。

## 二、六步流水线差距分析（AIHOT → 本站）

| AIHOT 环节 | AIHOT 做法 | 本站现状 | 重构动作 |
|---|---|---|---|
| 采集 | 六种信源读取器 + 后台管理 + 试抓预览 | RSS/HTML/Google News 硬编码 + registry（85 源登记未切换） | 保持现状，仅信源配置逐步 registry 化 |
| 预筛 | prefilter.md 宽召回三态 PASS/BLOCK/UNKNOWN | scope_gate + internet_relevance（规则闸门，更严） | **保留现有闸门**（比 AIHOT 更强的产品边界），预筛 prompt 仅用于 AI 层兜底 |
| 评分 | selection-score.md 五轴加权、品味规则、噪声压制，独立打两次，分级门槛 | signal_scoring.py 程序规则 + 单次 AI 输出 9 字段 | **新增 AI 双评分层**：结构化与评分拆开；评分 prompt 移植五轴框架但换成本站 BD 轴 |
| 写作 | content-understanding.md：标题/摘要/推荐理由/反幻觉约束 | 单次 AI 输出 9 字段（已含 summary/reason/impact） | 保留 9 字段契约，prompt 外置 + 增补反幻觉/答案先行规则 |
| 归组 | 四关系（SAME_OCCURRENCE/SAME_STORY/UNRELATED/ROUNDUP）+ 复核模型 + 热度按独立来源 | canonical 指纹合并（≈SAME_OCCURRENCE） | **升级为事件归组层**：四关系判断 + 事件热度（独立来源计数+时间衰减） |
| 成刊 | 日报规则化编排不调模型；周月报只写总述 | 日报已规则化；周月报编辑层有缓存 | 基本达标，借鉴「代表稿选择」与跟进门槛细化 |

## 三、目标架构与不变规则

**目录结构**（fetch_news.py 4187 行 → 分包，命名对齐 AIHOT 域概念）：

```text
scripts/
  sources/      # 信源读取与调度（自 fetch_news 拆出）
  content/      # 入库、判重、正文清洗
  editorial/    # AI 分析：prompts.py（读 prompts/）、双评分、写作
  events/       # 事件归组、四关系、热度
  publication/  # 唯一公开读取层（view_selectors 并入，scope 契约）
  reports/      # 周期报告（period_themes 并入）
  providers/    # AI 通道 + 回执 + 预算（editorial_cache 推广为通用回执）
  prompts/      # 全部 AI 提示词（md 文件，版本=内容哈希）
```

**不变规则**（写入 docs/ARCHITECTURE.md，并加架构测试强制）：
1. 页面不调模型；模型只在采集/生成任务里调用。
2. 所有公开出口（页面/RSS/未来 API）只从 publication 层读取，资格判断不在渲染层。
3. 付费 AI 请求先记回执（输入指纹+prompt 版本+模型），失败重试复用回执，不重复花钱。
4. 提示词改标准不改代码；prompt 版本=文件内容哈希，缓存键自动含版本（替代手动 EDITORIAL_PROMPT_VERSION）。
5. 评分只排序，不过滤；资格仍由四道闸门决定（本站既有原则，与 AIHOT 一致）。
6. 安全阀环境变量：`AI_CALLS_ENABLED`、`AI_SCORE_ENABLED` 等，默认开，出问题可单独关掉新链路回退旧行为。

## 四、分阶段实施（每阶段独立 commit + 全量离线测试）

| 阶段 | 内容 | 验证 | 回滚 |
|---|---|---|---|
| **P0 架构契约** | 写 docs/ARCHITECTURE.md；新增 test_architecture.py（检查模块 import 边界，对应 AIHOT tests/architecture.test.ts） | 测试通过 | 单 commit revert |
| **P1 提示词外置** | AI_SYSTEM_PROMPT/AI_EXAMPLES/编辑层 prompt 抽到 scripts/prompts/*.md；加载器 + 内容哈希版本；缓存键接哈希 | 输出与旧 prompt 逐字节等价（哈希对齐）后切换 | 保留旧常量一个过渡期 |
| **P2 AI 双评分 + 回执** | 新增 score prompt（五轴：实质份量/信息增量/证据强度/BD 行动性/区域共振，按 action_type 加权——沿用在位权重政策 25/产品 24/融资 22/财报 18/扩张 18/招聘 12 的思路重排）；同一事件独立评两次取均值；providers 回执表 data/ai_receipts.json；AI_SCORE_ENABLED 开关 | 对最近 7 天事件跑双评 vs 程序分对比报告，人工抽检 20 条；回执复用率统计 | 关开关即回退程序评分 |
| **P3 事件归组 + 热度** | 归组 prompt 移植四关系定义；SAME_STORY 进展挂同一事件（不再只靠 canonical 合并）；事件热度=48h 独立来源数（每源一次）+ 24h 减半；首页/公司索引加热度信号 | test_event_grouping.py 用历史已知案例（Starcloud/Navi/Kakao）做回归 | 归组开关关闭走旧指纹路径 |
| **P4 巨石拆包** | fetch_news.py → sources/content/editorial/providers 分文件搬迁（行为不变，分 4 个 commit）；generate_html 渲染逻辑拆分 | 每步全量测试 + 生成 HTML diff 为空 | 按文件 revert |
| **P5 校准工具 + 出口** | eval_selection.py：从 events.json 抽样本→人工标 gold→门槛扫描（对齐 AIHOT SelectBench 思路，本地报告形态）；llms.txt + /agent 说明页（静态，零成本） | 校准报告产出；llms.txt 上线 | 独立文件，随时撤 |

依赖链：P0→P1→P2→P3→P4（P4 技术上可提前，但放后降低评审负担）；P5 独立。

## 五、成本与风险

- **成本**：P2 后每事件约 3 次 AI 调用（方舟 ¥0.14/¥0.56 每百万 token），日均 60 事件估算 < ¥0.2/日；P3 归组仅对入库候选调模型（量级更小）。回执机制本身省钱（重试不重复付费）。
- **风险① 双评分数漂移**：与程序分不一致时以谁为准——定案：过渡期程序分仍为主排序轴，AI 双评分并排落库对比 2 周，数据说话后再切换主轴。
- **风险② 归组误合并**：AIHOT 用「拿不准的合并+复核模型」，本站反向——拿不准的不合并（本站历史教训是漏并而非误并，但误并伤害更大）；保留 canonical 指纹硬路径为第一道。
- **风险③ 拆包引入回归**：P4 每文件搬迁要求「生成物 diff 为空」这一硬验收，不满足不合并。
- **红线提醒**：全程不改 .github/workflows/；不动 .env/Secrets；每阶段只本地 commit，推送由你决定。

## 六、决策记录（2026-10-06 已拍板）

| 决策 | 结果 |
|---|---|
| 1. 路线 | **方案 B**——移植方法论，保 Python/GHA/$0 渐进重构 |
| 2. 五轴 | **按建议执行**——实质份量/信息增量/证据强度/BD 行动性/区域共振，按 action_type 加权 |
| 3. 切换策略 | **并排 2 周再切**——两套分数同时落库 + 分歧对比报告，数据验证后 AI 分主导排序，程序分退为兜底 |
| 4. P5 出口 | **一起做**——llms.txt + Agent 说明页 |

执行顺序：P0 → P1 → P2 → P3 → P4 → P5，每阶段独立 commit（本地不推送）。

## 附：本站已达标、无需动的部分

四道闸门与产品边界（比 AIHOT 更强）、日报规则化编排、观察账本七态、Evidence Atom、周月报晋级门槛与编辑缓存、fail-stale 策略、离线测试门控——这些是本站领先项，全部保留。

## 七、P4 详细执行方案（2026-10-06 定稿）

### 前置（已完成）

- 测试基线：`bash scripts/run_all_tests.sh` → **PASS=31 FAIL=0 SKIP=3**，全量约 5–7 分钟。
- 路径收口：`scripts/repo_paths.py` 已作为唯一锚点；裸路径实质性清零（59→16，余下为注释/git_ref 正确用法/一次性脚本豁免）。

### 硬验收（每步都跑）

1. `bash scripts/run_all_tests.sh` 全绿（31 项）。
2. **生成物 diff 为空**：对固定输入跑 `scripts/generate_html.py`，逐字节比对 `docs/index.html`。
   - 注意 `--force` 会调 AI（编辑层多通道重试），验收用离线路径，避免 API 依赖与长等待。
3. 导入冒烟：新增模块可独立 import，无循环依赖。

### fetch_news.py（4221 行）拆分地图

文件内已有天然分区注释，按此搬迁，**不改任何函数体逻辑**：

| 现区块（行范围） | 目标模块 | 说明 |
|---|---|---|
| 1–70 头部/`_cn_now` 等 | `content/util.py` | 日期与通用工具 |
| 71–118 aiohttp 并行 | `sources/http.py` | 并发抓取底座 |
| 119–171 信源标注 | `sources/meta.py` | 融资专属源等标注 |
| 172–490 27 家重点公司监控 | `sources/company_watch.py` | Google News RSS |
| 491–1279 关键词检测/归类/指纹（~790 行） | `content/classify.py` | 判型、区域、别名、主体键 |
| 1280–1620 事实评分账本 + 去重（~340 行） | `content/dedupe.py` | `_is_same_event`、`_fingerprint_match` |
| 1621–1704 工具函数 | `content/util.py`（并入） | |
| 1705–1832 采集 | `sources/collect.py` | |
| 1833–2408 HTML 备用采集（~580 行） | `sources/html_fallback.py` | 独立成文件，体量最大 |
| 2409–2537 智能过滤 | `content/filter.py` | |
| 2538–2882 AI 分析（MiniMax/豆包） | `providers/llm.py` | 通道与调用 |
| 2883–3650 P0 Agent 系列（~770 行） | `editorial/agents.py` | 标题改写/趋势/价值评分 |
| 3650–3690 og:image 补抓 | `content/og_image.py` | |
| 3691–4221 `main()` 流水线 | `pipeline.py` | 编排入口 |

### 实施纪律

- **每搬一个模块一个 commit**，commit 内只做搬迁 + import 修正，绝不夹带行为改动。
- `fetch_news.py` 保留为**兼容转发层**（re-export 全部公开符号），使现有 import 与测试零改动；待全部搬完后再决定是否删除。
- 搬迁完成后 `fetch_news.py` 应只剩 re-export 与 `main()` 入口。
- `generate_html.py`（2902 行）同理：先按渲染域（卡片/日期面板/信号簇/报告/发布）分区，再做同样处理。

### ⚠️ 核心风险：转发层会让 mock patch 静默失效

**风险描述**：现有 25 处引用 `fetch_news`，其中大量是私有符号。更要紧的是
`test_period_report.py` 有 20+ 处 `mock.patch('fetch_news._post_chat')` /
`'fetch_news._chat_api_candidates'`，其它文件则有
`from fetch_news import _post_chat` 这样的值绑定。

Python 的 `from X import f` 会把 `f` **绑定到导入方的命名空间**。一旦函数搬到
`providers/llm.py`：

- patch `fetch_news._post_chat` 只改转发层的名字，
- 而 `generate_html` 等导入方持有的是**搬迁前那一刻的引用**，
- 结果是测试**仍然通过，但根本没拦到真实调用** —— 静默失效，比直接报错危险得多。

**对策（按优先级）**：

1. **搬迁顺序上把 provider 层放最后**，且搬迁 `providers/llm.py` 时**同步改写所有
   调用点为 `from providers.llm import _post_chat`**，同时把测试的 patch 目标改到
   `providers.llm._post_chat`。这属于「import 修正」，在允许范围内。
2. **禁止调用方直接从 `fetch_news` 取 provider 函数**（即使是过渡期）——转发层只给
   外部脚本与测试兜底，主链路一律直连真实模块。
3. **每个模块搬完后，专门验证 mock 是否仍生效**：临时把被 patch 的函数改成抛异常，
   确认测试确实失败（证明 patch 命中了真实调用点），再恢复。不能只看「测试通过」。

这一条是 P4 里唯一可能造成**隐性质量损失**的地方，评审时请重点确认。

