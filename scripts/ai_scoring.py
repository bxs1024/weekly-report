"""AI 双评分层：同一标准独立评两次，取均值，与程序分并排落库。

对齐 AIHOT `industry/prompts/selection-score.md` + `industry/selection.ts` 的做法：
- 同一份评分提示词对同一条事件**独立问两次**，压掉单次评分的随机抖动。
- 页面上显示的分数是两次的平均（向下取整）；AIHOT 用「两次之和 ≥ 2×门槛」等价于均值门槛。
  本站沿用同一数学：均值 ≥ 门槛。

本站差异（docs/plans/2026-10-06-aihot-methodology-refactor.md 决策 3）：
- **过渡期程序分仍是主排序轴**，AI 双评分只落库并排对比，不改变现有展示顺序。
- 用 `AI_SCORE_ENABLED` 安全阀控制；关闭时行为与改动前完全一致。

输出字段（写在事件上，不改动既有字段）：
    ai_score_1 / ai_score_2 / ai_score_avg / ai_score_model / ai_score_prompt_version

设计约束：
- 只对已通过闸门的事件评分（评分只排序不过滤，闸门决定资格）。
- 失败不写分数（不写 0，避免污染对比），记录在 ai_score_error。
- 全部调用走回执，重复运行不重复付费。
"""

import os

try:
    from prompt_loader import load_prompt, prompt_version
    from ai_receipts import receipt_key, receipt_get, receipt_put
except ImportError:
    from scripts.prompt_loader import load_prompt, prompt_version
    from scripts.ai_receipts import receipt_key, receipt_get, receipt_put

SITE_NAME = '全球互联网动态情报站'
DEFAULT_THRESHOLD = 60
AI_SCORE_ENABLED_ENV = 'AI_SCORE_ENABLED'


def ai_score_enabled():
    """安全阀：没设或设成 true/1/yes 时开启；显式关掉则完全跳过本层。"""
    raw = (os.environ.get(AI_SCORE_ENABLED_ENV) or '').strip().lower()
    if raw == '':
        return True
    return raw in ('1', 'true', 'yes', 'on')


def build_score_prompt(event):
    """组装单条事件的评分输入。

    刻意不传信源层级、来源名称、旧分数——与 AIHOT 一致，避免模型靠来源名气打分。
    """
    material = {
        'title': (event.get('title') or '').strip(),
        'overview': (event.get('content_overview') or event.get('summary_short') or '').strip(),
        'reason': (event.get('reason') or '').strip(),
        'impact': (event.get('impact') or '').strip(),
        'region': (event.get('region') or '').strip(),
        'date': (event.get('date') or event.get('published_at') or '').strip(),
    }
    system = load_prompt('score')
    return system, material


def _parse_score(text):
    """从模型回答里抽出 attentionScore。允许 ```json 包裹。"""
    import json
    import re

    if not text:
        return None
    cleaned = re.sub(r'^```(?:json)?\s*', '', text.strip()).strip().rstrip('`').strip()
    try:
        data = json.loads(cleaned)
    except ValueError:
        match = re.search(r'\{[^{}]*"attentionScore"\s*:\s*(\d{1,3})[^{}]*\}', cleaned)
        if not match:
            return None
        data = {'attentionScore': int(match.group(1))}
    value = data.get('attentionScore')
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = int(value)
    if value < 0 or value > 100:
        return None
    return value


def _call_once(system, material, api, model_name, pass_index):
    """单次评分：先查回执，未命中才真的调模型。

    pass_index 是评分轮次（1/2）。必须进回执键——否则第二次评分会命中
    第一次写入的回执，两次评分退化成同一个数，失去「独立评两次」的意义。
    """
    import json

    payload = {'system': system, 'material': material, 'pass': pass_index}
    key = receipt_key('score', model_name, payload)
    cached = receipt_get(key)
    if cached is not None:
        return cached, True

    body = system + "\n\n待评材料：\n" + json.dumps(material, ensure_ascii=False) + "\n\n只返回 JSON。"
    from fetch_news import _post_chat

    resp = _post_chat(api, body, max_tokens=32, temperature=0.0, timeout=(10, 60))
    if resp.status_code != 200:
        raise RuntimeError(f'HTTP {resp.status_code}')
    text = resp.json()['choices'][0]['message']['content'].strip()
    score = _parse_score(text)
    if score is None:
        raise RuntimeError('回答无法解析出 attentionScore')
    receipt_put(key, score, prompt_kind='score', model=model_name)
    return score, False


def score_events(events, apis=None, model_name=None):
    """对一批事件做 AI 双评分，原地写入 ai_score_* 字段。

    返回统计：{'scored': n, 'failed': n, 'reused': n, 'enabled': bool}。
    """
    stats = {'scored': 0, 'failed': 0, 'reused': 0, 'enabled': ai_score_enabled()}
    if not stats['enabled']:
        return stats
    if not events:
        return stats

    if apis is None:
        try:
            from fetch_news import _chat_api_candidates
            apis = _chat_api_candidates()
        except Exception:
            apis = []
    if not apis:
        stats['failed'] = len(events)
        return stats
    if model_name is None:
        model_name = apis[0].get('name') or 'default'

    version = prompt_version('score')
    for event in events:
        system, material = build_score_prompt(event)
        if not material.get('title'):
            continue
        try:
            first, reused1 = _call_once(system, material, apis[0], model_name, 1)
            second, reused2 = _call_once(system, material, apis[0], model_name, 2)
        except Exception as exc:
            event['ai_score_error'] = f'{type(exc).__name__}: {exc}'[:120]
            stats['failed'] += 1
            continue

        event['ai_score_1'] = first
        event['ai_score_2'] = second
        event['ai_score_avg'] = (first + second) // 2
        event['ai_score_model'] = model_name
        event['ai_score_prompt_version'] = version
        event.pop('ai_score_error', None)
        stats['scored'] += 1
        if reused1 and reused2:
            stats['reused'] += 1
    return stats


def compare_with_program_score(events, threshold=DEFAULT_THRESHOLD):
    """过渡期对比报告：AI 双评分 vs 程序分的一致性与分歧。

    返回 {'n', 'agree_hi', 'agree_lo', 'divergent': [...], 'corr'}。
    用于「并排 2 周再切」决策的数据依据（方案决策 3）。
    """
    pairs = []
    for event in events:
        ai = event.get('ai_score_avg')
        program = event.get('signal_change_score')
        if ai is None or program is None:
            continue
        pairs.append((event, ai, program))

    if not pairs:
        return {'n': 0, 'agree_hi': 0, 'agree_lo': 0, 'divergent': [], 'corr': None}

    ai_vals = [p[1] for p in pairs]
    pg_vals = [p[2] for p in pairs]
    agree_hi = sum(1 for _, aa, pp in pairs if aa >= threshold and pp >= threshold)
    agree_lo = sum(1 for _, aa, pp in pairs if aa < threshold and pp < threshold)

    # 分歧：一方过门槛另一方没过，或差距超过 25 分
    divergent = []
    for event, ai, program in pairs:
        cross = (ai >= threshold) != (program >= threshold)
        far = abs(ai - program) > 25
        if cross or far:
            divergent.append({
                'title': (event.get('title') or '')[:60],
                'ai': ai,
                'program': program,
                'kind': 'cross' if cross else 'gap',
            })

    corr = None
    n = len(pairs)
    if n >= 3:
        mean_ai = sum(ai_vals) / n
        mean_pg = sum(pg_vals) / n
        cov = sum((a - mean_ai) * (p - mean_pg) for a, p in zip(ai_vals, pg_vals))
        var_ai = sum((a - mean_ai) ** 2 for a in ai_vals) ** 0.5
        var_pg = sum((p - mean_pg) ** 2 for p in pg_vals) ** 0.5
        if var_ai and var_pg:
            corr = round(cov / (var_ai * var_pg), 3)

    return {
        'n': n,
        'agree_hi': agree_hi,
        'agree_lo': agree_lo,
        'divergent': divergent[:20],
        'corr': corr,
    }
