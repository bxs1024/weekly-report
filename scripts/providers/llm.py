"""AI 通道与调用：四个模型供应商的配置、结构化分析、_post_chat 统一出口。

架构契约「页面不调模型」：本模块是唯一发起付费 AI 请求的地方，调用方一律
直连本模块（不要走 fetch_news 转发层，否则 monkeypatch 会静默打空）。"""

# AI 通道从 os.environ 取 API key。搬出前，调用方是靠「import fetch_news」顺带
# 触发 load_dotenv() 的；这条隐式依赖一断，_chat_api_candidates() 会返回空，
# AI 分支整段跳过——表现为「补丁明明生效但合并数为 0」的静默偏差。故本模块
# 自己加载（load_dotenv 幂等，多处调用无害）。
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import json
import os
import re
import time

import requests

try:
    from content.util import _is_http_url
    from prompt_loader import load_prompt, prompt_version
except ImportError:
    from scripts.content.util import _is_http_url
    from scripts.prompt_loader import load_prompt, prompt_version

# DeepSeek/豆包均为国内 API，直连即可；trust_env=False 忽略系统代理（含 ALL_PROXY），
# 避免依赖 socks 库且更快。所有 AI 通道共用此 session（新闻抓取仍走系统代理，不受影响）。
_LLM_SESSION = requests.Session()
_LLM_SESSION.trust_env = False

# 总闸：AI 请求的最终开关。只决定「发不发出去」，不改变任何逻辑分支——
# 关掉后 _chat_api_candidates() 返回空，等价于没配 key。
AI_CALLS_ENABLED_ENV = 'AI_CALLS_ENABLED'


def ai_calls_enabled():
    """没设或设成 true/1/yes/on 时开启；显式关掉则所有 AI 通道一律不发请求。"""
    raw = (os.environ.get(AI_CALLS_ENABLED_ENV) or '').strip().lower()
    if raw == '':
        return True
    return raw in ('1', 'true', 'yes', 'on')


def configure_minimax():
    """配置 MiniMax API，优先使用"""
    key = os.environ.get('MINIMAX_API_KEY')
    model = os.environ.get('MINIMAX_MODEL', 'MiniMax-M2.7')
    print(f"  🔑 MINIMAX_API_KEY: {'已设置 (' + str(len(key)) + ' 字符)' if key else '未设置 ❌'}")
    if not key:
        print("  ⚠️  未设置 MINIMAX_API_KEY，将降级使用豆包")
        return False
    if len(key) < 10:
        print(f"  ❌ MINIMAX_API_KEY 长度异常（{len(key)} 字符），降级使用豆包")
        return False
    print(f"  ✅ MiniMax API 配置检查通过，模型: {model}")
    return True

def analyze_events_minimax(items):
    """
    使用 MiniMax 大模型分析新闻事件（OpenAI 兼容格式）
    模型: MiniMax-Text-01
    """
    import os
    api_key = os.environ.get('MINIMAX_API_KEY')
    if not api_key:
        print("  ⚠️  未设置 MINIMAX_API_KEY")
        return None

    url = "https://api.minimax.chat/v1/text/chatcompletion_v2"
    model = os.environ.get('MINIMAX_MODEL', 'MiniMax-M2.7')
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json"
    }

    news = [{'title': it['title'], 'url': it['url'], 'source': it['source'], 'region': it.get('region','')} for it in items]
    prompt = build_analysis_prompt(news)

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
        "temperature": 0.1
    }

    # 创建不使用代理的session（MiniMax不需要代理）
    session = requests.Session()
    session.trust_env = False  # 禁用环境变量代理

    for attempt in range(3):
        try:
            resp = session.post(url, headers=headers, json=payload, timeout=60)
            if resp.status_code == 429:
                wait = (attempt + 1) * 10
                print(f"  ⚠️  MiniMax API 配额耗尽（429），等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            if resp.status_code == 400:
                print(f"  ⚠️  MiniMax API 请求错误（400）: {resp.text[:200]}，尝试降级...")
                return None
            if resp.status_code != 200:
                print(f"  ❌ MiniMax API HTTP {resp.status_code}: {resp.text[:300]}")
                return None
            data = resp.json()
            text = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            if not text:
                print("  ⚠️  MiniMax 返回空内容: " + str(data))
                return None
            for m in ['```json', '```']:
                if m in text:
                    parts = text.split(m)
                    for p in parts[1:]:
                        text = p.strip()
                        if text.endswith('```'):
                            text = text[:-3].strip()
                        break
                    break
            result = json.loads(re.sub(r'^json\s*', '', text, flags=re.I))
            if isinstance(result, list):
                result = [r for r in result if _is_http_url(r.get('url')) and r.get('summary_short')]
            print(f"  ✅ MiniMax 分析成功，{len(result) if isinstance(result, list) else 0} 条")
            return result
        except requests.exceptions.Timeout:
            print(f"  ⚠️  MiniMax API 超时（60s），快速失败，跳过该批次")
            return None
        except json.JSONDecodeError as e:
            if attempt < 2:
                wait = (attempt + 1) * 5
                print(f"  ⚠️  MiniMax 返回非JSON，尝试修正解析...")
                import re as re2
                match = re2.search(r'\[[\s\S]*\]', text if 'text' in dir() else '')
                if match:
                    try:
                        result = json.loads(match.group())
                        result = [r for r in result if isinstance(r, dict) and _is_http_url(r.get('url'))]
                        if result:
                            print(f"  ✅ 修正解析成功，提取 {len(result)} 条")
                            return result
                    except: pass
                print(f"  解析失败，等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            print(f"  ❌ MiniMax JSON 解析最终失败")
            return None
        except Exception as e:
            if attempt < 2:
                wait = (attempt + 1) * 5
                print(f"  ⚠️  MiniMax API 调用失败（{type(e).__name__}），等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            print(f"  ❌ MiniMax API 最终失败: {type(e).__name__} {str(e)[:200]}")
            return None
    return None

def configure_doubao():
    key = os.environ.get('DOUBAO_API_KEY')
    model = os.environ.get('DOUBAO_MODEL', 'ep-20260409223830-dnt5b')
    print(f"  🔑 DOUBAO_API_KEY: {'已设置 (' + str(len(key)) + ' 字符)' if key else '未设置 ❌'}")
    if not key:
        print("  ❌ 未找到 DOUBAO_API_KEY，跳过 AI 分析")
        return False
    if len(key) < 10:
        print(f"  ❌ DOUBAO_API_KEY 长度异常（{len(key)} 字符），跳过 AI 分析")
        return False
    print(f"  ✅ 豆包 API 配置检查通过，模型: {model}")
    return True

def configure_deepseek():
    """配置 DeepSeek API，优先使用"""
    key = os.environ.get('DEEPSEEK_API_KEY')
    model = os.environ.get('DEEPSEEK_MODEL', 'deepseek-chat')
    print(f"  🔑 DEEPSEEK_API_KEY: {'已设置 (' + str(len(key)) + ' 字符)' if key else '未设置 ❌'}")
    if not key:
        print("  ⚠️  未设置 DEEPSEEK_API_KEY，将降级使用豆包")
        return False
    if len(key) < 10:
        print(f"  ❌ DEEPSEEK_API_KEY 长度异常（{len(key)} 字符），降级使用豆包")
        return False
    print(f"  ✅ DeepSeek API 配置检查通过，模型: {model}")
    return True

def configure_ark():
    """配置火山方舟 DeepSeek V4 Flash（ARK），价格约为官方 DeepSeek 的 1/6"""
    key = os.environ.get('ARK_API_KEY')
    model = os.environ.get('ARK_MODEL') or 'ep-20260827101830-qgtm4'
    print(f"  🔑 ARK_API_KEY: {'已设置 (' + str(len(key)) + ' 字符)' if key else '未设置 ❌'}")
    if not key:
        print("  ⚠️  未设置 ARK_API_KEY，跳过方舟 V4 Flash")
        return False
    if len(key) < 10:
        print(f"  ❌ ARK_API_KEY 长度异常（{len(key)} 字符），跳过方舟 V4 Flash")
        return False
    print(f"  ✅ 方舟 V4 Flash 配置检查通过，模型: {model}")
    return True

def analyze_events_ark(items):
    """
    使用火山方舟 DeepSeek V4 Flash 分析新闻事件（OpenAI 兼容 API）
    模型：ep-20260827101830-qgtm4（方舟价格约为官方 1/6）
    """
    api_key = os.environ.get('ARK_API_KEY')
    if not api_key:
        print("  ⚠️  未设置 ARK_API_KEY")
        return None

    url = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    model = os.environ.get('ARK_MODEL') or 'ep-20260827101830-qgtm4'
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json"
    }

    news = [{'title': it['title'], 'url': it['url'], 'source': it['source'], 'region': it.get('region','')} for it in items]
    prompt = build_analysis_prompt(news)

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
        "temperature": 0.1,
        "thinking": {"type": "disabled"},  # 关深度思考必须用 thinking 参数（reasoning:{effort:none} 会被静默忽略），降延迟、省 reasoning 计费
    }

    for attempt in range(2):
        try:
            # 关思考后响应快；60s 余量覆盖网络波动与长正文生成
            resp = _LLM_SESSION.post(url, headers=headers, json=payload, timeout=(10, 60))
            if resp.status_code == 429:
                wait = (attempt + 1) * 10
                print("  ⚠️  方舟 API 配额耗尽（429），等待 " + str(wait) + "s 后重试...")
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                print(f"  ❌ 方舟 API HTTP {resp.status_code}: {resp.text[:300]}")
                return None
            data = resp.json()
            text = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            if not text:
                print("  ⚠️  方舟返回空内容: " + str(data))
                return None
            for m in ['```json', '```']:
                if m in text:
                    parts = text.split(m)
                    for p in parts[1:]:
                        text = p.strip()
                        if text.endswith('```'):
                            text = text[:-3].strip()
                        break
                    break
            result = json.loads(re.sub(r'^json\s*', '', text, flags=re.I))
            if isinstance(result, list):
                result = [r for r in result if _is_http_url(r.get('url')) and r.get('summary_short')]
            return result
        except requests.exceptions.Timeout:
            print(f"  ⚠️  方舟 API 超时（30s），快速失败，跳过该批次")
            return None
        except json.JSONDecodeError as e:
            if attempt < 2:
                wait = (attempt + 1) * 5
                print(f"  ⚠️  方舟返回非JSON，尝试修正解析...")
                import re as re2
                match = re2.search(r'\[[\s\S]*\]', text if 'text' in dir() else '')
                if match:
                    try:
                        result = json.loads(match.group())
                        result = [r for r in result if isinstance(r, dict) and _is_http_url(r.get('url'))]
                        if result:
                            print(f"  ✅ 修正解析成功，提取 {len(result)} 条")
                            return result
                    except: pass
                print(f"  解析失败，等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            print(f"  ❌ 方舟 JSON 解析最终失败")
            return None
        except Exception as e:
            if attempt < 2:
                wait = (attempt + 1) * 5
                print(f"  ⚠️  方舟 API 调用失败（{type(e).__name__}），等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            print(f"  ❌ 方舟 API 最终失败: {type(e).__name__} {str(e)[:200]}")
            return None
    return None

def analyze_events_deepseek(items):
    """
    使用 DeepSeek 大模型分析新闻事件（OpenAI 兼容 API）
    模型：deepseek-chat
    """
    api_key = os.environ.get('DEEPSEEK_API_KEY')
    if not api_key:
        print("  ⚠️  未设置 DEEPSEEK_API_KEY")
        return None

    url = "https://api.deepseek.com/v1/chat/completions"
    model = os.environ.get('DEEPSEEK_MODEL', 'deepseek-chat')
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json"
    }

    news = [{'title': it['title'], 'url': it['url'], 'source': it['source'], 'region': it.get('region','')} for it in items]
    prompt = build_analysis_prompt(news)

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
        "temperature": 0.1
    }

    for attempt in range(2):
        try:
            resp = _LLM_SESSION.post(url, headers=headers, json=payload, timeout=(10, 20))
            if resp.status_code == 429:
                wait = (attempt + 1) * 10
                print("  ⚠️  DeepSeek API 配额耗尽（429），等待 " + str(wait) + "s 后重试...")
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                print(f"  ❌ DeepSeek API HTTP {resp.status_code}: {resp.text[:300]}")
                return None
            data = resp.json()
            text = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            if not text:
                print("  ⚠️  DeepSeek 返回空内容: " + str(data))
                return None
            for m in ['```json', '```']:
                if m in text:
                    parts = text.split(m)
                    for p in parts[1:]:
                        text = p.strip()
                        if text.endswith('```'):
                            text = text[:-3].strip()
                        break
                    break
            result = json.loads(re.sub(r'^json\s*', '', text, flags=re.I))
            if isinstance(result, list):
                result = [r for r in result if _is_http_url(r.get('url')) and r.get('summary_short')]
            return result
        except requests.exceptions.Timeout:
            print(f"  ⚠️  DeepSeek API 超时（30s），快速失败，跳过该批次")
            return None
        except json.JSONDecodeError as e:
            if attempt < 2:
                wait = (attempt + 1) * 5
                print(f"  ⚠️  DeepSeek 返回非JSON，尝试修正解析...")
                import re as re2
                match = re2.search(r'\[[\s\S]*\]', text if 'text' in dir() else '')
                if match:
                    try:
                        result = json.loads(match.group())
                        result = [r for r in result if isinstance(r, dict) and _is_http_url(r.get('url'))]
                        if result:
                            print(f"  ✅ 修正解析成功，提取 {len(result)} 条")
                            return result
                    except: pass
                print(f"  解析失败，等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            print(f"  ❌ DeepSeek JSON 解析最终失败")
            return None
        except Exception as e:
            if attempt < 2:
                wait = (attempt + 1) * 5
                print(f"  ⚠️  DeepSeek API 调用失败（{type(e).__name__}），等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            print(f"  ❌ DeepSeek API 最终失败: {type(e).__name__} {str(e)[:200]}")
            return None
    return None

ANALYSIS_PROMPT_FILES = ('analysis-system', 'analysis-examples')

def build_analysis_prompt(news):
    """组装一次事件分析的完整 prompt。提示词文本来自 scripts/prompts/。

    段落间隔与提示词外置前的拼接结果逐字节一致（见下方注释），
    调整时必须同步核对，避免悄悄改变喂给模型的分段结构。

    外置前源码为：
        AI_SYSTEM_PROMPT + "\\n" + AI_EXAMPLES
        + "\\n\\n分析以下事件，返回JSON数组：\\n" + json + "\\n\\n返回JSON："
    其中 AI_EXAMPLES 常量以换行开头、以换行结尾，因此净间隔是：
        system 与 examples 之间 2 个换行；examples 与说明之间 3 个换行。
    本函数直接写净效果，不依赖提示词文件的首尾空行。
    """
    system = load_prompt('analysis-system')
    examples = load_prompt('analysis-examples')
    return (system + "\n\n" + examples
            + "\n\n\n分析以下事件，返回JSON数组：\n"
            + json.dumps(news, ensure_ascii=False) + "\n\n返回JSON：")

def analysis_prompt_versions():
    """当前分析提示词版本，用于 run_metrics 审计与缓存键。"""
    return {name: prompt_version(name) for name in ANALYSIS_PROMPT_FILES}

def analyze_events_doubao(items):
    """
    使用豆包大模型分析新闻事件（OpenAI 兼容 API）
    模型：doubao-pro-32k
    """
    import os
    api_key = os.environ.get('DOUBAO_API_KEY')
    if not api_key:
        print("  ⚠️  未设置 DOUBAO_API_KEY，降级跳过 AI 分析")
        return None

    url = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    model = os.environ.get('DOUBAO_MODEL', 'ep-20260409223830-dnt5b')
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json"
    }

    news = [{'title': it['title'], 'url': it['url'], 'source': it['source'], 'region': it.get('region','')} for it in items]
    prompt = build_analysis_prompt(news)

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
        "temperature": 0.1
    }

    for attempt in range(2):  # 最多重试1次（快速降级到程序生成）
        try:
            resp = _LLM_SESSION.post(url, headers=headers, json=payload, timeout=(10, 90))  # 90s 超时（给冷启动留足时间）
            if resp.status_code == 429:
                wait = (attempt + 1) * 10
                print("  ⚠️  豆包 API 配额耗尽（429），等待 " + str(wait) + "s 后重试...")
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                print(f"  ❌ 豆包 API HTTP {resp.status_code}: {resp.text[:300]}")
                return None
            data = resp.json()
            text = data.get('choices', [{}])[0].get('message', {}).get('content', '')
            if not text:
                print("  ⚠️  豆包返回空内容: " + str(data))
                return None
            for m in ['```json', '```']:
                if m in text:
                    parts = text.split(m)
                    for p in parts[1:]:
                        text = p.strip()
                        if text.endswith('```'):
                            text = text[:-3].strip()
                        break
                    break
            result = json.loads(re.sub(r'^json\s*', '', text, flags=re.I))
            if isinstance(result, list):
                result = [r for r in result if _is_http_url(r.get('url')) and r.get('summary_short')]
            return result
        except requests.exceptions.Timeout:
            if attempt < 1:  # 重试一次（网络抖动场景）
                wait = (attempt + 1) * 3
                print(f"  ⚠️  豆包 API 超时，等待 {wait}s 后重试（第 {attempt+1}/2 次）...")
                time.sleep(wait)
                continue
            print(f"  ⚠️  豆包 API 超时，重试耗尽，跳过该批次")
            return None
        except json.JSONDecodeError as e:
            if attempt < 2:
                wait = (attempt + 1) * 5
                print(f"  ⚠️  豆包返回非JSON，尝试修正解析...")
                import re as re2
                match = re2.search(r'\[[\s\S]*\]', text if 'text' in dir() else '')
                if match:
                    try:
                        result = json.loads(match.group())
                        result = [r for r in result if isinstance(r, dict) and _is_http_url(r.get('url'))]
                        if result:
                            print(f"  ✅ 修正解析成功，提取 {len(result)} 条")
                            return result
                    except: pass
                print(f"  解析失败，等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            print(f"  ❌ 豆包 JSON 解析最终失败")
            return None
        except Exception as e:
            if attempt < 2:
                wait = (attempt + 1) * 5
                print(f"  ⚠️  豆包 API 调用失败（{type(e).__name__}），等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            print(f"  ❌ 豆包 API 最终失败: {type(e).__name__} {str(e)[:200]}")
            return None
    return None

def analyze_single_event_minimax(item):
    """单条事件分析（MiniMax批次失败时的兜底）"""
    try:
        result = analyze_events_minimax([item])
        return result
    except Exception:
        return None

def analyze_single_event_doubao(item):
    """单条事件分析（豆包批次失败时的兜底）"""
    # 单条分析也有30s timeout，不等待
    try:
        result = analyze_events_doubao([item])
        return result
    except Exception:
        return None

def _results_by_url(results):
    if not isinstance(results, list):
        return {}
    return {
        r.get('url'): r
        for r in results
        if isinstance(r, dict) and _is_http_url(r.get('url'))
    }

def _chat_api_candidates():
    """Return AI chat APIs in priority order: 方舟 V4 Flash primary, DeepSeek, Doubao fallback.

    总闸 `AI_CALLS_ENABLED` 关掉时返回空列表——与「一个 key 都没配」完全同路，
    调用方各自的降级路径自然接管。这是全部付费请求的唯一决策点：任何 AI 调用
    都要先从这里拿到 api，拿不到就发不出去。
    """
    if not ai_calls_enabled():
        return []
    apis = []
    ark_key = os.environ.get('ARK_API_KEY', '')
    if ark_key and len(ark_key) >= 10:
        apis.append({
            'id': 'ark',
            'name': '方舟 V4 Flash',
            'url': 'https://ark.cn-beijing.volces.com/api/v3/chat/completions',
            'key': ark_key,
            'model': os.environ.get('ARK_MODEL') or 'ep-20260827101830-qgtm4',
        })
    ds_key = os.environ.get('DEEPSEEK_API_KEY', '')
    if ds_key and len(ds_key) >= 10:
        apis.append({
            'id': 'deepseek',
            'name': 'DeepSeek',
            'url': 'https://api.deepseek.com/v1/chat/completions',
            'key': ds_key,
            'model': os.environ.get('DEEPSEEK_MODEL', 'deepseek-chat'),
        })
    db_key = os.environ.get('DOUBAO_API_KEY', '')
    if db_key and len(db_key) >= 10:
        apis.append({
            'id': 'doubao',
            'name': '豆包',
            'url': 'https://ark.cn-beijing.volces.com/api/v3/chat/completions',
            'key': db_key,
            'model': os.environ.get('DOUBAO_MODEL', 'ep-20260409223830-dnt5b'),
        })
    return apis

def _post_chat(api, prompt, max_tokens=1024, temperature=0.1, timeout=(10, 20)):
    payload = {
        "model": api['model'],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if api.get('id') == 'ark':
        # 方舟 DeepSeek-V4-Flash 关深度思考必须用 thinking 参数（实测 reasoning:{effort:none} 会被静默忽略，思考 token 仍产生）
        payload['thinking'] = {"type": "disabled"}
    headers = {
        "Authorization": "Bearer " + api['key'],
        "Content-Type": "application/json"
    }
    return _LLM_SESSION.post(api['url'], headers=headers, json=payload, timeout=timeout)
