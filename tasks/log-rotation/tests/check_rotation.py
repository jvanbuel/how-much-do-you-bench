"""Reseed, run the script twice, and read what is left on disk."""

import gzip
import subprocess
import sys
from pathlib import Path

ROOT = Path("/var/log/app")
ARCHIVE = ROOT / "archive"
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'} {label}" + ("" if ok else f": {detail}"))
    if not ok:
        failures.append(label)


def state() -> dict[str, bytes]:
    return {str(p.relative_to(ROOT)): p.read_bytes() for p in sorted(ROOT.rglob("*")) if p.is_file()}


subprocess.run(["/app/seed_logs.sh"], check=True)
first = subprocess.run(["/app/rotate.sh"], capture_output=True, text=True)
check("first run succeeds", first.returncode == 0, (first.stderr or first.stdout)[-300:])
after_first = state()

# Rotated, with their contents intact through the compression.
for name in ("api.log", "worker 1.log", "-broken.log"):
    gz = ARCHIVE / f"{name}.gz"
    check(f"{name} rotated", gz.is_file() and not (ROOT / name).exists(),
          f"archive={gz.is_file()} plain={(ROOT / name).exists()}")
    if gz.is_file():
        try:
            body = gzip.decompress(gz.read_bytes())
        except Exception as exc:  # a copied-not-compressed file lands here
            body = b""
            check(f"{name}.gz is gzip", False, str(exc))
        check(f"{name}.gz keeps its contents", body == b"line one\nline two\n", repr(body[:60]))

check("recent log untouched", (ROOT / "app.log").is_file() and not (ARCHIVE / "app.log.gz").exists())
check("non-log file untouched", (ROOT / "app.log.1").is_file())
check("subdirectory not recursed", (ROOT / "nested/deep.log").is_file()
      and not (ARCHIVE / "deep.log.gz").exists())

# Retention, the inverted half of the original bug.
check("old archive deleted", not (ARCHIVE / "checkout-2024.log.gz").exists())
check("recent archive kept", (ARCHIVE / "checkout-2026.log.gz").is_file())

# Nothing beyond the six files the task describes: a script that renames or
# leaves temporaries behind passes everything above and still is not correct.
check("no stray files", set(after_first) == {
    "app.log", "app.log.1", "nested/deep.log", "archive/api.log.gz",
    "archive/worker 1.log.gz", "archive/-broken.log.gz", "archive/checkout-2026.log.gz",
}, str(sorted(after_first)))

second = subprocess.run(["/app/rotate.sh"], capture_output=True, text=True)
check("second run succeeds", second.returncode == 0, (second.stderr or second.stdout)[-300:])
check("second run changes nothing", state() == after_first, "state differs after the second run")

sys.exit(1 if failures else 0)
