# Testing the deployed path: one host, one rollout at a time. ~$0.20/hr.
#
# c7g.xlarge (4 vCPU / 8 GiB) is the smallest size that fits a rollout: the
# task container alone asks for 2 vCPU / 4 GiB, plus the worker task and the
# ECS agent. A c7g.large would be handing the whole instance to the task and
# leaving nothing to run it.
#
# Most development does not need this at all. `just eval` runs the identical
# rollout on your laptop for nothing, through the same Harbor path the workers
# use, so bring the fleet up only to exercise ECS itself.
worker_instance_type = "c7g.xlarge"
worker_instances     = 1
worker_replicas      = 1

# Four repeats of the one task: the table is keyed (submission_id, task_id), so
# distinct ids are what make four runs four rows instead of one overwritten
# row. passed/4 then reads as that group's pass rate.
task_ids = "incremental-dupes,airflow-assets,airflow-parse-cost,terraform-rekey,dbt-scd2,polars-vectorise"
