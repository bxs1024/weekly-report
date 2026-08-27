# API 降本调研待办（遗留，用户晚点处理）

> 日期：2026-08-20
> 状态：调研完成，实施遗留
> 触发：用户反馈 DeepSeek API 费用变高，想降本

## 用量基线（已量化）

- 每天 2 次采集 × ~80 条事件全量 AI 分析（30 条/批，约 6 批/天）
- 输入/批 ≈ 4500 token（规则 500 + 示例 1300 + 30 条标题 2700）
- 输出/批 ≈ 4000 token 上下（max_tokens=4096）
- 月合计：输出约 200-300 万 token、输入约 80 万 token
- 按 DeepSeek 历史价位估算月成本 ¥30-60 量级；近期上涨需到 platform.deepseek.com 控制台确认**是调价还是用量增长**（事件量 8 月已从 60/天涨到 87/天）

## 三个优化点（按性价比排序，均未实施）

1. **示例精简**：`fetch_news.py:2507` AI_EXAMPLES 的 8 个长示例 → 3 个（保留融资/财报方向/Report 语义三例），每批省 ~800 token，零风险，随时可做
2. **分析前预去重**：现在流程"先分析后合并"，同事件多来源重复付分析费（日浪费 5-15%）。把去重挪到分析前：同标题相似高只发一条，被合并源 URL 带上一起给 AI
3. **分析指纹缓存**（推荐与身份卡方案合并做）：按 URL+归一化标题建指纹，跨 run（早/晚班）命中历史库的跳过重分析

## 免费 API 源候选（未选定）

| 候选 | 特点 | 顾虑 |
|------|------|------|
| Google Gemini API | 有免费层级，GHA 网络通，质量好 | 国内注册/访问不便 |
| 硅基流动 SiliconFlow | Qwen 系免费档，OpenAI 兼容，接入最简单 | 免费档能力偏低 |
| 豆包火山方舟 | 项目已有 key | 促销额度非每日免费 |

## 进展（2026-08-27 更新）

- **优化点 1 已完成**：AI_EXAMPLES 8 例 → 3 例，commit `719559e`（本地未 push）。每批 prompt 省 1367 token（-62%），超出方案预期 ~800 token；全套离线测试 0 失败。保留融资/财报/Report 语义三例。
- **免费源已选火山方舟 DeepSeek V4 Flash**：endpoint `ep-20260827101830-qgtm4`，base `https://ark.cn-beijing.volces.com/api/v3/chat/completions`。连通性测试通过：ping 3.1s 返 200；中文摘要采样 10.6s 返 200，模型回显 `deepseek-v4-flash-ga-260731`，usage `prompt=121 / completion=1205(reasoning 1192)`。注意：方舟 V4 Flash 把 reasoning token 也算进 completion，价格是官方 V4 Flash 的 1/6 左右。
- **当前主链用的不是 V4 Flash**：`.env` 未设 `DEEPSEEK_MODEL`，代码默认 `deepseek-chat`（官方软指针，随时间切版本）。官方 V4 Flash 价格约 ¥0.88 输入 / ¥3.52 输出 每百万 token；方舟 V4 Flash 约 ¥0.14 / ¥0.56。**官方主链价格是方舟的 6 倍以上**——切到方舟等于直接降本 6 倍。
- **方案已锁定为 ②**：方舟 V4 Flash 排首位，官方 DeepSeek 降级，豆包保底。`_chat_api_candidates` 顺序改为 `ark-deepseek → deepseek → doubao`。secrets 需新增 `ARK_API_KEY` + `ARK_MODEL`。

## 待用户决策

- [ ] 确认 DeepSeek 账单归因（调价 vs 用量）
- [x] ~~是否做优化点 1、2（①零风险可立即做）~~ → ①已做，②继续搁置
- [x] ~~免费源选哪家（有账号的优先）~~ → 选火山方舟 V4 Flash，**主链切换待用户拍板**
- [ ] **主链切换实施**：用户手动在 GHA Secrets 加 `ARK_API_KEY=你的key` + `ARK_MODEL=ep-20260827101830-qgtm4`；本地 `.env` 同步加；然后我改 `fetch_news.py` + `update.yml` + `retrofit_events.py` + `repair_stale_analysis.py`
- [ ] 优化点 2（分析前预去重）
- [ ] 优化点 3（分析指纹缓存与身份卡合并做）

## 安全提示（2026-08-27）

本会话用户桌面文件 `C:\Users\16120\Desktop\新建文本文档 .txt` 暴露了 ARK API Key （原文已移除——该 key 已于 2026-08-27 轮换作废） 到本会话历史与命令记录。**强烈建议立刻去火山方舟控制台轮换该 key**。轮换后再做主链切换，避免新 key 也被历史捕获。

## 关联

- AI 身份卡方案：docs/plans/2026-08-20-event-dedup-label-scope-fix.md（优化点 3 与身份卡缓存合并）
- 降级链路由：fetch_news.py `_chat_api_candidates`（2721 附近）
- AI_EXAMPLES 精简 commit：`719559e`