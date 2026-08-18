FROM hmdyb-task-base:1

RUN pip install --no-cache-dir duckdb==1.4.1 polars==1.35.1 pyarrow==22.0.0

WORKDIR /app
COPY report.sql run_report.sh generate_data.py /app/
RUN chmod +x /app/run_report.sh && mkdir -p /app/out && python /app/generate_data.py

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
