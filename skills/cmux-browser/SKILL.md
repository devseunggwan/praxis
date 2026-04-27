---
name: cmux-browser
description: "Browser automation E2E testing via cmux browser CLI — navigation, form input, click, state verification with SPA hydration wait. Triggers on \"cmux browser\", \"cmux 브라우저\"."
---

# cmux Browser E2E Test

Runs E2E tests using the cmux terminal's built-in browser automation CLI.
Calls `cmux browser` commands directly instead of Playwright MCP.

## User Input

- `$ARGUMENTS` — Target URL or test scenario description. Ask if empty.

---

## Iron Law — SPA Hydration Wait Before Any DOM Action

```
Before snapshot --interactive or any DOM-dependent action (click/fill/is/get),
always run the SPA Hydration Wait Protocol once.
wait --load-state complete alone is NOT sufficient for SPAs.
```

`wait --load-state complete` only guarantees network-level load (HTML, CSS, scripts).
SPAs (React, Vue, Next.js, etc.) render the actual DOM client-side AFTER that point.
Capturing a snapshot or touching the DOM before JS hydration finishes returns only
the empty shell/skeleton tree — exactly the pre-hydration state.

---

## SPA Hydration Wait Protocol

Run this sequence **before every snapshot** (and before the first DOM-dependent action on a new page).
All commands after `open` require `--surface "$SURFACE"` — capture it once and thread it through every step.

> **Prerequisite:** `cmux browser` requires an active cmux workspace.
> Inside cmux: `$CMUX_WORKSPACE_ID` is set automatically.
> Outside cmux (CI, standalone shell): pass `--workspace <id>` to `cmux browser open` explicitly.

### Step 1 — Open and Load State Wait

```bash
# Inside cmux ($CMUX_WORKSPACE_ID set automatically):
SURFACE=$(cmux browser open <target-url> | grep -oE 'surface:[0-9]+')

# Outside cmux (standalone shell / CI — supply workspace ID explicitly):
# WORKSPACE_ID=<id>  # get from: cmux list-workspaces
# SURFACE=$(cmux browser open <target-url> --workspace "$WORKSPACE_ID" | grep -oE 'surface:[0-9]+')

cmux browser --surface "$SURFACE" wait --load-state complete --timeout 15
```

Network-level load gate. Necessary but not sufficient for SPAs.

### Step 2 — SPA Auto-detect

```bash
cmux browser --surface "$SURFACE" eval '!!(window.__NEXT_DATA__||window.__NUXT__||window.__remixContext||window.__SVELTEKIT_DATA__||window.___gatsby||window.__INITIAL_STATE__||window.ng||document.querySelector("[data-reactroot],[data-v-app],[data-server-rendered],[ng-version],[data-svelte-h],[q\\:container]"))'
```

- Output `true` → SPA framework detected → proceed to Step 3A or 3B
- Output `false`/`null` or detection fails → run Step 3A with a short timeout (3s) then Step 4
  (Step 3A passes instantly on static pages — no performance penalty)

### Step 3A — Content-Density Hydration Wait (default)

Wait until real content is rendered into the DOM:

```bash
# Single-quote JS string — no shell escaping issues with inner double-quotes
# Primary: no loading state + some content (works for dense AND sparse pages)
cmux browser --surface "$SURFACE" wait --function 'document.readyState==="complete" && !document.querySelector("[aria-busy=true],[data-loading=true]") && document.body.innerText.length>30' --timeout 10
```

- `innerText.length > 30` — minimal content threshold; covers login/OTP/confirmation pages
  (high-density threshold of >200 would time out on sparse but fully-hydrated pages)
- `aria-busy`, `data-loading` — loading state resolved
- Unquoted attribute selectors (`[aria-busy=true]`) are valid per CSS spec

For **content-rich pages** (docs, dashboards) where you want stricter validation, upgrade to:
```bash
cmux browser --surface "$SURFACE" wait --function 'document.readyState==="complete" && document.body.innerText.length>200 && document.querySelectorAll("a[href],button").length>5 && !document.querySelector("[aria-busy=true],[data-loading=true]")' --timeout 10
```

### Step 3B — Explicit Selector Wait (precision control)

Wait for a known element to appear. Use when you know the site's DOM structure:

```bash
# Navigation rendered
cmux browser --surface "$SURFACE" wait --selector "nav, aside, [role='navigation']" --timeout 10

# Content container non-empty
cmux browser --surface "$SURFACE" wait --selector "main article, .content > *:not(:empty)" --timeout 10

# Specific text appeared
cmux browser --surface "$SURFACE" wait --text "API Reference" --timeout 10
```

Use Step 3B when the target site is known. Fall back to Step 3A + snapshot validation otherwise.

### Step 4 — Snapshot

```bash
cmux browser --surface "$SURFACE" snapshot --interactive
```

### Snapshot Result Validation (required)

After snapshot, enforce hydration completeness with a conditional check:

```bash
NODE_COUNT=$(cmux browser --surface "$SURFACE" eval 'document.querySelectorAll("a[href],h1,h2,h3,button,nav,article").length' | tr -d ' \n')
if [ "${NODE_COUNT:-0}" -lt 3 ]; then
  echo "Snapshot validation: only $NODE_COUNT elements — likely pre-hydration shell, retrying" >&2
  # Content-density retry — NOT a bare structural selector (main/nav exist pre-hydration)
  cmux browser --surface "$SURFACE" wait --function 'document.readyState==="complete" && !document.querySelector("[aria-busy=true],[data-loading=true]") && document.body.innerText.length>30' --timeout 15 || \
    { echo "Error: hydration retry timed out — provide a specific selector via Step 3B and retry" >&2; exit 1; }
  cmux browser --surface "$SURFACE" snapshot --interactive
fi
```

- **< 3** → truly empty shell (2-node skeleton) → retry with content-density wait
- **≥ 3** → hydration complete, proceed (covers sparse pages: login/OTP/confirmation have ≥ 3 elements)

**Empty-tree signals:** result contains only 2–5 nodes ("Jump to Content", "Welcome") with no nav/article/h2
→ retry with Step 3B or longer timeout.

### Framework Hydration Markers

| Framework | Detection signal | Recommended wait |
|-----------|-----------------|-----------------|
| Next.js | `window.__NEXT_DATA__` | Step 3A |
| Nuxt.js | `window.__NUXT__` | Step 3A |
| Remix | `window.__remixContext` | Step 3A |
| React (CRA) | `[data-reactroot]` attr | Step 3A |
| Vue 3 | `[data-v-app]` attr | Step 3A |
| Gatsby | `window.___gatsby` | Step 3A |
| SvelteKit | `window.__SVELTEKIT_DATA__` | Step 3A |
| Angular | `[ng-version]` attr | Step 3A |
| ReadMe.io / sidebar SPAs | `[class*="Sidebar"]` in DOM | Step 3B `--selector "[class*='Sidebar'],[class*='rm-Sidebar'],nav.sidebar"` |

---

## cmux browser Command Reference

### Navigation

| Command | Description | Example |
|---------|-------------|---------|
| `open <url>` | Open URL in browser | `cmux browser open https://example.com` |
| `open-split <url>` | Open URL in split view | `cmux browser open-split https://example.com` |
| `navigate <url>` | Navigate current tab | `cmux browser navigate /dashboard` |
| `back` | Go back | `cmux browser back` |
| `forward` | Go forward | `cmux browser forward` |
| `reload` | Reload page | `cmux browser reload` |
| `url` | Get current URL | `cmux browser url` |

> **Note:** All commands below (except `open`/`open-split`/`new`/`identify`) require a surface handle.
> Always use `--surface "$SURFACE"` in real scripts. Examples below use the shorthand for readability;
> replace `cmux browser` with `cmux browser --surface "$SURFACE"` in practice.

### DOM Interaction

| Command | Description | Example |
|---------|-------------|---------|
| `click <selector>` | Click element | `cmux browser --surface "$SURFACE" click "button[type='submit']"` |
| `dblclick <selector>` | Double-click | `cmux browser --surface "$SURFACE" dblclick ".editable-cell"` |
| `hover <selector>` | Mouse hover | `cmux browser --surface "$SURFACE" hover ".tooltip-trigger"` |
| `focus <selector>` | Focus element | `cmux browser --surface "$SURFACE" focus "#email"` |
| `check <selector>` | Check checkbox | `cmux browser --surface "$SURFACE" check "#agree"` |
| `uncheck <selector>` | Uncheck checkbox | `cmux browser --surface "$SURFACE" uncheck "#newsletter"` |

### Text Input

| Command | Description | Example |
|---------|-------------|---------|
| `type <selector> <text>` | Simulate keystrokes | `cmux browser --surface "$SURFACE" type "#search" "query"` |
| `fill <selector> <value>` | Set field value (replaces existing) | `cmux browser --surface "$SURFACE" fill "#email" "test@example.com"` |
| `press <key>` | Press keyboard key | `cmux browser --surface "$SURFACE" press Enter` |
| `scroll` | Scroll page | `cmux browser --surface "$SURFACE" scroll --dy 300` |

### Page Inspection

| Command | Description | Example |
|---------|-------------|---------|
| `snapshot [--interactive\|-i]` | Capture accessibility tree | `cmux browser --surface "$SURFACE" snapshot --interactive` |
| `screenshot [--out <path>]` | Save screenshot | `cmux browser --surface "$SURFACE" screenshot --out /tmp/test.png` |
| `get <prop> [--selector <css>]` | Get element property | `cmux browser --surface "$SURFACE" get text --selector "#status"` |
| `is <state> [--selector <css>]` | Check element state | `cmux browser --surface "$SURFACE" is visible --selector "#modal"` |
| `find <role\|text\|...>` | Find element | `cmux browser --surface "$SURFACE" find role button` |
| `highlight [--selector <css>]` | Highlight element | `cmux browser --surface "$SURFACE" highlight ".error"` |

### Wait

| Command | Description | Example |
|---------|-------------|---------|
| `wait --selector <css>` | Wait for selector to appear | `cmux browser --surface "$SURFACE" wait --selector ".loaded"` |
| `wait --text <text>` | Wait for text to appear | `cmux browser --surface "$SURFACE" wait --text "Done"` |
| `wait --url <exact-url>` | Wait for exact URL match | `cmux browser --surface "$SURFACE" wait --url "https://example.com/dashboard"` |
| `wait --url-contains <text>` | Wait for URL to contain substring | `cmux browser --surface "$SURFACE" wait --url-contains "/dashboard"` |
| `wait --load-state complete` | Wait for document.readyState complete | `cmux browser --surface "$SURFACE" wait --load-state complete` |
| `wait --load-state interactive` | Wait for DOM parsed | `cmux browser --surface "$SURFACE" wait --load-state interactive` |
| `wait --function <js>` | Wait for JS condition | `cmux browser --surface "$SURFACE" wait --function '!!window.__APP_READY__'` |
| `wait --timeout <sec>` | Set max wait time | `cmux browser --surface "$SURFACE" wait --selector ".btn" --timeout 20` |

### JavaScript

| Command | Description | Example |
|---------|-------------|---------|
| `eval <js>` | Execute JS | `cmux browser --surface "$SURFACE" eval "document.title"` |
| `addscript <js>` | Inject JS string | `cmux browser --surface "$SURFACE" addscript 'console.log("injected")'` |
| `addstyle <css>` | Inject CSS | `cmux browser --surface "$SURFACE" addstyle "body { outline: 1px solid red; }"` |

### Tab Management

| Command | Description | Example |
|---------|-------------|---------|
| `tab list` | List open tabs | `cmux browser tab list` |
| `tab new <url>` | Open new tab | `cmux browser tab new https://example.com` |
| `tab switch <id>` | Switch tab | `cmux browser tab switch 2` |
| `tab close` | Close current tab | `cmux browser tab close` |

### Session State

| Command | Description | Example |
|---------|-------------|---------|
| `cookies get` | Get cookies | `cmux browser cookies get` |
| `storage local get` | Get local storage | `cmux browser storage local get` |
| `state save <file>` | Save browser state | `cmux browser state save /tmp/session.json` |
| `state load <file>` | Restore browser state | `cmux browser state load /tmp/session.json` |

### Debugging

| Command | Description | Example |
|---------|-------------|---------|
| `console list` | Get console logs | `cmux browser console list` |
| `errors list` | Get JS errors | `cmux browser errors list` |
| `dialog accept` | Accept dialog | `cmux browser dialog accept` |

### Surface Selection

When multiple browsers are open, capture the surface handle once and apply to all commands:

```bash
SURFACE="surface:2"
cmux browser --surface $SURFACE wait --load-state complete --timeout 15
cmux browser --surface $SURFACE snapshot --interactive
```

---

## Test Workflow

### Phase 0: CSP Pre-check (MUST)

`cmux browser` uses `eval()` internally — CSP can block all commands:

```bash
# 1. HTTP response headers — -L follows redirects (http→https, bare domain→www, etc.)
curl -sIL <target-url> | grep -i content-security-policy

# 2. meta tag CSP — fetch HTML body with curl (NOT via cmux browser eval, which is itself
#    blocked when the meta CSP omits unsafe-eval, creating a circular dependency)
curl -sL <target-url> | grep -i 'content-security-policy'

# 3. Quick eval probe — the most reliable live gate once the page is open
#    Open here and reuse $SURFACE in Phase 1 — do NOT open the same URL again
# Inside cmux:
SURFACE=$(cmux browser open <target-url> | grep -oE 'surface:[0-9]+')
# Outside cmux (standalone shell / CI):
# WORKSPACE_ID=<id>  # get from: cmux list-workspaces
# SURFACE=$(cmux browser open <target-url> --workspace "$WORKSPACE_ID" | grep -oE 'surface:[0-9]+')
cmux browser --surface "$SURFACE" wait --load-state complete --timeout 10
cmux browser --surface "$SURFACE" eval "1+1"
# "2" → eval works, proceed. Any error → CSP blocked → switch to Playwright
```

If `script-src` is present without `unsafe-eval` in steps 1 or 2 → ⚠️ likely blocked.
If step 3 eval probe errors → confirmed blocked → switch to Playwright.
If no CSP or `unsafe-eval` is included → proceed.

### Phase 1: Setup with SPA Hydration Wait

```bash
# $SURFACE is already set from Phase 0 — reuse it, do NOT open again
# (reopening creates an orphaned surface and bypasses the CSP probe)

# 2. SPA Hydration Wait Protocol

# Step 1: network-level load gate
cmux browser --surface "$SURFACE" wait --load-state complete --timeout 15

# Step 2: detect SPA (output: "true" or "false")
IS_SPA=$(cmux browser --surface "$SURFACE" eval '!!(window.__NEXT_DATA__||window.__NUXT__||window.__remixContext||window.__SVELTEKIT_DATA__||window.___gatsby||window.__INITIAL_STATE__||window.ng||document.querySelector("[data-reactroot],[data-v-app],[data-server-rendered],[ng-version],[data-svelte-h],[q\\:container]"))' 2>/dev/null | tr -d '"' | tr -d ' \n')

# Step 3A: content-density wait
# On timeout, fall back to selector-based wait (Step 3B) rather than silently continuing —
# proceeding after a failed hydration wait captures the pre-hydration shell
if [ "$IS_SPA" = "true" ]; then
  # Primary: minimal content threshold — works for both sparse (login) and dense (docs) pages
  # loading-state check ensures we wait past skeleton; innerText>30 avoids passing on true empty shell
  cmux browser --surface "$SURFACE" wait --function 'document.readyState==="complete" && !document.querySelector("[aria-busy=true],[data-loading=true]") && document.body.innerText.length>30' --timeout 10 || \
    # Fallback: bare content presence — NOT a structural selector (main/nav exist pre-hydration)
    cmux browser --surface "$SURFACE" wait --function 'document.body.innerText.length>30' --timeout 10 || \
    { echo "Error: hydration wait timed out on a detected SPA — provide an explicit selector via Step 3B and retry" >&2; exit 1; }
else
  # SPA not detected; same minimal check as safety net for undetected SPAs
  cmux browser --surface "$SURFACE" wait --function 'document.readyState==="complete" && !document.querySelector("[aria-busy=true],[data-loading=true]") && document.body.innerText.length>30' --timeout 5 || \
    cmux browser --surface "$SURFACE" wait --function 'document.body.innerText.length>30' --timeout 5 || \
    { echo "Error: hydration wait timed out — page may be an undetected SPA; provide an explicit selector via Step 3B and retry" >&2; exit 1; }
fi

# 3. Snapshot
cmux browser --surface "$SURFACE" snapshot --interactive

# Validation: < 3 elements means truly empty shell (2-node skeleton); retry
# Threshold is 3, not 10 — sparse pages (login/OTP/confirmation) are fully hydrated
# with only 3-8 elements and must not be treated as failures
NODE_COUNT=$(cmux browser --surface "$SURFACE" eval 'document.querySelectorAll("a[href],h1,h2,h3,button,nav,article").length' | tr -d ' \n')
if [ "${NODE_COUNT:-0}" -lt 3 ]; then
  echo "Snapshot validation: only $NODE_COUNT elements — likely empty shell, retrying" >&2
  # Content-density check — NOT a structural selector (main/nav exist pre-hydration)
  cmux browser --surface "$SURFACE" wait --function 'document.readyState==="complete" && !document.querySelector("[aria-busy=true],[data-loading=true]") && document.body.innerText.length>30' --timeout 15 || \
    { echo "Error: hydration retry timed out — provide a specific selector via Step 3B and retry" >&2; exit 1; }
  cmux browser --surface "$SURFACE" snapshot --interactive
fi

# 4. Verify eval works (CSP check)
cmux browser --surface "$SURFACE" eval "1+1"
# "2" → OK. Error → CSP blocked → switch to Playwright
```

### Phase 2: Scenario Execution

**Login form test:**

```bash
# $SURFACE is set from Phase 0/1 — navigate within the same surface
cmux browser --surface "$SURFACE" navigate https://example.com/login
# Re-apply hydration wait after every navigate — destination page may still be hydrating
cmux browser --surface "$SURFACE" wait --load-state complete --timeout 15
cmux browser --surface "$SURFACE" wait --function 'document.body.innerText.length>50 && document.querySelectorAll("a[href],button,input").length>2' --timeout 10 || \
  { echo "Error: login page hydration timed out — provide explicit selector and retry" >&2; exit 1; }
cmux browser --surface "$SURFACE" wait --selector "#email"
cmux browser --surface "$SURFACE" fill "#email" "test@example.com"
cmux browser --surface "$SURFACE" fill "#password" "password123"
cmux browser --surface "$SURFACE" click "button[type='submit']"
# After submit, destination route also needs hydration wait before snapshot
cmux browser --surface "$SURFACE" wait --url-contains "/dashboard"
cmux browser --surface "$SURFACE" wait --load-state complete --timeout 15
cmux browser --surface "$SURFACE" wait --function 'document.body.innerText.length>100 && document.querySelectorAll("a[href],button").length>3' --timeout 10 || \
  { echo "Error: dashboard hydration timed out — provide explicit selector and retry" >&2; exit 1; }
cmux browser --surface "$SURFACE" snapshot --interactive
```

**ReadMe.io SPA documentation:**

```bash
# Navigate within the Phase 0/1 surface — do not open a new one
cmux browser --surface "$SURFACE" navigate https://developers.example.com/reference
cmux browser --surface "$SURFACE" wait --load-state complete --timeout 15

# Step 3B — wait for sidebar render (known ReadMe.io structure)
cmux browser --surface "$SURFACE" wait --selector "[class*='Sidebar'],[class*='rm-Sidebar'],nav.sidebar" --timeout 15

# Snapshot — now includes sidebar and body content
cmux browser --surface "$SURFACE" snapshot --interactive
```

**Extract API endpoints:**

```bash
cmux browser --surface "$SURFACE" navigate https://developers.example.com/reference
cmux browser --surface "$SURFACE" wait --load-state complete --timeout 15
cmux browser --surface "$SURFACE" wait --selector "[data-testid='endpoint-list'], .api-endpoints" --timeout 15

cmux browser --surface "$SURFACE" eval "Array.from(document.querySelectorAll('h2,h3')).map(h => h.textContent.trim()).join('\n')"
```

### Phase 3: Assertions

```bash
cmux browser --surface "$SURFACE" is visible --selector "#success-message"
cmux browser --surface "$SURFACE" get text --selector "#result"
cmux browser --surface "$SURFACE" url
cmux browser --surface "$SURFACE" errors list
cmux browser --surface "$SURFACE" console list
```

### Phase 4: Cleanup

```bash
cmux browser --surface "$SURFACE" state save /tmp/test-session.json
cmux browser --surface "$SURFACE" tab close
```

---

## Error Handling

### Empty Snapshot — SPA Hydration Retry

```bash
# Symptom: snapshot returns 2–5 nodes, no nav/content
# Assumes $SURFACE is set from Phase 1

# Retry 1 — more specific selector
cmux browser --surface "$SURFACE" wait --selector "main > section, article, .page-content" --timeout 15
cmux browser --surface "$SURFACE" snapshot --interactive

# Retry 2 — wait for specific text
cmux browser --surface "$SURFACE" wait --text "API Reference" --timeout 15
cmux browser --surface "$SURFACE" snapshot --interactive

# Retry 3 — query DOM directly via eval
cmux browser --surface "$SURFACE" eval "Array.from(document.querySelectorAll('a[href]')).map(a => a.textContent + ' → ' + a.href).join('\n')"
```

### Common Failure Patterns

| Symptom | Cause | Fix |
|---------|-------|-----|
| Empty snapshot tree | SPA hydration not complete | Apply Hydration Wait Protocol (see above) |
| Selector not found | Page not loaded / wrong selector | `wait --selector` then retry; confirm DOM via snapshot |
| Click not working | Element obscured | `scroll-into-view --selector`, check `is visible` |
| eval error | CSP blocked | Check CSP header, consider Playwright |
| Timeout | Network delay / SPA rendering | Increase `--timeout`, try Step 3B selector |
| Text input fails | No focus / readonly field | `focus` then `fill`; verify with `get attr` |
| Dialog blocking | alert/confirm popup | `dialog accept` or `dialog dismiss` |

---

## Execution Rules

1. **Run SPA Hydration Wait Protocol before any DOM-dependent action** (Iron Law)
2. **Verify after each step** — check state with `snapshot --interactive` or `is` after commands
3. **Wait before acting** — always `wait --selector` or `wait --load-state complete` before click/fill
4. **Validate snapshot results** — run `eval` count check; retry if < 3 (truly empty shell); sparse pages like login/OTP are valid at 3–8 elements
5. **Debug on failure** — immediately collect `snapshot --interactive` + `console list` + `errors list`
6. **Screenshot evidence** — save `screenshot --out /tmp/step-N.png` at key checkpoints
7. **Always thread the surface** — `cmux browser open` outputs `OK surface:N workspace:M`; extract the ref with `SURFACE=$(cmux browser open <url> | grep -oE 'surface:[0-9]+')` and pass `--surface "$SURFACE"` to every subsequent command (only `open`/`open-split`/`new`/`identify` work without it)
8. **Never swallow SPA hydration timeouts** — `|| true` after a hydration wait silently proceeds with a pre-hydration DOM; instead fall back to Step 3B (`wait --selector`) or emit a warning before continuing
