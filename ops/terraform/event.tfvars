# Event day: twelve rollouts in flight.
#
# Measured rollout is 142s, so 1000 rollouts across four hours needs about ten
# concurrent; twelve covers the closing spike.
#
# Six hosts rather than four, because a replica now reserves the worst task in
# the suite (6144MiB + 512) instead of the common one, and two of those fit a
# c7g.2xlarge where three of the old 4608MiB reservation did. The old packing
# was not cheaper, it was oversubscribed: a replica drawing pyspark-skew,
# polars-vectorise or airflow-assets on a full host got an OOM kill that reads
# as "no trial result produced" and retries five times.
#
# Spreading over six hosts rather than two big ones costs the same per hour and
# loses only a sixth of the in-flight rollouts if a host dies. Check the
# reservation against the fleet after changing either:
#   terraform output task_budgets
worker_instance_type = "c7g.2xlarge"
worker_instances     = 6
worker_replicas      = 12
