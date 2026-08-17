FROM hmdyb-task-base:1

WORKDIR /app

RUN pip install --no-cache-dir pytest

# The starter workspace the agent edits, copied from this build context.
COPY app/ /app/
