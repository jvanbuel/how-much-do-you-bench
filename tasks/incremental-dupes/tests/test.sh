#!/bin/bash
# Replays the pipeline from a clean state, then checks what it produced.
# Dependencies are baked into the environment image, so this needs no network.

mkdir -p /logs/verifier

bash /app/run_pipeline.sh > /logs/verifier/pipeline.log 2>&1 || \
  echo "run_pipeline.sh exited non-zero" >> /logs/verifier/pipeline.log

python -m pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
