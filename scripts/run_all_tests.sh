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
echo "======== 汇总: PASS=$pass FAIL=$fail SKIP=$skip ========"
[ -n "$failed" ] && echo "失败:$failed"
[ $fail -eq 0 ] && exit 0 || exit 1
