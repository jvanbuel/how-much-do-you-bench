<script>
  import { sortRows } from "./lib/scoring.js";

  /** One sortable table. Two boards said the same thing twice; sorting is the
   * affordance people already expect from a leaderboard.
   *
   * The efficiency floor survives the merge. Sorting by tokens ascending would
   * otherwise put a submission that gave up immediately at the top, so rows
   * below half the leader's score are marked as out of the running. */
  let {
    rows = [],
    columns = [],
    frontier = [],
    sortKey = $bindable("passed"),
    sortDir = $bindable("desc"),
    floor = 0,
    openId = $bindable(null),
    pending = [],
    viewerUrl = "",
  } = $props();

  /** Where a rollout lives in the trajectory viewer.
   *
   * Must match `viewer_slug` in the worker, which names the symlink this URL
   * resolves to. `#` marks a repeat of the same task and would be read as a URL
   * fragment, so it becomes a dash on both sides. */
  // A column that is blank on every row is furniture. It appears only when a
  // rollout actually recorded an error, or a note about how it was run -- an
  // agent.yaml that could not be graded as written, or a token count the
  // gateway could not vouch for.
  const hasDetail = (tasks) => tasks.some((t) => t.error || t.notes);

  const traceUrl = (submissionId, taskId) =>
    `${viewerUrl}/jobs/${submissionId}__${taskId.replaceAll("#", "-")}`;

  const column = $derived(columns.find((c) => c.key === sortKey) ?? columns[0]);

  const sorted = $derived(sortRows(rows, column, sortDir));

  // Medals mean "leading", so they only appear under a prize ordering. Sorted
  // by team name, or by score ascending, position carries no rank and a medal
  // would be a lie.
  // Only meaningful while ranking on cost; on accuracy the floor is irrelevant.
  const showFloor = $derived(sortKey === "tokens" && sortDir === "asc");

  const ranking = $derived(
    (sortKey === "passed" && sortDir === "desc") ||
      (sortKey === "tokens" && sortDir === "asc"),
  );

  const MEDALS = ["\u{1F947}", "\u{1F948}", "\u{1F949}"];
  const PLACES = ["First", "Second", "Third"];

  // Submissions below the efficiency floor are out of the running, so they
  // cannot take a medal in the cost ranking; the next eligible team does.
  const medals = $derived.by(() => {
    if (!ranking) return new Map();
    const eligible = sorted.filter((r) => !(showFloor && r.passed < floor));
    return new Map(eligible.slice(0, 3).map((r, i) => [r.submission_id, i]));
  });

  function toggle(key) {
    const next = columns.find((c) => c.key === key);
    if (!next?.sort) return;
    if (key === sortKey) {
      sortDir = sortDir === "asc" ? "desc" : "asc";
    } else {
      sortKey = key;
      sortDir = next.defaultDir ?? "desc";
    }
  }

  // One row open at a time: this is a drill-down, not a comparison view, and
  // several open rows push the rest of the board off screen. Owned by the page
  // so selecting a point in the chart opens the matching row.
  const toggleRow = (id) => (openId = openId === id ? null : id);

  // A row opened from the chart is usually below the fold.
  let rows_el = $state(null);
  $effect(() => {
    if (!openId || !rows_el) return;
    const target = rows_el.querySelector(`[data-row="${CSS.escape(openId)}"]`);
    target?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });

  const ariaSort = (key) =>
    key === sortKey ? (sortDir === "asc" ? "ascending" : "descending") : "none";
</script>

<table class="w-full text-sm">
    <thead>
      <tr class="border-surface-300-700 border-b">
        <th class="rank-head">#</th>
        {#each columns as col (col.key)}
          <th
            class="py-2 font-medium {col.num ? 'text-right' : 'text-left'}"
            aria-sort={ariaSort(col.key)}
            title={col.title}
          >
            {#if col.sort}
              <button
                type="button"
                class="sort-btn"
                class:active={col.key === sortKey}
                onclick={() => toggle(col.key)}
              >
                <span>{col.label}</span>
                <span class="indicator" aria-hidden="true">
                  {col.key === sortKey ? (sortDir === "asc" ? "↑" : "↓") : "↕"}
                </span>
              </button>
            {:else}
              <span class="static-label">{col.label}</span>
            {/if}
          </th>
        {/each}
        <th class="w-8"><span class="sr-only">Task detail</span></th>
      </tr>
    </thead>
    <tbody bind:this={rows_el}>
      {#each sorted as row, index (row.submission_id)}
        {@const ineligible = showFloor && row.passed < floor}
        {@const medal = medals.get(row.submission_id)}
        {@const open = openId === row.submission_id}
        <tr
          class="row"
          data-row={row.submission_id}
          class:ineligible
          class:open
          onclick={() => toggleRow(row.submission_id)}
        >
          <td class="rank">
            <button
              type="button"
              class="disclosure"
              aria-expanded={open}
              aria-controls="tasks-{row.submission_id}"
              onclick={(e) => {
                e.stopPropagation();
                toggleRow(row.submission_id);
              }}
            >
              {#if medal !== undefined}
                <span class="medal" title="{PLACES[medal]} place">{MEDALS[medal]}</span>
              {:else}
                <span class="muted">{index + 1}</span>
              {/if}
              <span class="sr-only">
                {open ? "Hide" : "Show"} task results for {row.team}
              </span>
            </button>
          </td>
          {#each columns as col (col.key)}
            <td class="px-2 py-2 {col.num ? 'text-right tabular-nums' : ''}">
              {col.get(row)}
              {#if col.key === "team" && ineligible}
                <span class="note">below floor</span>
              {/if}
              {#if col.key === "team" && frontier.includes(row.submission_id)}
                <!-- Identity is never carried by mark colour alone. -->
                <span class="frontier" title="On the Pareto frontier">●</span>
              {/if}
            </td>
          {/each}
          <td class="chevron" aria-hidden="true">{open ? "▾" : "▸"}</td>
        </tr>
        {#if open}
          <tr class="detail-row">
            <td colspan={columns.length + 2} id="tasks-{row.submission_id}">
              {#if row.tasks?.length}
                <table class="tasks">
                  <thead>
                    <tr>
                      <th class="st"><span class="sr-only">Result</span></th>
                      <th>Task</th>
                      <th class="text-right">Duration</th>
                      {#if hasDetail(row.tasks)}<th>Detail</th>{/if}
                      <th><span class="sr-only">Trajectory</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {#each row.tasks as task (task.task_id)}
                      <tr class:failed={!task.passed}>
                        <!-- The marker is the whole result: a word beside it
                             said the same thing twice. Screen readers get the
                             word, since a glyph alone does not carry it. -->
                        <td class="st">
                          <span aria-hidden="true">{task.passed ? "✓" : "✗"}</span>
                          <span class="sr-only">{task.passed ? "passed" : "failed"}</span>
                        </td>
                        <td class="name" title={task.agent ? `ran with ${task.agent}` : ""}>
                          {task.task_id}
                        </td>
                        <td class="text-right tabular">{Math.round(task.duration_s)}s</td>
                        {#if hasDetail(row.tasks)}
                          <!-- Only a real error earns a column: "failed" here
                               would repeat the marker. -->
                          {@const detail = task.error ?? task.notes ?? ""}
                          <td class="detail" title={detail}>{detail}</td>
                        {/if}
                        <td class="text-right">
                          {#if viewerUrl && task.status === "done"}
                            <a
                              class="trace"
                              href={traceUrl(row.submission_id, task.task_id)}
                              target="_blank"
                              rel="noopener"
                              title="Open this rollout's trajectory"
                            >
                              trace ↗
                            </a>
                          {/if}
                        </td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              {:else}
                <p class="muted py-2">No task detail recorded for this submission.</p>
              {/if}
            </td>
          </tr>
        {/if}
      {/each}

      {#each pending as row (row.submission_id)}
        <tr class="row pending">
          <td class="rank"><span class="pulse" aria-hidden="true"></span></td>
          <td class="px-2 py-2">
            {row.team}
            <span class="note">running</span>
          </td>
          <td class="px-2 py-2">{row.commit}</td>
          <td class="px-2 py-2 text-right tabular-nums" colspan="2">
            {row.completed} of {row.task_count} tasks done
            <!-- A rollout that never ran is why a submission sits here after
                 its last task finished, so saying so is the difference between
                 "still going" and "somebody needs to redrive the queue". -->
            {#if row.errored}
              <span class="note">{row.errored} failed to run</span>
            {/if}
          </td>
          <td></td>
        </tr>
      {/each}
      {#if !rows.length && !pending.length}
        <!-- The header row is the point: an empty board still has to show what
             it will rank on, so the columns stay and only the body is empty. -->
        <tr>
          <td class="py-8 text-center opacity-60" colspan={columns.length + 2}>
            No completed submissions yet. Scores appear here as soon as a run
            finishes.
          </td>
        </tr>
      {/if}
    </tbody>
  </table>

<style>
  table {
    font-size: var(--text-small);
  }

  th {
    padding-block: 0.6rem;
    color: var(--ink-muted);
    font-weight: 500;
  }

  .rank-head,
  .rank {
    width: 3rem;
    text-align: right;
    padding-right: 0.5rem;
  }

  .static-label {
    padding-inline: 0.5rem;
  }

  .row {
    cursor: pointer;
    border-bottom: 1px solid var(--color-surface-200);
    transition: background-color 150ms ease-out;
  }

  :global(.dark) .row {
    border-bottom-color: var(--color-surface-800);
  }

  .row:hover {
    background-color: var(--color-surface-100);
  }

  :global(.dark) .row:hover {
    background-color: var(--color-surface-900);
  }

  .row.open {
    background-color: var(--color-surface-100);
    border-bottom-color: transparent;
  }

  :global(.dark) .row.open {
    background-color: var(--color-surface-900);
  }

  .disclosure {
    background: none;
    border: 0;
    padding: 0.25rem;
    cursor: pointer;
    color: inherit;
    font: inherit;
  }

  .disclosure:focus-visible {
    outline: 2px solid var(--color-primary-500);
    outline-offset: 2px;
    border-radius: 0.25rem;
  }

  .chevron {
    width: 2rem;
    text-align: center;
    color: var(--ink-subtle);
  }

  .detail-row td {
    padding: 0.25rem 0.5rem 1.25rem 3rem;
    background-color: var(--color-surface-100);
    border-bottom: 1px solid var(--color-surface-200);
  }

  :global(.dark) .detail-row td {
    background-color: var(--color-surface-900);
    border-bottom-color: var(--color-surface-800);
  }

  .tasks {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--text-caption);
    margin: 0.25rem 0 0.5rem;
  }

  .tasks th {
    text-align: left;
    font-weight: 500;
    color: var(--ink-muted);
    padding: 0.3rem 0.75rem 0.3rem 0;
    border-bottom: 1px solid var(--color-surface-300);
    white-space: nowrap;
  }

  .tasks td {
    padding: 0.35rem 0.75rem 0.35rem 0;
    border-bottom: 1px solid var(--color-surface-200);
    vertical-align: baseline;
  }

  .tasks tbody tr:last-child td {
    border-bottom: none;
  }

  :global(.dark) .tasks th {
    border-bottom-color: var(--color-surface-700);
  }

  :global(.dark) .tasks td {
    border-bottom-color: var(--color-surface-800);
  }

  /* The status column is the result, so it gets the colour and the glyph. */
  .st {
    width: 1.5rem;
    color: var(--color-success-600, #2e7d32);
    font-weight: 700;
  }

  .tasks tr.failed .st {
    color: var(--color-error-600, #c0392b);
  }

  :global(.dark) .st {
    color: #5fb865;
  }

  :global(.dark) .tasks tr.failed .st {
    color: #f08b7c;
  }

  .name {
    font-weight: 500;
    color: var(--ink);
  }

  /* Numbers line up column-wise, so a slow rollout is visible at a glance. */
  .tabular {
    font-variant-numeric: tabular-nums;
    color: var(--ink-subtle);
  }

  .detail {
    color: var(--ink-subtle);
    max-width: 28rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .trace {
    font-size: 0.9em;
    font-weight: 600;
    color: var(--color-primary-600);
    text-decoration: none;
    white-space: nowrap;
    border-bottom: 1px solid transparent;
    transition: border-color 160ms cubic-bezier(0.22, 1, 0.36, 1);
  }

  .trace:hover,
  .trace:focus-visible {
    border-bottom-color: currentColor;
  }

  @media (prefers-reduced-motion: reduce) {
    .trace {
      transition: none;
    }
  }

  .muted {
    color: var(--ink-muted);
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }

  .sort-btn {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.15rem 0.5rem;
    border-radius: 0.25rem;
    color: inherit;
    opacity: 0.6;
    cursor: pointer;
    transition:
      opacity 150ms ease-out,
      background-color 150ms ease-out;
  }

  th:last-child .sort-btn,
  th.text-right .sort-btn {
    flex-direction: row-reverse;
  }

  .sort-btn:hover {
    opacity: 1;
    background-color: var(--color-surface-200);
  }

  :global(.dark) .sort-btn:hover {
    background-color: var(--color-surface-800);
  }

  .sort-btn:focus-visible {
    outline: 2px solid var(--color-primary-500);
    outline-offset: 1px;
    opacity: 1;
  }

  /* Accent marks the active sort: selection state, not decoration. */
  .sort-btn.active {
    opacity: 1;
    color: var(--color-primary-600);
  }

  :global(.dark) .sort-btn.active {
    color: var(--color-primary-300);
  }

  .indicator {
    font-size: 0.75em;
  }

  .ineligible td {
    color: var(--ink-subtle);
  }

  .medal {
    font-size: 1rem;
    line-height: 1;
  }

  /* Still running: present so teams can see their submission is alive, but
     unranked, because a partial score would place them wrongly. */
  .row.pending td {
    color: var(--ink-subtle);
    cursor: default;
  }

  .pulse {
    display: inline-block;
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 50%;
    background: var(--color-primary-500);
    animation: pulse 1.8s ease-in-out infinite;
  }

  :global(.dark) .pulse {
    background: var(--color-primary-300);
  }

  @keyframes pulse {
    0%,
    100% {
      opacity: 1;
      transform: scale(1);
    }
    50% {
      opacity: 0.35;
      transform: scale(0.75);
    }
  }

  .frontier {
    color: var(--series-1);
    margin-left: 0.3rem;
    font-size: 0.7em;
    vertical-align: middle;
  }

  .note {
    margin-left: 0.4rem;
    font-size: 0.75em;
    opacity: 0.55;
  }

  @media (prefers-reduced-motion: reduce) {
    .sort-btn,
    .row {
      transition: none;
    }

    .pulse {
      animation: none;
      opacity: 0.85;
    }
  }
</style>
