# codex-broker-reaper — launchd job (macOS, opt-in)

Periodically reaps openai-codex `app-server-broker.mjs` processes that outlived
their owning Claude session. This is the **session-independent reclaim path**:
because it is decoupled from any review session, there is no concurrency hazard
— each running broker must individually pass the idle-age gate (`broker.log`
idle longer than `--max-age`, default 30 min) before it is killed, and active
brokers are skipped.

It complements **Step 6** of `codex-review-wrap`: phase-end reaping keeps the
broker count down between runs, while this job reclaims orphans whose owning
session is already gone — either because the workspace directory was deleted,
or because the workspace survives but nobody works in it any more (the latter
needs a way to read live process cwds: `lsof`, or `/proc` on Linux; without
one the broker is kept).

> macOS only. The leak is inherent to launchd reparenting and the
> `/var/folders` sessionDirs the broker creates.

## Install

```bash
# 1. Resolve the reaper path from the installed praxis plugin.
manifest="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/installed_plugins.json"
plugin_root=$(jq -r '.plugins["praxis@praxis"][0].installPath // empty' "$manifest")
reaper="$plugin_root/skills/codex-review-wrap/codex-broker-reaper.sh"
test -x "$reaper" || { echo "reaper not found at $reaper"; exit 1; }

# 2. Render the plist from the template.
log="$HOME/Library/Logs/codex-broker-reaper.log"
plist="$HOME/Library/LaunchAgents/com.praxis.codex-broker-reaper.plist"
sed -e "s#__REAPER_PATH__#$reaper#" -e "s#__LOG_PATH__#$log#" \
  "$plugin_root/skills/codex-review-wrap/codex-broker-reaper.plist" > "$plist"

# 3. Load it.
launchctl unload "$plist" 2>/dev/null || true
launchctl load "$plist"
launchctl list | grep codex-broker-reaper
```

## Verify

```bash
# Force a run now and watch the log.
launchctl start com.praxis.codex-broker-reaper
tail -n 20 "$HOME/Library/Logs/codex-broker-reaper.log"
```

Each run ends with a summary line, e.g.
`codex-broker-reaper: mode=reap dry_run=false max_age_min=30 scanned=4 reaped=1 gc_dirs=3 skipped=3`.

## Uninstall

```bash
plist="$HOME/Library/LaunchAgents/com.praxis.codex-broker-reaper.plist"
launchctl unload "$plist"
rm -f "$plist"
```

## Tuning

Edit the rendered `$HOME/Library/LaunchAgents/com.praxis.codex-broker-reaper.plist`:

- `--max-age` argument — minutes a broker must be idle before it is reaped (default 30).
- `StartInterval` — seconds between runs (default 1800).

Then `launchctl unload` + `launchctl load` to apply.

The job appends to a single log with no rotation. Over the multi-day uptime
this targets, prune it periodically (or point `StandardOutPath` /
`StandardErrorPath` at `/dev/null` if you do not need the run history):

```bash
: > "$HOME/Library/Logs/codex-broker-reaper.log"
```

## After a praxis plugin update

`installPath` is version-pinned (e.g. `.../praxis/6.3.1`), so the rendered
plist points at a specific version. After updating the praxis plugin, **re-run
the Install steps** to re-render the plist against the new path. (The
phase-end Step 6 reaper resolves its path at runtime and is unaffected.)
