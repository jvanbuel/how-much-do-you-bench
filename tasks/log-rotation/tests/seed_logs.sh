#!/bin/bash
# Rebuilds /var/log/app in a known state. The verifier calls this too, so a
# rollout always starts from the same mtimes no matter what has been run.
set -euo pipefail

root=/var/log/app
rm -rf "$root"
mkdir -p "$root/archive"

write() {  # write <path> <age-in-days>
  printf 'line one\nline two\n' > "$1"
  touch -d "$2 days ago" "$1"
}

write "$root/app.log" 0
write "$root/api.log" 10
write "$root/worker 1.log" 30
write "$root/app.log.1" 20
write "$root/-broken.log" 12
mkdir -p "$root/nested"
write "$root/nested/deep.log" 40

gz() {  # gz <path> <age-in-days>
  printf 'archived\n' | gzip > "$1"
  touch -d "$2 days ago" "$1"
}

gz "$root/archive/checkout-2024.log.gz" 200
gz "$root/archive/checkout-2026.log.gz" 5
