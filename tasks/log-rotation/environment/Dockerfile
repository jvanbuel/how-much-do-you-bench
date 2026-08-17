FROM ghcr.io/jvanbuel/hmdyb-task-base:1

WORKDIR /app
COPY rotate.sh /app/rotate.sh
COPY seed_logs.sh /app/seed_logs.sh
RUN chmod +x /app/rotate.sh /app/seed_logs.sh && /app/seed_logs.sh
