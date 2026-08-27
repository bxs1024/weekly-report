"""Tests for _chat_api_candidates ark-first fallback chain (方案②: 方舟→DeepSeek→豆包)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch_news  # noqa: E402

ENV_KEYS = ('ARK_API_KEY', 'ARK_MODEL', 'DEEPSEEK_API_KEY', 'DOUBAO_API_KEY')


class TestChatApiCandidates(unittest.TestCase):
    def setUp(self):
        # test_period_report 在模块级启动了对 _chat_api_candidates 的全局 mock
        # （discover 收集阶段即生效且不停靠）；本类要测真函数，须临时解除，结束后还原
        self._guard = None
        tp = sys.modules.get('test_period_report')
        guard = getattr(tp, '_patch_api', None)
        if guard is not None and getattr(guard, 'target', None):
            self._guard = guard
            guard.stop()

    def tearDown(self):
        if self._guard is not None:
            self._guard.start()

    def _set_env(self, env):
        old = {k: os.environ.pop(k, None) for k in ENV_KEYS}
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return old

    def _restore(self, old):
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_ark_first_chain(self):
        old = self._set_env({'ARK_API_KEY': 'ark-1234567890', 'ARK_MODEL': None,
                             'DEEPSEEK_API_KEY': 'ds-1234567890', 'DOUBAO_API_KEY': 'db-1234567890'})
        try:
            apis = fetch_news._chat_api_candidates()
            self.assertEqual([a['id'] for a in apis], ['ark', 'deepseek', 'doubao'])
            self.assertEqual(apis[0]['model'], 'ep-20260827101830-qgtm4')
            self.assertEqual(apis[0]['url'], 'https://ark.cn-beijing.volces.com/api/v3/chat/completions')
        finally:
            self._restore(old)

    def test_no_ark_falls_back_to_deepseek(self):
        old = self._set_env({'ARK_API_KEY': None, 'DEEPSEEK_API_KEY': 'ds-1234567890'})
        try:
            apis = fetch_news._chat_api_candidates()
            self.assertEqual([a['id'] for a in apis], ['deepseek'])
        finally:
            self._restore(old)

    def test_ark_model_override(self):
        old = self._set_env({'ARK_API_KEY': 'ark-1234567890', 'ARK_MODEL': 'ep-custom'})
        try:
            apis = fetch_news._chat_api_candidates()
            self.assertEqual(apis[0]['model'], 'ep-custom')
        finally:
            self._restore(old)


if __name__ == '__main__':
    unittest.main()