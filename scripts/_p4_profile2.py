import faulthandler
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TRACE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.data', 'p4_trace2.log')
os.makedirs(os.path.dirname(TRACE), exist_ok=True)
fh = open(TRACE, 'w', encoding='utf-8')

t0 = time.time()


def mark(msg):
    print(f'[{time.time() - t0:7.1f}s] {msg}', flush=True)


mark('import ...')
import fetch_news
import providers.llm
import generate_html as G
mark('imported')

providers.llm._chat_api_candidates = lambda: []  # AI 通道已搬到 providers.llm；打 fetch_news 转发层是值绑定，会静默失效
G._editorial_cache_get = lambda *a, **k: (None, None)
G._editorial_cache_put = lambda *a, **k: None

mark('build_display_context() ...')
ctx = G.build_display_context()
mark(f'context built keys={len(ctx)}')

# 从此刻起每 30s 打一次栈，定位 generate_html 内部卡点
faulthandler.dump_traceback_later(30, repeat=True, file=fh)
mark('generate_html(force, preview) ...')
G.generate_html(force=True, preview_mode=True)
faulthandler.cancel_dump_traceback_later()
mark('done')
fh.close()
