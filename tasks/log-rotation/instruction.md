`/app/rotate.sh` is the nightly log rotation for a service that writes to
`/var/log/app`. Ops filed three complaints in one week: logs from a month ago are
still sitting there uncompressed, the worker logs never rotate at all, and a
quarter of the archive vanished.

The retention policy the script is supposed to implement:

* a `*.log` file directly in `/var/log/app` older than 7 days is compressed to
  `/var/log/app/archive/<name>.log.gz`, and the plain file removed;
* anything in `/var/log/app/archive` older than 90 days is deleted;
* nothing else in `/var/log/app` is touched.

Fix `/app/rotate.sh` so one run leaves the directory exactly as that policy says.
`/app/seed_logs.sh` rebuilds the starting state, so you can run the script, look
at the result, reseed, and try again. Running `rotate.sh` twice in a row must
succeed both times, and the second run must change nothing.

Only `/app/rotate.sh` is checked. Keep it a bash script at that path.
