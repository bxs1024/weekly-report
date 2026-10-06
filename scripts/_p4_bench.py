import cProfile
import io
import os
import pstats
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_news
import generate_html as G

fetch_news._chat_api_candidates = lambda: []
G._editorial_cache_get = lambda *a, **k: (None, None)
G._editorial_cache_put = lambda *a, **k: None

t0 = time.time()
pr = cProfile.Profile()
pr.enable()
ctx = G.build_display_context()
pr.disable()
print(f'build_display_context: {time.time() - t0:.1f}s')

s = io.StringIO()
ps = pstats.Stats(pr, stream=s).sort_stats('cumulative')
ps.print_stats(18)
print(s.getvalue())

info = fetch_news._entity_key_info_cached.cache_info()
print('entity_key_info cache:', info)

n = len(ctx['all_events_for_list'])
print(f'all_events_for_list: {n} 条')
