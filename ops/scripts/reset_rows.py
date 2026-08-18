"""Scan the results table and delete every row.

One process, one client, one connection. The script this replaced ran the AWS
CLI once per row -- a second each in interpreter startup and credential
resolution, so a few hundred rows spent minutes spawning processes rather than
talking to DynamoDB. batch_writer() does the twenty-five-item batching and the
unprocessed-item retries itself.

Run through uv, which supplies boto3 without the repo carrying a dependency:

    uv run --with boto3 python ops/scripts/reset_rows.py <region> <table>
"""

import sys

import boto3


def main() -> int:
    region, table_name = sys.argv[1:3]
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    deleted = 0
    scan = {"ProjectionExpression": "submission_id, task_id"}
    with table.batch_writer() as batch:
        while True:
            page = table.scan(**scan)
            for item in page["Items"]:
                batch.delete_item(Key={"submission_id": item["submission_id"], "task_id": item["task_id"]})
                deleted += 1
            # A scan returns at most 1MB per page, so the last key is how the
            # next page is asked for. Without this a large table is half-cleared.
            if "LastEvaluatedKey" not in page:
                break
            scan["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    print(f"  {deleted} rows deleted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
