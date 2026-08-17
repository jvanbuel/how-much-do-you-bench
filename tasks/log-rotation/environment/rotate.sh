#!/bin/bash
# Nightly log rotation. Cron runs this at 03:00.
set -euo pipefail

ROOT=/var/log/app
ARCHIVE=$ROOT/archive
KEEP_PLAIN_DAYS=7
KEEP_ARCHIVE_DAYS=90

mkdir -p $ARCHIVE

for f in $(find $ROOT -maxdepth 1 -type f -name '*.log' -mtime $KEEP_PLAIN_DAYS); do
    echo "rotating $f"
    gzip -c $f > $ARCHIVE/$(basename $f).gz
    rm $f
done

find $ARCHIVE -type f -name '*.gz' -mtime -$KEEP_ARCHIVE_DAYS -delete

echo "rotation complete"
