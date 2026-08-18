#!/bin/bash
mkdir -p /logs/verifier
got="$(head -c 100 /app/canary.txt 2>/dev/null | tr -d '[:space:]')"
if [ "$got" = "canary" ]; then
  echo "PASS /app/canary.txt says canary"
  echo 1 > /logs/verifier/reward.txt
else
  echo "FAIL /app/canary.txt missing or wrong: '${got}'"
  echo 0 > /logs/verifier/reward.txt
fi
