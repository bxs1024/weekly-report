# 🌍 全球互联网动态情报站

自动采集全球互联网产业、重点公司、行业变化和区域 AI/互联网政策，通过规则与 AI 分析整理为可读、可审计的情报产品。

**范围**：围绕既定行业和区域观察中国、亚太、中东、非洲、拉美、欧洲等市场；中国公司只纳入重大 AI/互联网动作，不因地域自动加分。

---

## 在线访问

**https://bxs1024.github.io/weekly-report/**

每天北京时间 02:00 早采、09:00 补采。02:00 覆盖亚太/欧洲和已有更新，09:00 补欧美晚发内容；采集时点只代表 workflow 运行时间，页面展示仍按事件日期和成熟批次计算。AIHOT 热点与模型榜由独立任务每天北京 08:00 / 20:00 抓取（**只更新数据文件，页面统一由主采集渲染**），周报/月报含「AI 热点」小节，RSS 含「全球AI视野 Top5」。

---

## 使用指南

- 快速理解产品目标和整体架构：[docs/SYSTEM_OVERVIEW.md](docs/SYSTEM_OVERVIEW.md)
- 详细使用说明和踩坑记录：[docs/USAGE_GUIDE.md](docs/USAGE_GUIDE.md)
- 首页、RSS、公司和周期报告展示契约：[docs/VIEW_CONTRACT.md](docs/VIEW_CONTRACT.md)

---

## 本地运行

### 1. 克隆仓库

```bash
git clone https://github.com/bxs1024/weekly-report.git
cd weekly-report
```

### 2. 配置 API Key

```bash
# 编辑 .env 填入 DEEPSEEK_API_KEY=sk-xxx
# fetch_news.py 会通过 dotenv 优先读取 .env；GHA 中通过 Secrets 注入

# DeepSeek 获取地址：https://platform.deepseek.com/
# 豆包获取地址：https://console.volcengine.com/ark/
```

### 2.1 配置反馈 API（可选）

反馈表单应写入线上记录，而不是只保存在浏览器。可部署 `workers/feedback-worker.js`，由 Worker 持有 `GITHUB_TOKEN` 并创建内部 Issue：

```bash
# 站点生成时注入前端提交地址
FEEDBACK_ENDPOINT=https://your-worker.example.workers.dev py -3 scripts/generate_html.py --force

# Worker 侧配置 GitHub token，不要写进前端或仓库
wrangler secret put GITHUB_TOKEN
```

未配置 `FEEDBACK_ENDPOINT` 时，页面会提示线上提交通道尚未配置，并保留本机草稿。

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 运行

```bash
# Windows（使用 Python 启动器）
py -3 scripts/generate_html.py --force

# Linux/macOS
python scripts/generate_html.py --force
```

生成的文件在 `docs/index.html`，用浏览器打开即可预览。

---

## 信源

### RSS 信源

| 层级 | 信源 | 覆盖区域 | 说明 |
|------|------|----------|------|
| L2 垂直交易源 | TechCrunch / TechCrunch VC | 全球 | 创业、融资、VC 动态 |
| L2 垂直交易源 | Tech.eu / UKTN / EU-Startups | 欧洲 | 欧洲融资、并购、上市与科技公司动态 |
| L3 区域生态源 | The Recursive / The Next Web | 欧洲 | 欧洲区域生态与战略动态 |
| L2 垂直交易源 | Tech in Asia / Inc42 | 亚太 | 亚洲与印度科技、融资、上市动态 |
| L3 区域生态源 | TechWire Asia | 亚太 | 亚太科技生态动态 |
| L4 垂直赛道精品源 | Finextra / Payments Dive | 全球 | 支付、金融科技、监管和商户网络信号 |
| L4 垂直赛道精品源 | Retail Dive / EcommerceBytes | 全球 | 电商、零售科技、AI 购物和平台变化 |
| L4 垂直赛道精品源 | Social Media Today / Mobile Marketing Magazine | 全球 | 社交平台、移动生态和广告商业化变化 |
| L2 垂直交易源 | WAMDA / MENAbytes | 中东 | 中东北非创业、融资、合作动态 |
| L2/L3 | Disrupt Africa / Ventureburn / TechCabal / Techpoint / WeeTracker | 非洲 | 非洲创业融资、平台经济、区域生态 |
| L2 垂直交易源 | LatamList / LAVCA | 拉美 | 拉美融资、创投、私募与创业动态 |
| L3 区域生态源 | Contxto | 拉美 | 拉美创业与创新生态 |
| L4 深度趋势源 | Rest of World Money / Ecommerce | 全球南方 | 只保留高信号事件，用于趋势和周/月报判断 |

### 公司监控

| 层级 | 信源 | 用途 |
|------|------|------|
| L1 官方/IR源 | Rakuten、Grab、MercadoLibre、Adyen、Sea、Zalando、Allegro、Kaspi.kz、Naver、Kakao、HKTVmall、U-NEXT、Square Enix、Jumia | 校准重点客户自身披露，优先保留财报、公告、战略和新闻稿 |
| L5 Google News 补漏源 | 31 家重点公司关键词 | 只做公司动态雷达，每家公司最多 2 条，默认不保留 `other` 类；中资公司只保留海外投资、跨境合作、海外市场和出海业务动向 |

每条事件会写入 `source_tier`、`source_role`、`bd_triggers`、`opportunity_direction`、`follow_up_window`、`bd_priority`。日报用它们辅助判断“今天先看谁和哪些证据”，周报再收敛窗口和方向，月报看趋势与结构变化。

信源处理库维护在 `data/source_registry.json`。新增自动采集源前，先在处理库记录赛道、信号类型、质量层级、抓取方式和晋级判断；候选源样本稳定后再进入脚本配置。

信源转化用 `scripts/source_conversion_report.py` 观察：原始抓取、信号命中、入库、首页展示、复核、边界外、质量复核、Google News 未进主列表等原因。它和 `source_health_report.py` 的区别是：前者看“转化漏斗”，后者看“生命周期健康”。

重点对象池维护在 `data/entity_pool.json`。对象池不是媒体列表，而是 Grab、Shopee、MercadoLibre、Stripe 等长期观察对象及其 newsroom/IR、jobs、changelog/developer docs/product update 观察点。`scripts/entity_signal_conversion_report.py` 用现有事件先统计对象覆盖、首页贡献和未接入观察点；后续接入 jobs/changelog 时继续沿用同一套转化口径。

### HTML 备用采集

| 信源 | 覆盖区域 | 说明 |
|------|----------|------|
| DealStreetAsia | 亚太 | RSS 停用（Temporarily Disabled），JS SPA 降级成功率低 |

---

## 技术栈

- **数据采集**：Python + aiohttp + BeautifulSoup（异步 RSS + HTML 降级采集）
- **AI 分析**：评分前置分流 → DeepSeek API（主力，GHA 可用）+ 豆包 API（降级）+ 程序降级
- **API Key 安全**：PBKDF2 + Fernet 加密存储
- **页面生成**：Jinja2 模板
- **部署**：GitHub Actions + GitHub Pages

**月成本：$0**

---

## 项目结构

```
weekly-report/
├── .github/
│   ├── workflows/update.yml      # 自动更新工作流（北京时间 02:00 / 09:00）
│   └── workflows/aihot.yml       # AIHOT 热点+模型榜独立工作流（北京时间 08:00 / 20:00）
├── data/
│   ├── events.json               # 完整结构化事件历史
│   ├── aihot_hot/                # AIHOT 热点按天归档（供周报/月报 AI 热点小节）
│   ├── aihot_hot.json            # AIHOT 热点当前快照
│   ├── model_leaderboard.json    # AIHOT 模型榜
│   ├── source_registry.json      # 信源注册中心
│   └── entity_pool.json          # 重点对象池与观察点
├── scripts/
│   ├── fetch_news.py             # 爬取 + AI 分析
│   ├── fetch_aihot_hot.py        # AIHOT 热点抓取（按天归档）
│   ├── fetch_model_leaderboard.py # AIHOT 模型榜抓取
│   ├── generate_html.py          # 生成 HTML
│   ├── period_themes.py          # 周/月主题 + 公司/行业变化聚合
│   ├── signal_scoring.py         # 评分（signal_change_score 主排序轴）
│   ├── calibrate_weights.py      # 评分权重离线校准（只读）
│   ├── source_conversion_report.py # 信源转化漏斗
│   ├── entity_signal_conversion_report.py # 对象/观察点转化治理
│   ├── template.html             # HTML 模板（设计 SSOT）
│   └── DESIGN_WORKFLOW.md        # 设计变更流程
├── docs/
│   └── index.html                # 生成的页面
├── requirements.txt
└── README.md
```

---

## 设计说明

页面采用"极简克制 + 现代杂志风"设计：

- **四层产品职责**：日报做事件导航 → 周报看窗口和方向 → 月报看趋势和结构变化 → 公司索引看单个对象时间线
- **两 tab 统一卡片风格**：今日要点和全部事件使用一致的 `.daily-event` 卡片设计
- **今日事件导航**：合格事件全部展示，按精选、重点、观察排序，不替用户隐藏事实
- **信源筛选底线**：分层分级不放松信源筛选，边界外、低质、重复或需修复内容不进入日报
- **固定顶栏**：搜索和筛选始终可见
- **事件图片**：左侧 100px×70px 缩略图（RSS media_content → og:image 两级兜底）
- **事件标签**：资金流向 / 合作机会 / 警示信号 / 趋势信号 / 中资出海
- **区域标签**：欧洲 / 亚太 / 中东 / 非洲 / 拉美
- **响应式**：移动端单栏布局

---

## 采集信号类型

1. **融资**：融资轮次、估值变化、投资者
2. **并购**：收购、合并、战略投资
3. **财报**：季度/年度财务结果、IPO、上市
4. **战略**：合作伙伴、新产品发布、扩张、裁员、关停
5. **行业研报**：市场规模、份额、区域分布、预测和 benchmark 等可核验行业判断
6. **AI 模型**：模型发布、开放方式、API、价格和开发者可获得性
7. **区域政策**：与既定行业/AI相关的法规、牌照、监管和生效变化

事件先过范围门槛和信源质量筛选，再分别计算 `confidence_score`、`attention_score` 和 `signal_change_score`（主排序轴）。单条 Signal 不评分趋势潜力；趋势只在聚合层由多事件/跨时间窗口判断。月报含三层变化聚合：**主题结构变化**（按行业主题）、**公司变化**（按公司聚合，看集中加码方向）、**行业变化**（按行业聚合，看赛道信号增减），三者共用"跨周门槛 + 前 3 窗口基线均值 + 覆盖率校正"，只做变化检测不做观点；月报历史趋势与基线均值比较并做覆盖率校正。日报展示合格事件并按“精选 / 重点 / 观察”排序；周报、月报需要独立证据积累后才形成窗口和趋势。研报没有全文时只依据公开摘要、目录、新闻稿或公开二次解读，并保留解读依据。评分权重（action_type 分档：政策25/产品24/融资22/财报18/扩张18/招聘12）可用 `scripts/calibrate_weights.py` 用真实数据离线校准。
