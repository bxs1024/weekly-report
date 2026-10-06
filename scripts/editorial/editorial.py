"""编辑部成稿：周报/月报正文生成，带 prompt 版本化缓存。"""

import hashlib
import json
import os
import re
from datetime import datetime

try:
    from prompt_loader import prompt_version, render_prompt
    from repo_paths import data_path
except ImportError:
    from scripts.prompt_loader import prompt_version, render_prompt
    from scripts.repo_paths import data_path

def _editorial_prompt_version(kind):
    """编辑层提示词版本（内容哈希）。kind: weekly | monthly。"""
    try:
        return prompt_version(f'editorial-{kind}')
    except Exception:
        return 'unknown'

def _editorial_cache_path():
    return data_path('editorial_cache.json')

def _load_editorial_cache():
    try:
        with open(_editorial_cache_path(), 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}

def _save_editorial_cache(cache):
    try:
        os.makedirs('data', exist_ok=True)
        with open(_editorial_cache_path(), 'w', encoding='utf-8') as handle:
            json.dump(cache, handle, ensure_ascii=False, indent=2)
    except OSError:
        pass

def _editorial_cache_get(cache_key, input_hash):
    """返回 (exact, stale)：exact 为 hash 完全命中的导读；stale 为同周期旧版导读（AI 全败时 fail-stale 兜底）。"""
    entry = (_load_editorial_cache().get('periods') or {}).get(cache_key)
    if not entry:
        return None, None
    editorial = entry.get('editorial')
    if entry.get('input_hash') == input_hash:
        return editorial, editorial
    return None, editorial

def _editorial_cache_put(cache_key, input_hash, editorial, channel):
    cache = _load_editorial_cache()
    cache.setdefault('version', 1)
    cache.setdefault('periods', {})
    cache['periods'][cache_key] = {
        'input_hash': input_hash,
        'editorial': editorial,
        'channel': channel,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }
    _save_editorial_cache(cache)

def _editorial_input_hash(brief, prompt_kind=None):
    """输入指纹。带上提示词版本，改提示词即自动全量失效。"""
    payload = json.dumps(brief, ensure_ascii=False, sort_keys=True)
    prefix = 'v?'
    if prompt_kind:
        prefix = f'p{prompt_kind}:{_editorial_prompt_version(prompt_kind)}'
    return hashlib.sha256(f"{prefix}:".encode('utf-8') + payload.encode('utf-8')).hexdigest()

def build_weekly_editorial(themes, period_id, cache_key=None):
    """AI 编辑层：把周报主题写成当期标题与叙事导读。失败优先沿用上一版缓存（fail-stale），无缓存返回 None。"""
    if not themes:
        return None
    try:
        from providers import llm as _llm
    except ImportError:
        try:
            from scripts.providers import llm as _llm
        except ImportError:
            return None
    # 走模块引用而非 `from X import f`：后者是值绑定，测试 patch
    # providers.llm._post_chat 会静默失效，表现为「补丁写了却在调真实 API」。
    apis = _llm._chat_api_candidates()
    if not apis:
        return None

    # 任务形状路由：编辑层低频长输出，优先走实测最快最稳的 DeepSeek 官方；方舟留给事件分析主链
    apis = sorted(apis, key=lambda a: 0 if a.get('id') == 'deepseek' else 1)

    theme_brief = []
    for t in themes:
        evs = [e.get('title', '') for e in (t.get('evidence') or [])][:4]
        change_events = t.get('change_brief') or [
            {'title': title, 'date': '', 'type': ''} for title in evs
        ]
        theme_brief.append({
            'key': t.get('key', ''),
            'title': t.get('title') or t.get('direction', ''),
            'region': t.get('region', ''),
            'objects': t.get('objects', ''),
            'why': t.get('why', ''),
            'evidence_titles': evs,
            'change_events': change_events,
        })

    input_hash = None
    stale = None
    if cache_key:
        input_hash = _editorial_input_hash(theme_brief, 'weekly')
        exact, stale = _editorial_cache_get(cache_key, input_hash)
        if exact:
            print(f"  📋 周报编辑命中缓存（{period_id}）: {(exact.get('mainline') or '')[:30]}...")
            return exact

    prompt = render_prompt('editorial-weekly', {
        'period_id': period_id,
        'theme_brief': json.dumps(theme_brief, ensure_ascii=False, indent=2),
    })

    for api in apis:
        try:
            # 每通道单发：超时说明该通道当前干不了这活，等更久只会放大总时长，直接换下一通道
            resp = _llm._post_chat(api, prompt, max_tokens=1400, temperature=0.3, timeout=(10, 60))
            if resp.status_code != 200:
                print(f"  ⚠️  周报编辑 {api['name']} 返回 {resp.status_code}，尝试下一个")
                continue
            text = resp.json()['choices'][0]['message']['content'].strip()
            text = re.sub(r'^```(?:json)?\s*', '', text).strip().rstrip('`').strip()
            data = json.loads(text)
            mainline = (data.get('mainline') or '').strip()
            editorial_title = (data.get('editorial_title') or '').strip()
            tmap = {t.get('key'): (t.get('narrative') or '').strip() for t in data.get('themes') or []}
            titles = {t.get('key'): (t.get('theme_title') or '').strip() for t in data.get('themes') or []}
            if len(mainline) < 20 or not tmap:
                print(f"  ⚠️  周报编辑 {api['name']} 结果不完整，尝试下一个")
                continue
            result = {'editorial_title': editorial_title, 'mainline': mainline, 'themes': tmap, 'theme_titles': titles}
            if cache_key and input_hash:
                _editorial_cache_put(cache_key, input_hash, result, api['name'])
            print(f"  📝 周报编辑已生成（{api['name']}，{len(tmap)} 个主题导读）: {mainline[:40]}...")
            return result
        except Exception as e:
            print(f"  ⚠️  周报编辑 {api['name']} 失败: {type(e).__name__}")
            continue
    if stale:
        print(f"  ⚠️  AI 编辑全通道失败，沿用上一版缓存导读（{cache_key}）")
        return stale
    return None

def build_monthly_editorial(trends, period_id, cache_key=None):
    """AI 编辑层：把月报趋势写成月度标题与结构变化导读。失败优先沿用上一版缓存（fail-stale），无缓存返回 None。"""
    if not trends:
        return None
    try:
        from providers import llm as _llm
    except ImportError:
        try:
            from scripts.providers import llm as _llm
        except ImportError:
            return None
    # 走模块引用而非 `from X import f`：后者是值绑定，测试 patch
    # providers.llm._post_chat 会静默失效，表现为「补丁写了却在调真实 API」。
    apis = _llm._chat_api_candidates()
    if not apis:
        return None

    # 任务形状路由：编辑层低频长输出，优先走实测最快最稳的 DeepSeek 官方；方舟留给事件分析主链
    apis = sorted(apis, key=lambda a: 0 if a.get('id') == 'deepseek' else 1)

    trend_brief = []
    for t in trends:
        evs = [e.get('title', '') for e in (t.get('evidence') or [])][:4]
        trend_brief.append({
            'key': t.get('key', ''),
            'title': t.get('title') or t.get('name', ''),
            'change': t.get('change', ''),
            'region': t.get('region', ''),
            'summary': t.get('summary', ''),
            'week_count': t.get('week_count', 0),
            'count': t.get('count', 0),
            'previous_count': t.get('previous_count', 0),
            'evidence_titles': evs,
        })

    input_hash = None
    stale = None
    if cache_key:
        input_hash = _editorial_input_hash(trend_brief, 'monthly')
        exact, stale = _editorial_cache_get(cache_key, input_hash)
        if exact:
            print(f"  📋 月报编辑命中缓存（{period_id}）: {(exact.get('mainline') or '')[:30]}...")
            return exact

    prompt = render_prompt('editorial-monthly', {
        'period_id': period_id,
        'trend_brief': json.dumps(trend_brief, ensure_ascii=False, indent=2),
    })

    for api in apis:
        try:
            # 每通道单发：超时说明该通道当前干不了这活，等更久只会放大总时长，直接换下一通道
            resp = _llm._post_chat(api, prompt, max_tokens=1700, temperature=0.3, timeout=(10, 60))
            if resp.status_code != 200:
                print(f"  ⚠️  月报编辑 {api['name']} 返回 {resp.status_code}，尝试下一个")
                continue
            text = resp.json()['choices'][0]['message']['content'].strip()
            text = re.sub(r'^```(?:json)?\s*', '', text).strip().rstrip('`').strip()
            data = json.loads(text)
            mainline = (data.get('mainline') or '').strip()
            editorial_title = (data.get('editorial_title') or '').strip()
            tmap = {}
            titles = {}
            for t in data.get('themes') or []:
                key = t.get('key')
                if not key:
                    continue
                tmap[key] = {
                    'narrative': (t.get('narrative') or '').strip(),
                    'drivers': [str(d).strip() for d in (t.get('drivers') or []) if str(d).strip()][:3],
                    'uncertainty': (t.get('uncertainty') or '').strip(),
                    'next_validation': (t.get('next_validation') or '').strip(),
                }
                if (t.get('theme_title') or '').strip():
                    titles[key] = (t.get('theme_title') or '').strip()
            if len(mainline) < 30 or not tmap:
                print(f"  ⚠️  月报编辑 {api['name']} 结果不完整，尝试下一个")
                continue
            result = {'editorial_title': editorial_title, 'mainline': mainline, 'themes': tmap, 'theme_titles': titles}
            if cache_key and input_hash:
                _editorial_cache_put(cache_key, input_hash, result, api['name'])
            print(f"  📝 月报编辑已生成（{api['name']}，{len(tmap)} 个趋势导读）: {mainline[:40]}...")
            return result
        except Exception as e:
            print(f"  ⚠️  月报编辑 {api['name']} 失败: {type(e).__name__}")
            continue
    if stale:
        print(f"  ⚠️  AI 编辑全通道失败，沿用上一版缓存导读（{cache_key}）")
        return stale
    return None
