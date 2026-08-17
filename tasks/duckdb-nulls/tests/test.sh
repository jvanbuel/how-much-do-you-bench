#!/bin/bash
mkdir -p /logs/verifier
python /tests/check_report.py > /logs/verifier/checks.log 2>&1
status=$?
cat /logs/verifier/checks.log
if [ $status -eq 0 ]; then echo 1 > /logs/verifier/reward.txt; else echo 0 > /logs/verifier/reward.txt; fi
