FROM hmdyb-task-base:1

WORKDIR /app
COPY rotate.sh /app/rotate.sh
COPY seed_logs.sh /app/seed_logs.sh
RUN chmod +x /app/rotate.sh /app/seed_logs.sh && /app/seed_logs.sh

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
