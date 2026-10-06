"""Tests for AI scoring hard constraints in prompt (10 稀缺/亏损强制/1-3 刻度).

P1 后提示词已从 fetch_news 内联常量迁移到 scripts/prompts/*.md，
因此这里改为从 prompt 文件读取，语义不变（仍断言硬约束存在于提示词中）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prompt_loader import load_prompt  # noqa: E402


class TestScoreHardConstraints(unittest.TestCase):
    def test_prompt_has_scarcity_and_bounds(self):
        p = load_prompt('analysis-system')
        for token in ['10分仅限稀缺', '封顶9', '7-9分（强制', '禁止给4-5', '1-3分', '分数从1开始']:
            self.assertIn(token, p, f'missing: {token}')

    def test_examples_cover_loss_high_and_minor_low(self):
        ex = load_prompt('analysis-examples')
        self.assertIn('Zaggle', ex)          # 亏损强制高分档示例
        self.assertIn('"score":8', ex)
        self.assertIn('X adds video overlays', ex)  # 微小事件低分示例
        self.assertIn('"score":3', ex)

    def test_examples_still_carry_fingerprint_fields(self):
        ex = load_prompt('analysis-examples')
        self.assertIn('canonical_company', ex)
        self.assertIn('canonical_key', ex)


if __name__ == '__main__':
    unittest.main()
