FROM hmdyb-task-base:1

RUN pip install --no-cache-dir duckdb==1.4.1 polars==1.35.1 pyarrow==22.0.0

WORKDIR /app
COPY report.sql run_report.sh generate_data.py /app/
RUN chmod +x /app/run_report.sh && mkdir -p /app/out && python /app/generate_data.py
