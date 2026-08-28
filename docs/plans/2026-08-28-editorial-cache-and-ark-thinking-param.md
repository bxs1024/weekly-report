# 编辑层缓存化 + 方舟关思考参数修复（2026-08-28）

## 背景与根因

2026-08-27 方舟 key 切换后云端首跑 39 分钟（平时 9 分钟）。排查定性三个根因：

1. **关思考参数失效**：代码发 `reasoning:{effort:"none"}`，A/B 实测思考 token 只从 50 压到 17（被静默忽略）；官方有效参数 `thinking:{type:"disabled"}` 实测归零（2026-08-28 方舟官方答复确认）。思考偷跑也是方舟单流出字 ~39-50 tok/s（编辑级输出 21s+）的主要原因之一，关闭后实测 ~104 tok/s。
2. **编辑层无缓存全量重跑**：每次运行对全部 17 周+4 月重调 AI 生成编辑导读（21 次长输出/班），封档周期输入不变也重写。方舟慢 + 晚高峰公共池排队，把 Generate HTML 放大到 31m54s（平时 3m51s）。
3. **超时重试反向**：编辑层 (10,60) 失败后 (10,90) 等更久再放弃，4 次月报 × 150s 白等。

另确认：fail-closed 的 `RuntimeError` 无任何捕获，且 Generate HTML 在 commit 步骤之前——一旦抛出，当班采集的事件数据也不提交，整班白跑。

## 第一性原理

编辑导读由输入主题唯一决定（AI 只做写作，不做决策）。封档周期输入冻结 → 输出冻结 → 重写不是"慢"，是**不该发生**。正确形态：编辑层是内容寻址的派生数据（缓存），不是每班的实时计算。

## 实施内容

### 改动 1：方舟参数换官方版（3 处）

`reasoning: {"effort": "none"}` → `thinking: {"type": "disabled"}`：

- `scripts/fetch_news.py` `_post_chat`（覆盖编辑器/标题改写/今日判断等全部经此的方舟调用）
- `scripts/fetch_news.py` `analyze_events_ark`（事件分析主链；repair_stale_analysis 导入自动受益）
- `scripts/retrofit_events.py` `_pick_api` 方舟分支补 `id:'ark'` + `rewrite_batch` payload 补参数（此前思考全开）

不动：`analyze_events_doubao`（豆包模型非 V4-Flash）、update.yml/aihot.yml（workflow 红线，curl 探测为 1-token ping 无影响）。

### 改动 2：编辑层缓存化（generate_html.py）

- 新文件 `data/editorial_cache.json`：`{version, periods: {<"weekly:2026-W35"/"monthly:2026-07">: {input_hash, editorial, channel, generated_at}}}`，坏文件静默当空缓存自愈
- 新增模块函数：`_editorial_cache_path/_load/_save/_get/_put/_editorial_input_hash`；`EDITORIAL_PROMPT_VERSION=1`，未来改 prompt 递增即全量失效
- `build_weekly_editorial/build_monthly_editorial` 加 `cache_key` 参数：
  - hash 命中 → 直接返回缓存（`📋 命中缓存`），零 AI 调用
  - 未命中 → 生成；通道改 **DeepSeek 官方优先**（任务形状路由：编辑层低频长输出走实测最快通道 ~9s/次，方舟留给事件分析主链），每通道单发 timeout=(10,60) 不二档
  - 成功 → 写缓存
  - **fail-stale**：全通道失败但有旧版缓存 → 沿用旧版照常发布；无缓存 → 维持 fail-closed raise（全新周期+全 AI 死的罕见双重故障，宁可不发不发降级版）
- 迟到事件落入封档周期：hash 变化自动重生成，缓存键天然处理

### 测试（scripts/test_period_report.py）

- 模块级隔离：mock `_load_editorial_cache`（side_effect 每次新 dict——`return_value={}` 共享可变对象会把上一条测试的 put 泄漏给下一条）+ `_save_editorial_cache`
- 新增 4 测：缓存命中不调 LLM / fail-stale 不 raise / 输入变化重生成并写缓存 / 输入 hash 内容寻址纯函数

### 预播种

本地以 origin/main 数据冷启动生成 17 个周的编辑缓存（161s），缓存文件随本版入库，云端首班即热启动。

## 验证结果

- 全套离线测试 FAIL 0（含 4 个新测试）
- 参数探测：经 `_post_chat` 的方舟调用 `reasoning_tokens=0`（编辑级 1086 token 输出 10.4s）
- 端到端（隔离目录 + origin/main 数据）：PASS1 冷启动 17 档案 160.9s（DeepSeek ~9.5s/次）→ PASS2 全命中缓存 6.5s，内容一致

## 预期效果

| 指标 | 现状 | 改后 |
|------|------|------|
| 每班编辑 AI 调用 | 21 次（方舟慢时 30+ 分钟） | 0-2 次（约 10-20 秒） |
| AI 全挂时 | 页面中止 + 当班数据丢弃 | 上一版缓存照常发布，数据照常提交 |
| 方舟思考 token | 17-50 | 0 |
| 方舟角色 | 全链主用 | 事件分析主链（高频短输出），编辑层降为备用 |

## 后续（未做，另行决策）

- 火山引擎控制台开 FastTpm 低延迟通道 + 请求带 `service_tier`：改善事件分析主链晚高峰排队（用户手动操作）
- 云端观察：GHA 播种后首班 Generate HTML 应 ≤2 分钟，日志大量「命中缓存」
