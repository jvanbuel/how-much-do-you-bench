FROM hmdyb-task-base:1

WORKDIR /app

RUN pip install --no-cache-dir pytest

# The starter workspace the agent edits, copied from this build context.
COPY app/ /app/

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
