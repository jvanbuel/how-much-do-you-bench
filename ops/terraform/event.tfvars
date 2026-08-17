# Event day: four hosts, twelve rollouts in flight.
#
# Measured rollout is 142s, so 1000 rollouts across four hours needs about ten
# concurrent; twelve covers the closing spike. Three per host keeps each
# rollout's 2 vCPU / 4 GiB inside a c7i.2xlarge with room for the worker
# containers themselves.
#
# Spreading over four hosts rather than one big instance costs the same per
# hour and loses only a quarter of the in-flight rollouts if a host dies.
worker_instance_type = "c7g.2xlarge"
worker_instances     = 4
worker_replicas      = 12

# The graded suite: every task in tasks/. Without this the module default
# applies, and a submission is scored on one task.
task_ids = "airflow-assets,airflow-parse-cost,analyze-multi-run-plot,dbt-scd2,duckdb-nulls,extend-trajectory-multistep,git-secret-history,harbor-analyze-results,harbor-analyze-trajectories,incremental-dupes,log-rotation,normalize-token-usage,polars-vectorise,proxmox-container-ssh-troubleshooting,pyspark-skew,survey-challenges-by-segment,survey-initiatives,survey-normalize,survey-top-challenges,terraform-rekey,voltpulse-alb"
