/** A submission mid-run has a partial score and a partial token count, so it is
 * shown as running rather than ranked against finished work. */
export const isFinal = (s) => s.task_count > 0 && s.completed >= s.task_count;

/** Pinned locale: the machine default renders 121500 as "121.500", which reads
 * as a decimal. */
export const num = (n) => n.toLocaleString("en-US");

/** Elapsed time as a leaderboard reads it: 48s, 3m 20s, 1h 04m.
 *
 * Seconds are dropped past an hour, where they are noise, but kept below it,
 * where a 20 second difference between two submissions is the story. */
export function duration(seconds) {
  const s = Math.round(seconds ?? 0);
  if (!s) return "—";
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

/** Submission time in the viewer's own timezone, to the minute.
 *
 * With the date: a board that outlives one day otherwise shows two submissions
 * as "09:18" and leaves you to guess which week each belongs to. */
export function clockTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleString(undefined, {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      });
}

/** Compact axis labels: 900, 9.2k, 10k, 100k, 1M.
 *
 * Naive division gives "1000k" and "1.0k" on a log axis, where the ticks are
 * exact powers of ten and those are the labels you see most. */
export function short(n) {
  const trim = (v) => Number(v.toFixed(1)).toString();
  if (n >= 1_000_000) return trim(n / 1_000_000) + "M";
  if (n >= 1_000) return trim(n / 1_000) + "k";
  return String(n);
}

/** Round a maximum up to a clean axis bound. */
export function niceMax(value) {
  if (!Number.isFinite(value) || value <= 0) return 10;
  const magnitude = Math.pow(10, Math.floor(Math.log10(value)));
  return Math.ceil(value / magnitude) * magnitude;
}

/** Half the leader's score: the bar for the efficiency prize.
 *
 * Without it, a submission that gives up immediately spends the fewest tokens
 * and tops any cost ranking. The table marks anything below it. */
export function efficiencyFloor(subs) {
  if (!subs.length) return 0;
  return Math.max(...subs.map((s) => s.passed)) / 2;
}

/** Sort rows for the leaderboard.
 *
 * Ties break on fewer tokens, then team name. That is not cosmetic: the
 * accuracy ranking is defined as "tasks passed, ties broken by fewer tokens",
 * so an alphabetical tie-break would quietly change the standings. */
export function sortRows(rows, column, dir) {
  const factor = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const av = column.sort(a);
    const bv = column.sort(b);
    if (av !== bv) return av > bv ? factor : -factor;
    if (a.tokens !== b.tokens) return a.tokens - b.tokens;
    return a.team.localeCompare(b.team);
  });
}
