FROM hmdyb-task-base:1

# Pinned so the environment is reproducible and the verifier needs no network.
RUN pip install --no-cache-dir \
      dbt-core==1.9.4 \
      dbt-duckdb==1.9.3 \
      duckdb==1.4.1 \
      polars==1.35.1 \
      pytest==8.4.1 \
      pytest-json-ctrf==0.3.5

WORKDIR /app
COPY pipeline/ /app/pipeline/
COPY run_pipeline.sh /app/run_pipeline.sh
COPY generate_data.py /app/generate_data.py
RUN chmod +x /app/run_pipeline.sh

# The feed is generated rather than committed: deterministic from a fixed
# seed, and large enough that reading it tells you nothing.
RUN python /app/generate_data.py

# A git repo, because a harness that curates its own file list has no other way
# to find them: aider's context comes from its repo map, the repo map comes from
# git, and without one it asks the user to paste the code. Cloning the real
# repository here would hand the agent tests/ and solution/solve.sh, so the
# fixtures get their own single commit instead -- same discovery, no answers, no
# history to mine.
RUN cd /app \
 && git init -q \
 && git config user.email fixtures@example.com \
 && git config user.name fixtures \
 && git add -A \
 && git commit -qm "task fixtures"
