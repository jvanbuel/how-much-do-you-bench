#!/bin/bash
set -euo pipefail

cat > /app/rotate.sh <<'ROTATE'
#!/bin/bash
# Nightly log rotation. Cron runs this at 03:00.
set -euo pipefail

ROOT=/var/log/app
ARCHIVE="$ROOT/archive"
KEEP_PLAIN_DAYS=7
KEEP_ARCHIVE_DAYS=90

mkdir -p "$ARCHIVE"

# -print0 with a null-delimited read: a for-loop over $(find) splits on the
# spaces inside filenames. +N is "more than N days", which is what "older
# than" means; a bare N matches that one day only.
while IFS= read -r -d '' f; do
    echo "rotating $f"
    gzip -c "$f" > "$ARCHIVE/$(basename "$f").gz"
    rm -- "$f"
done < <(find "$ROOT" -maxdepth 1 -type f -name '*.log' -mtime +"$KEEP_PLAIN_DAYS" -print0)

find "$ARCHIVE" -type f -name '*.gz' -mtime +"$KEEP_ARCHIVE_DAYS" -delete

echo "rotation complete"
ROTATE
chmod +x /app/rotate.sh
