# 展示口径契约

本文定义情报站各展示面的入选口径。评分层只回答“事件价值有多高”，展示选择器回答“事件应该出现在哪里”。代码入口统一放在 `scripts/view_selectors.py`。

## 分层原则

```text
采集 -> 快照/变化事实 -> 候选信号 -> 合格事件 -> 冻结展示资格 -> 独立事实 -> 周/月主题 -> 页面/RSS
```

- 采集层负责拿到候选事件，不决定最终展示。
- 范围准入层只允许既定行业，以及目标区域中与 AI 或既定行业直接相关的政策、行业和公司变化继续流转。
- 分析/评分层负责事件类型、分数、解释完整度和 BD 优先级。
- 产品边界层负责判断事件是否属于全球互联网产业情报站，而不是广义科技新闻站。
- 展示选择层负责首页、RSS、公司索引、复核区、周报/月报各自的产品口径。
- 日报分层负责在合格事件内部排序，不放松信源筛选和产品边界。
- 证据块层负责把同质事件压成独立证据，避免用相似材料硬撑趋势。
- 叙事层负责把判断、关注窗口和证据事件绑定成同一条推理链，主要服务周报/月报和后续解释层。
- 页面和 RSS 不直接拼评分规则，必须调用 selector。

## 事件资格冻结

入口：`scripts/event_contract.py` / `apply_view_contract()`

每条事件在进入存储或任何展示面前生成稳定字段：

- `view_status`：`main / review / filtered`。
- `view_reason`：入选、复核或过滤原因。
- `view_priority`：`selected / important / watch`。

统一信号字段同时冻结在事件上：`content_type`、`subject_type`、`action_type`、`domain`、`claim_type`、`confidence_score`、`attention_score`、`signal_change_score` 和 `score_breakdown`。它们分别回答内容是什么、对象是谁、动作是什么、哪个行业、主张属于哪一类、证据有多可信、日报应先看什么以及这个变化有多重要（主排序轴）。单条 Signal 不再评分趋势潜力；趋势只在聚合层由多事件/跨时间窗口判断。

页面、RSS、覆盖报表和信源转化报表必须读取这些字段；渲染阶段可以补展示文案，但不能重新改变事件是否合格。官方 Changelog、Developer Docs 和 Product Update 可使用确定性规则补齐事实说明，再冻结资格。

## 产品边界

入口：`scripts/internet_relevance.py`

范围入口：`scripts/scope_gate.py`。范围判断先于价值评分，并只使用原始标题、摘要和信源观察契约，不使用生成式 `reason` 或 `impact` 反推相关性。范围结果分为：

- `qualified`：明确属于既定行业/AI范围，并存在政策、行业结构或公司动作变化；
- `candidate`：行业相关但变化不明确，只记审计指标，不进入 AI 和日报；
- `filtered`：未命中既定行业/AI范围，直接过滤。

迁移口径：范围闸门只对带 `scope_enforced=true` 的新事件形成强约束。历史事件缺少 `source_excerpt` 等原始事实字段，不使用生成式 `reason`、`impact` 或旧标签反推范围，也不做机械批量回填；旧事件随 30 天周期窗口自然滚出。

目标：回答“这条事件是否属于本站”，独立于信源可信度和事件金额大小。

当前分级：
- `3 core_internet`：电商、支付、游戏、广告、SaaS、云、AI infra、开发者工具、社交、本地生活平台等核心互联网信号。
- `2 adjacent_internet`：明确指向软件、平台、数据基础设施、AI 应用或企业软件的相邻行业信号。
- `1 edge_observation`：工业软件、机器人、能源软件等边缘观察信号，不进入主展示。
- `0 out_of_scope`：军工/国防、生物制药、疗法、纯医疗器械、医疗基金、农业、建筑、矿业等不属于本站主赛道。

规则：
- 首页、RSS、关注窗口默认只允许 `internet_relevance >= 2`。
- 医疗/工业/能源等相邻行业不能按行业名一刀切；只有明确是平台、SaaS、EHR/医疗 IT、AI notetaker、云或 AI 基础设施时才保留。
- 军工/国防默认不属于本站主赛道；即使命中 AI、云或融资，也不得进入主展示。
- 不能用 `impact` 或泛化 `opportunity_direction` 反推产品边界，避免“影响到 IT 供应商”把任何行业都放进本站。

## 首页今日情报

入口：`select_homepage_events()`

目标：作为事件导航层，回答“今天有哪些合格事实、先看哪些、哪些继续跟、哪些留作观察”。日报不承担趋势成立与否的裁决，也不替用户隐藏已通过筛选的合格事件。

允许进入：
- 高价值事件。
- 非 Google News 的低分但可解释强信号事件，例如融资、并购、财报、战略。
- 通过范围和质量闸门的 `industry_report`、`model_release`、`regional_policy`。研报以“研报观点”标明机构归属；模型发布把发布事实与性能自述分开。
- `internet_relevance >= 2` 的本站主线事件。

不进入：
- 不属于本站主赛道的广义科技事件，即使金额大或来源可信。
- 需要质量修复的事件。
- 低信号 Google News 补漏。
- 缺少摘要、原因或影响说明的事件。
- 只有付费全文才能证明、但公开页面没有可核验依据的研报解读。

前台分层：
- `精选`：最先看，强信号、强相关、可直接进入判断。
- `重点`：值得继续跟，有明确对象或方向。
- `观察`：保留事实，用于背景留档和后续跟踪。

原则：
- 信源筛选是地基，事件分层是导航。
- `精选 / 重点 / 观察` 只在合格事件内部排序，不能把边界外、低质、重复或需修复内容放进日报。
- 右侧历史导航显示首页合格事件数量，不能再用全部事件库数量误导用户。

## 关注窗口

入口：`scripts/signal_clusters.py`

目标：把事实事件聚合成“值得观察的信号簇”。关注窗口是日报的辅助层、周报的主承载层；它不是商业机会结论，不替用户做最终跟进判断。

允许进入：
- 至少满足两个门槛：多个事件、多个对象、连续出现、影响区域、影响预算、影响组织、可信信源。
- 事件必须是非 `other` 类型，且不需要质量修复。
- 事件必须满足本站产品边界，`internet_relevance >= 2`。
- Google News 只能作为证据补充；纯 Google News 窗口需要更高门槛。

不进入：
- 单条孤立事件，除非后续有更多证据形成聚集。
- 明显低信号或解释不完整的事件。
- 只靠标题包装出来的“机会”判断。

展示措辞：
- 前台使用“关注窗口”“加入观察名单”“等待二次确认”等克制表达。
- 不使用“立刻跟进”“马上投入资源”等强决策表达。

## 叙事层

入口：`scripts/narratives.py`

目标：统一回答“当前最重要的主题是什么、为什么、证据是什么、涉及谁、建议动作是什么”。当前日报已改为事件导航层；Narrative 不再负责隐藏日报事实，后续主要服务周报、月报和窗口解释。

当前规则：
- 日报顶部展示“今日事件导航”，不是“趋势是否成立”的裁决。
- 日报展示所有合格事件，并按 `精选 / 重点 / 观察` 分层。
- 周报/月报可以继续使用 Narrative、Signal Cluster 和 Evidence Atom 收敛窗口、方向与趋势。
- Narrative 先选择锚点关注窗口，再只保留同区域或同主题的窗口，避免多个不相关窗口拼成一个判断。
- 同一公司不能重复进入多个窗口；若重复，保留排序更靠前的窗口。
- Narrative 需要先通过 `scripts/evidence_atoms.py` 的独立证据检查；同来源、同地区、同动作、同主题的相似事件只能算一个证据块。
- 达不到独立证据门槛时，不展示硬凑趋势。

## 成熟批次

入口：`select_mature_main_date()`

目标：避免最新自然日只有少量早盘数据时覆盖成熟批次。

时间节点必须分开：
- `workflow_run_time`：采集任务运行时点，例如北京时间 02:00 早采、09:00 补采。
- `event_date`：事件归属日期，来自事件本身或采集分析后的日期字段。
- `display_main_date`：首页最终展示的成熟批次日期，由 `select_mature_main_date()` 选择。

原则：
- workflow 跑了两次，不代表页面必须出现两个展示批次。
- 补采拿到的晚发内容可以归入前一日 `event_date`，不能被误解为运行日事件。
- 右侧历史导航和首页总数展示 `display_main_date` 的合格事件数，不展示 workflow 运行次数或原始入库数。

当前规则：
- 最新有数据日期少于 `MATURE_BATCH_MIN_EVENTS` 条可见事件时，回退到最近一个可见事件数足够的日期。
- 页面需要展示提示，说明最新批次较薄。

## 复核区

入口：`select_review_events()`

目标：保留可能有价值但不适合直接进入主列表的事件。

典型来源：
- Google News 中真实的组织动作，但未达到主列表可信度。
- 强信号事件但解释质量不足。
- 中等优先级、需要人工判断的事件。

复核区不是垃圾桶。明显低信号内容不应进入。

## 公司索引

入口：`build_entity_event_timelines()` / `scripts/entity_observation_ledger.py`

目标：对象池中的每家公司始终存在，并同时展示对象时间线、活动状态和覆盖状态。

规则：
- 公司索引唯一名单来自 `data/entity_pool.json` 的 12 家必须覆盖、14 家战略观察和 26 家实验对象（2026-08-14 扩到 52 家）。
- 时间线使用对象名称和别名匹配全部合格事件，不再只读取公司专用抓取事件。
- 短别名必须按词边界匹配，避免 `Sea` 命中 `research` 或 `overseas`。
- 活动状态回答“近期有没有动作或候选”，覆盖状态回答“直接观察点是否真实运行”，两者不得合并。

## RSS

入口：`select_feed_events()`

目标：只推送高价值、解释完整、格式统一的事件，降低订阅理解压力。

规则：
- 优先从首页今日事件里选高价值事件。
- 今日无高价值事件时，回退到最近一个有高价值事件的批次。
- RSS 只推 `internet_relevance >= 2` 的事件。
- RSS 不承担补漏和候选展示功能。

研报、模型发布和区域政策若已进入首页合格事件，遵循同一 RSS 资格；RSS 不新增一套内容类型白名单。

RSS 另有独立「全球AI视野 Top5」汇总条：读 `data/aihot_hot.json` 当前快照 Top5（标题+热度+原始链接），放在事件流之后，entry id 按日期生成，避免同一热点每天刷成新条目。

## 周报/月报

入口：`scripts/period_themes.py`

目标：用于周期报告中的高优先级机会统计。周报负责把近 7 天事件收敛成关注窗口和方向；月报负责观察跨周重复出现的趋势和结构变化。

规则：
- 周报采用“本期主线 -> 本周主题 -> 证据事件”，每个主题至少有两个独立事实。
- 周报编辑层：`generate_html.build_period_narrative()` 用 AI 把本周主题写成叙事导读（本期主线 + 每主题一段），替换纯模板拼接；AI 失败时回退模板，不影响页面。
- 同一对象同一动作的转载只算一个事实；来源所在地不能直接当作事件地域。
- 月报采用“本月结构变化 -> 跨周统计 -> 历史基线比较 -> 代表证据”。
- 月报趋势至少跨两个周次、三个独立事实，并与当前窗口之前 3 个同长度窗口的基线均值比较；覆盖率校正避免新增信源被误判为升温。
- 月报含三个维度的变化聚合，共用同一套跨周门槛+基线+覆盖率校正（`scripts/period_themes.py` 的 `_build_dimension_changes`）：
  - **主题结构变化**（`period_themes`）：按行业主题分组；
  - **公司变化**（`company_changes`）：按 `company_name` 分组，看某公司是否集中加码某方向；
  - **行业变化**（`industry_changes`）：按 `domain`/`scope_industries`/`vertical` 分组，看赛道信号增减。
  三者在月报页面并列展示，只做变化检测不做观点；区域聚合按方案后置未做。
- 不再用默认机会方向填充月报主题；证据不足时宁可不生成趋势。
- 月报已接入 AI 编辑层：`generate_html.build_monthly_editorial()` 用 AI 把结构趋势写成月度标题与导读；失败时回退模板。
- 周报/月报含「本周/本月 AI 热点」小节：按自然周期读取 `data/aihot_hot/YYYY-MM-DD.json` 归档（AIHOT 全球 AI 视野，不标来源），当期为空则回退最近一期非空归档。

## Jobs 候选信号

入口：`scripts/job_observation.py` / `data/signal_candidates.json`

```text
职位快照 -> 差分事实 -> 职能变化簇 -> 候选信号 -> 合格事件
```

- 首次快照只建立基线。
- 候选必须保留 `candidate_id`、时间窗口、基线/当前数量、增减数量、职能簇和 `evidence_refs`。
- 候选状态支持积累、拒绝、晋级和已转事件；未晋级必须保留原因。
- ATS 迁移、全量刷新或大面积列表重置标记 `source_reset_suspected`，不得生成扩张事件。
- 单个职位不进入事件库；只有明确且集中的职能变化簇才能晋级。

## 健康检查

入口：`scripts/check_data_health.py`

每天至少检查：
- 最新一次采集漏斗：原始数、去重数、过滤后数、AI 分层数、新增入库数、重复跳过数。
- 最近采集时点对比：运行日、运行时间、新增事件所属日期、入库信源层级，判断早跑/晚跑和补采差异。
- 最新展示批次是否为空。
- 最近 7 天每日主列表和复核区数量。
- 公司索引优质公司数是否为 0。
- RSS 是否为空，Google News 是否主导 RSS。
- 近 7 天展示事件重复率是否异常。
- `must` 对象有效覆盖率、失败观察点、Jobs 失败、候选积压和已晋级候选数。

采集阶段指标写入 `data/run_metrics.json`，默认保留最近 30 次运行。`scripts/collection_timing_report.py` 可单独输出采集时点对比表；`scripts/check_data_health.py` 会同时打印最近 8 次。`update.yml` 的 `health-check` job 已接入 CI（`needs: render`，页面提交后运行 `--quick --strict`，失败即开告警 Issue）。日常巡检可先运行 `python scripts/check_data_health.py --quick`，只读取持久化事实，不重建页面和历史来源转化；发布前仍运行完整检查。

## 信源转化治理

入口：`scripts/source_conversion_report.py`

目标：把“这个源有没有干活”拆成可观察漏斗，而不是只看首页体感。

当前字段：
- `raw`：最近周期内原始抓取条目。
- `signal`：采集阶段命中的候选信号。
- `stored`：最终进入 `events.json` 的事件。
- `main` / `review`：进入首页主列表或复核区的事件。
- `out_of_scope_industry`：入库后被本站产品边界排除。
- `capital_only_low_actionability`：融资事件属于主赛道但缺少明确预算、扩张、采购、生态合作或区域进入信号。
- `quality_review`：解释不完整、fallback、needs_repair 或 quality_flags。
- `google_not_main`：Google News 补漏源未进入主列表。
- `other_type` / `weak_signal`：类型或强度不足。
- `lost_after_signal≈`：`signal - stored` 的近似损失，当前 run metrics 还没有逐条 drop reason，因此只能用于定位高 signal 低入库的源。

用法：
- 最近 7 天看近期异常：`python scripts/source_conversion_report.py --days 7`
- 最近 15/30 天看长期有效性：`python scripts/source_conversion_report.py --days 30`
- `check_data_health.py` 会输出轻量摘要和高 signal 低首页源，作为日常巡检入口。

## 对象池与观察点治理

入口：`data/entity_pool.json` / `scripts/entity_signal_conversion_report.py`

### 公司观察账本

入口：`data/entity_observation_ledger.json` / `scripts/entity_observation_ledger.py`

公司索引不再把所有“0 条优质事件”视为同一种空状态。每个观察点必须尽量回答：最近是否检查、是否成功访问、是否观察到变化，以及变化是否形成合格事件。

- `active`：近期形成合格组织行为事件。
- `quiet`：采集成功，近期没有显著变化。
- `changed_below_threshold`：观察到变化，但未达到情报门槛。
- `failed`：最近一次采集失败。
- `partial`：对象只有部分观察点具备可信运行状态。
- `pending`：观察点已登记但采集器尚未接入。
- `unverified`：旧运行数据缺少成功/失败证据，等待新采集确认。

Jobs 观察点采用“快照 -> 差分 -> 职能聚类 -> 候选信号”的口径。单个职位不是事件，首次快照只建立基线，不产生变化候选。

### 日期语义

事件的 `published_at`、`observed_at` 与 `scheduled_at` 分开记录。`date` 继续作为兼容现有页面的展示桶，但必须标注 `date_basis`；未来计划或正文生效日期不得写入 `published_at`。健康检查发现未来发布日期时必须失败。

目标：从 Source Pool 逐步转向 Entity Pool。对象池回答“我们长期观察谁”，观察点回答“从哪里看它的组织行为变化”。

当前链路：

```text
Entity -> Observation Point -> Raw Signal -> Candidate Signal -> Qualified Signal -> Event -> Window
```

第一版原则：
- 每个重点对象至少记录官方/IR、Jobs、Changelog/Developer/Product 三类观察点。
- `active` 表示当前采集链路已有对应来源或公司监控；`candidate` 表示已进入治理池但尚未接入自动抓取。
- `instrumented=false` 是刻意保守标记：目前大多数观察点还没有结构化 raw_signal 转化数据，不能假装已监控完整。
- `entity_signal_conversion_report.py` 先用现有 `events.json` 统计对象覆盖、首页贡献、复核贡献和未接入观察点。

后续接 jobs/changelog 时，必须把观察点也纳入转化监控，不能只把内容直接塞进事件库。
