FROM hmdyb-task-base:1

WORKDIR /app
# One file and a git repo, like every real task workspace: aider navigates by
# repo map, and the repo map comes from git.
RUN echo "Workspace for the canary task. The instruction says what to do." > README.md \
 && git init -q \
 && git config user.email fixtures@example.com \
 && git config user.name fixtures \
 && git add -A \
 && git commit -qm "task fixtures"
