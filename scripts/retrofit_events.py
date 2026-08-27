"""
存量事件描述补跑脚本
扫描 events.json 中 reason 泛化的事件，调用 DeepSeek/豆包改写后写回。

用法：
  py -3 scripts/retrofit_events.py              # 补跑所有日期
  py -3 scripts/retrofit_events.py --preview     # 预览模式：只看不改
  py -3 scripts/retrofit_events.py --after-html  # 补跑后重新生成 HTML

API 选择（自动检测）：
  DEEPSEEK_API_KEY 优先（本地开发），DOUBAO_API_KEY 降级（GHA 环境）
"""
import json, os, sys, re, time
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

GENERIC_PATTERNS = [
    '科技动态', '有新动态', '战略调整', '融资事件', '并购/收购',
    '财报披露', '金额待确认', '完成融资', '达成并购',
    '战略新动向', '战略动态', '科技行业动态', '科技公司',
    '科技企业',
]


def load_events(path='data/events.json'):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def save_events(data, path='data/events.json'):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def find_generic_events(data):
    """找出所有 reason 泛化的事件，返回 [(date_key, index, event), ...]"""
    targets = []
    for date_key, events in data.items():
        for i, ev in enumerate(events):
            reason = ev.get('reason', '')
            if any(p in reason for p in GENERIC_PATTERNS):
                targets.append((date_key, i, ev))
    return targets




def _pick_api():
    """自动选择可用 API，优先方舟 V4 Flash → DeepSeek（本地）→ 豆包（GHA）"""
    ark_key = os.environ.get('ARK_API_KEY', '')
    if ark_key and len(ark_key) >= 10:
        return {
            'url': 'https://ark.cn-beijing.volces.com/api/v3/chat/completions',
            'key': ark_key,
            'model': os.environ.get('ARK_MODEL') or 'ep-20260827101830-qgtm4',
            'name': '方舟 V4 Flash',
        }
    ds_key = os.environ.get('DEEPSEEK_API_KEY', '')
    if ds_key and len(ds_key) >= 10:
        return {
            'url': 'https://api.deepseek.com/chat/completions',
            'key': ds_key,
            'model': os.environ.get('DEEPSEEK_MODEL', 'deepseek-chat'),
            'name': 'DeepSeek',
        }
    db_key = os.environ.get('DOUBAO_API_KEY', '')
    if db_key and len(db_key) >= 10:
        return {
            'url': 'https://ark.cn-beijing.volces.com/api/v3/chat/completions',
            'key': db_key,
            'model': os.environ.get('DOUBAO_MODEL', 'ep-20260409223830-dnt5b'),
            'name': '豆包',
        }
    return None


def rewrite_batch(batch, api):
    """调用 AI 改写一批泛化描述，返回 {url: new_reason} 映射"""
    items = [
        {'url': e['url'], 'title': e['title'], 'region': e.get('region', ''),
         'type': (e.get('event_types') or ['other'])[0]}
        for _, _, e in batch
    ]

    prompt = f"""为以下科技新闻事件各写一句简短的中文描述（20字以内），格式为"[地区][公司名][具体动作]"。
要求：必须从标题提取公司名/产品名，描述具体做了什么。禁止出现"融资""并购""财报"等泛化词。
只返回JSON数组，每个元素包含"url"和"reason"字段。

{json.dumps(items, ensure_ascii=False)}

返回JSON："""

    headers = {
        "Authorization": "Bearer " + api['key'],
        "Content-Type": "application/json",
    }
    payload = {
        "model": api['model'],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1024,
        "temperature": 0.1,
    }

    for attempt in range(2):
        try:
            import requests
            session = requests.Session()
            session.trust_env = False  # 禁用代理（local SOCKS 干扰）
            resp = session.post(api['url'], headers=headers, json=payload,
                                timeout=(10, 30))
            if resp.status_code != 200:
                print(f"  HTTP {resp.status_code}: {resp.text[:200]}")
                if attempt == 0:
                    time.sleep(2)
                    continue
                return None
            text = resp.json()['choices'][0]['message']['content']
            for m in ['```json', '```']:
                if m in text:
                    parts = text.split(m)
                    text = parts[1] if len(parts) > 1 else text
                    if text.endswith('```'):
                        text = text[:-3].strip()
                    break
            results = json.loads(re.sub(r'^json\s*', '', text, flags=re.I))
            if not isinstance(results, list):
                print(f"  返回非列表，跳过")
                return None
            mapping = {}
            for r in results:
                url = r.get('url', '')
                new_reason = r.get('reason', '')
                if url and new_reason and len(new_reason) >= 8:
                    mapping[url] = new_reason
            return mapping
        except Exception as e:
            if attempt == 0:
                print(f"  重试（{type(e).__name__}）...")
                time.sleep(2)
                continue
            print(f"  失败: {type(e).__name__}: {str(e)[:120]}")
            return None
    return None


def main():
    preview = '--preview' in sys.argv
    after_html = '--after-html' in sys.argv

    data = load_events()
    targets = find_generic_events(data)

    if not targets:
        print("✅ 没有发现泛化描述事件")
        return

    print(f"📋 发现 {len(targets)} 条泛化描述事件，涉及 {len(set(t[0] for t in targets))} 天")

    if preview:
        print("\n--- 预览（前 20 条）---")
        for date_key, _, ev in targets[:20]:
            print(f"  [{date_key}] {ev.get('title','')[:50]}")
            print(f"    reason: {ev.get('reason','')[:60]}")
        print(f"\n  共 {len(targets)} 条，--preview 模式未做任何修改")
        return

    api = _pick_api()
    if not api:
        print("❌ 未检测到可用的 API Key（需设置 DEEPSEEK_API_KEY 或 DOUBAO_API_KEY）")
        sys.exit(1)

    print(f"🔑 使用 {api['name']}（{api['model']}）开始补跑...")

    total_rewritten = 0
    for i in range(0, len(targets), 20):
        batch = targets[i:i+20]
        batch_num = (i // 20) + 1
        total_batches = (len(targets) + 19) // 20

        print(f"  批次 {batch_num}/{total_batches}（{len(batch)} 条）...")
        mapping = rewrite_batch(batch, api)
        if not mapping:
            print(f"  批次 {batch_num}/{total_batches} 失败，跳过")
            continue

        batch_hits = 0
        for date_key, idx, ev in batch:
            url = ev['url']
            if url in mapping:
                old_reason = data[date_key][idx]['reason']
                data[date_key][idx]['reason'] = mapping[url]
                if data[date_key][idx].get('summary_short', '')[:25] == ev.get('title', '')[:25]:
                    data[date_key][idx]['summary_short'] = mapping[url]
                batch_hits += 1

        total_rewritten += batch_hits
        print(f"  批次 {batch_num}/{total_batches} ✅ {batch_hits}/{len(batch)} 条已改写")
        time.sleep(0.5)

    if total_rewritten == 0:
        print("❌ 没有事件被成功改写")
        sys.exit(1)

    save_events(data)
    print(f"\n✅ 补跑完成：{total_rewritten}/{len(targets)} 条已改写并保存到 events.json")

    if after_html:
        print("\n🔄 重新生成 HTML...")
        from generate_html import main as gen_main
        sys.argv = ['generate_html.py', '--force']
        gen_main()
        print("✅ HTML 已重新生成")


if __name__ == '__main__':
    main()
