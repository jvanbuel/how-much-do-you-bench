<script>
  import { Switch } from "@skeletonlabs/skeleton-svelte";
  import { apply, persist, resolve, stored, watchSystem } from "./lib/theme.js";

  let preference = $state(stored());
  const active = $derived(resolve(preference));

  // Two states, not three: a "system" item in a two-position control is a
  // setting rather than a toggle. System stays the default until first use.
  function set(dark) {
    preference = dark ? "dark" : "light";
    apply(preference);
    persist(preference);
  }

  $effect(() => watchSystem(() => apply(preference)));
</script>

<Switch
  checked={active === "dark"}
  onCheckedChange={(e) => set(e.checked)}
  aria-label={active === "dark" ? "Switch to light mode" : "Switch to dark mode"}
>
  <Switch.HiddenInput />
  <Switch.Control>
    <Switch.Thumb />
  </Switch.Control>
  <Switch.Label>
    <span aria-hidden="true">{active === "dark" ? "☾" : "☀"}</span>
  </Switch.Label>
</Switch>
