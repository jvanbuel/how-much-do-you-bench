<script>
  /** A metric card.
   *
   * One component so every figure shares a vocabulary: same label treatment,
   * same numeral size, same card. `live` marks a value that is still moving,
   * which is the difference between "1 running" as a fact and as a thing you
   * are waiting on.
   *
   * Skeleton's `card` utility and its `preset-filled-primary-100-900` carry the
   * surface, including the light/dark pair that was hand-written here as two
   * rules and a :global(.dark) override. What stays local is what Skeleton has
   * no opinion about: tabular numerals, and a dot that pulses while a figure is
   * still moving.
   */
  let { label, value, hint = null, live = false } = $props();
</script>

<div
  class="card preset-filled-primary-100-900 flex flex-col gap-1 border border-primary-200 p-4 dark:border-primary-500"
>
  <div class="flex min-h-6 items-center gap-2">
    <dt class="text-sm leading-snug opacity-70">{label}</dt>
    {#if live}
      <span class="pulse" aria-hidden="true"></span>
    {/if}
  </div>
  <dd class="text-3xl leading-tight tabular-nums">{value}</dd>
  {#if hint}
    <p class="text-xs opacity-70">{hint}</p>
  {/if}
</div>

<style>
  /* Work still in flight. The dot pulses rather than the number, so the figure
     stays readable while it changes. */
  .pulse {
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

  @media (prefers-reduced-motion: reduce) {
    .pulse {
      animation: none;
      opacity: 0.85;
    }
  }
</style>
