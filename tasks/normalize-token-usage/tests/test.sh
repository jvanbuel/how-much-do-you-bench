#!/bin/bash
# Grade the agent's /app workspace with the exercise's hidden pytest tests.
# Writes 1 to reward.txt iff every grader test passes, else 0 (same contract as
# the polyglot verifier). reward.txt is the grade, so always exit 0.
set -u
mkdir -p /logs/verifier /logs/artifacts

export BENCH_TARGET=/app
export PYTHONPATH=/tests/grader_lib

cd /app
# -rA surfaces captured output of passed tests too (e.g. exercise 08's plot-diff
# score), so it lands in the verifier log, not just on failure.
python -m pytest /tests/grader -rA > /logs/verifier/pytest.log 2>&1
status=$?
cat /logs/verifier/pytest.log

# Persist the agent's answer for post-hoc inspection.
cp -r /app/*.py /app/*.tf /app/templates /logs/artifacts/ 2>/dev/null || true

if [ "$status" -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit 0
