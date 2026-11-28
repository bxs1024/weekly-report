#!/bin/bash
# 全量离线测试基线批跑：在仓库根目录运行，使用带 jinja2 的解释器。
# 每个测试文件按风格自动选择 runner：
#   - 函数式（模块级 test_*）→ scripts/run_tests.py
#   - unittest.TestCase   → python -m unittest <module>
# 网络依赖测试直接 SKIP。
cd "$(dirname "$0")/.." || exit 1
PY=/c/Users/16120/AppData/Local/Python/bin/python
export PYTHONIOENCODING=utf-8

SKIP="test_google_news.py test_rss.py test_aihot_integration.py"
pass=0; fail=0; skip=0; failed=""

# 账本基线自动顶格：静态下限会过期（11-09 设 145 行，账本涨到 153 行后删一行
# 仍高于下限，校验一声不响）。跑测试前先把下限顶到账本当前值。
# 账本被抹过时命令行返回非零，这里直接算本次批跑失败，不掩盖。
prefail=0
echo "=== 治理账本基线（跑测试前先顶格） ==="
if ! "$PY" scripts/test_governance_ledger.py; then
  prefail=1
fi
echo ""

for f in scripts/test_*.py; do
  b=$(basename "$f")
  if [[ " $SKIP " == *" $b "* ]]; then
    printf "%-42s SKIP(需要网络)\n" "$b"; skip=$((skip+1)); continue
  fi
  start=$(date +%s)
  if grep -q "unittest.TestCase" "$f"; then
    timeout 900 "$PY" -u -m unittest "scripts.${b%.py}" > "/tmp/tr_$b.log" 2>&1
  else
    timeout 900 "$PY" -u scripts/run_tests.py "$f" > "/tmp/tr_$b.log" 2>&1
  fi
  rc=$?
  end=$(date +%s); dur=$((end-start))
  if [ $rc -eq 0 ]; then
    printf "%-42s PASS  %4ds\n" "$b" "$dur"; pass=$((pass+1))
  else
    printf "%-42s FAIL  %4ds (rc=$rc)\n" "$b" "$dur"; fail=$((fail+1)); failed="$failed $b"
  fi
done

echo ""
echo "======== 汇总: PASS=$pass FAIL=$((fail+prefail)) SKIP=$skip ========"
[ $prefail -ne 0 ] && failed="$failed 治理账本基线"
[ -n "$failed" ] && echo "失败:$failed"
[ $((fail+prefail)) -eq 0 ] && exit 0 || exit 1
