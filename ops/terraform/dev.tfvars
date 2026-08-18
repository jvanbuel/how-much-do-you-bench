# Testing the deployed path: one host, one rollout at a time. ~$0.20/hr.
#
# c7g.xlarge (4 vCPU / 8 GiB) is the smallest size that fits a rollout: a
# replica reserves the worst task in the suite -- 6144MiB + 512 for the worker
# itself -- and a c7g.large would be handing the whole instance to the task and
# leaving nothing to run it. `terraform output task_budgets` prints what the
# suite currently asks for; if it grows past this instance, nothing will place.
#
# Most development does not need this at all. `just eval` runs the identical
# rollout on your laptop for nothing, through the same Harbor path the workers
# use, so bring the fleet up only to exercise ECS itself.
worker_instance_type = "c7g.xlarge"
worker_instances     = 1
worker_replicas      = 1

# A subset, so a rehearsal finishes in minutes rather than an hour. Repeats of
# one task would be `incremental-dupes#1,incremental-dupes#2`: the table is
# keyed (submission_id, task_id), so distinct ids are what make four runs four
# rows instead of one overwritten row.
task_ids = "incremental-dupes,airflow-assets,airflow-parse-cost,terraform-rekey,dbt-scd2,polars-vectorise"
