---
name: cmux-browser
description: "Browser automation E2E testing via cmux browser CLI — navigation, form input, click, state verification with SPA hydration wait. Triggers on \"cmux browser\", \"cmux 브라우저\"."
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - AskUserQuestion
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

Run this sequence **before every snapshot** (and before the first DOM-dependent action on a new page):

### Step 1 — Load State Wait

```bash
cmux browser wait --load-state complete --timeout 15
```

Network-level load gate. Necessary but not sufficient for SPAs.

### Step 2 — SPA Auto-detect

```bash
cmux browser eval '!!(window.__NEXT_DATA__||window.__NUXT__||window.__remixContext||window.__SVELTEKIT_DATA__||window.___gatsby||window.__INITIAL_STATE__||window.ng||document.querySelector("[data-reactroot],[data-v-app],[data-server-rendered],[ng-version],[data-svelte-h],[q\\:container]"))'
```

- Output `true` → SPA framework detected → proceed to Step 3A or 3B
- Output `false`/`null` or detection fails → run Step 3A with a short timeout (3s) then Step 4
  (Step 3A passes instantly on static pages — no performance penalty)

### Step 3A — Content-Density Hydration Wait (default)

Wait until real content is rendered into the DOM:

```bash
# Single-quote JS string — no shell escaping issues with inner double-quotes
cmux browser wait --function 'document.readyState==="complete" && document.body.innerText.length>200 && document.querySelectorAll("a[href],button").length>5 && !document.querySelector("[aria-busy=true],[data-loading=true]")' --timeout 10
```

- `innerText.length > 200` — actual text content rendered (DOM node count is unreliable; loading skeletons can produce 50–100+ nodes before hydration)
- `a[href],button > 5` — interactive elements rendered (nav/sidebar signal)
- `aria-busy`, `data-loading` — loading state resolved
- Unquoted attribute selectors (`[aria-busy=true]`) are valid per CSS spec

### Step 3B — Explicit Selector Wait (precision control)

Wait for a known element to appear. Use when you know the site's DOM structure:

```bash
# Navigation rendered
cmux browser wait --selector "nav, aside, [role='navigation']" --timeout 10

# Content container non-empty
cmux browser wait --selector "main article, .content > *:not(:empty)" --timeout 10

# Specific text appeared
cmux browser wait --text "API Reference" --timeout 10
```

Use Step 3B when the target site is known. Fall back to Step 3A + snapshot validation otherwise.

### Step 4 — Snapshot

```bash
cmux browser snapshot --interactive
```

### Snapshot Result Validation (required)

After snapshot, quantitatively verify hydration is complete:

```bash
cmux browser eval 'document.querySelectorAll("a[href],h1,h2,h3,button,nav,article").length'
```

- **< 10** → likely pre-hydration capture → retry with Step 3B or increase timeout
- **≥ 10** → hydration complete, proceed

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

### DOM Interaction

| Command | Description | Example |
|---------|-------------|---------|
| `click <selector>` | Click element | `cmux browser click "button:has-text('Submit')"` |
| `dblclick <selector>` | Double-click | `cmux browser dblclick ".editable-cell"` |
| `hover <selector>` | Mouse hover | `cmux browser hover ".tooltip-trigger"` |
| `focus <selector>` | Focus element | `cmux browser focus "#email"` |
| `check <selector>` | Check checkbox | `cmux browser check "#agree"` |
| `uncheck <selector>` | Uncheck checkbox | `cmux browser uncheck "#newsletter"` |

### Text Input

| Command | Description | Example |
|---------|-------------|---------|
| `type <selector> <text>` | Simulate keystrokes | `cmux browser type "#search" "query"` |
| `fill <selector> <value>` | Set field value (replaces existing) | `cmux browser fill "#email" "test@example.com"` |
| `press <key>` | Press keyboard key | `cmux browser press Enter` |
| `scroll` | Scroll page | `cmux browser scroll --dy 300` |

### Page Inspection

| Command | Description | Example |
|---------|-------------|---------|
| `snapshot [--interactive\|-i]` | Capture accessibility tree | `cmux browser snapshot --interactive` |
| `screenshot [--out <path>]` | Save screenshot | `cmux browser screenshot --out /tmp/test.png` |
| `get <prop> [--selector <css>]` | Get element property | `cmux browser get text --selector "#status"` |
| `is <state> [--selector <css>]` | Check element state | `cmux browser is visible --selector "#modal"` |
| `find <role\|text\|...>` | Find element | `cmux browser find role button` / `cmux browser find text "Submit"` / `cmux browser find nth --index 2 --selector "li"` |
| `highlight [--selector <css>]` | Highlight element | `cmux browser highlight ".error"` |

### Wait

| Command | Description | Example |
|---------|-------------|---------|
| `wait --selector <css>` | Wait for selector to appear | `cmux browser wait --selector ".loaded"` |
| `wait --text <text>` | Wait for text to appear | `cmux browser wait --text "Done"` |
| `wait --url <pattern>` | Wait for URL change | `cmux browser wait --url "/dashboard"` |
| `wait --load-state complete` | Wait for document.readyState complete | `cmux browser wait --load-state complete` |
| `wait --load-state interactive` | Wait for DOM parsed | `cmux browser wait --load-state interactive` |
| `wait --function <js>` | Wait for JS condition | `cmux browser wait --function '!!window.__APP_READY__'` |
| `wait --timeout <sec>` | Set max wait time | `cmux browser wait --selector ".btn" --timeout 20` |

### JavaScript

| Command | Description | Example |
|---------|-------------|---------|
| `eval <js>` | Execute JS | `cmux browser eval "document.title"` |
| `addscript <js>` | Inject JS string | `cmux browser addscript 'console.log("injected")'` |
| `addstyle <css>` | Inject CSS | `cmux browser addstyle "body { outline: 1px solid red; }"` |

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
# 1. HTTP response header
curl -sI <target-url> | grep -i content-security-policy

# 2. meta tag CSP (SPAs often inject this via JS bundle)
cmux browser open <target-url>
cmux browser wait --load-state complete --timeout 10
cmux browser eval 'document.querySelector("meta[http-equiv=\"Content-Security-Policy\"]")?.content || "no meta CSP"'
```

If `script-src` is present without `unsafe-eval` → ⚠️ eval/click blocked → switch to Playwright.
If no CSP or `unsafe-eval` is included → proceed.

### Phase 1: Setup with SPA Hydration Wait

```bash
# 1. Open URL
cmux browser open <target-url>

# 2. SPA Hydration Wait Protocol

# Step 1: network-level load gate
cmux browser wait --load-state complete --timeout 15

# Step 2: detect SPA (output: "true" or "false")
IS_SPA=$(cmux browser eval '!!(window.__NEXT_DATA__||window.__NUXT__||window.__remixContext||window.__SVELTEKIT_DATA__||window.___gatsby||window.__INITIAL_STATE__||window.ng||document.querySelector("[data-reactroot],[data-v-app],[data-server-rendered],[ng-version],[data-svelte-h]"))' 2>/dev/null | tr -d '"' | tr -d ' \n')

# Step 3A: content-density wait for SPA; 3s fast path for static pages
if [ "$IS_SPA" = "true" ]; then
  cmux browser wait --function 'document.readyState==="complete" && document.body.innerText.length>200 && document.querySelectorAll("a[href],button").length>5 && !document.querySelector("[aria-busy=true],[data-loading=true]")' --timeout 10 || true
else
  cmux browser wait --function 'document.readyState==="complete"' --timeout 3 || true
fi

# 3. Snapshot
cmux browser snapshot --interactive

# Validation: count interactive elements (< 10 → retry with Step 3B)
cmux browser eval 'document.querySelectorAll("a[href],h1,h2,h3,button,nav,article").length'

# 4. Verify eval works (CSP check)
cmux browser eval "1+1"
# "2" → OK. Error → CSP blocked → switch to Playwright
```

### Phase 2: Scenario Execution

**Login form test:**

```bash
cmux browser navigate /login
cmux browser wait --selector "#email"
cmux browser fill "#email" "test@example.com"
cmux browser fill "#password" "password123"
cmux browser click "button:has-text('Login')"
cmux browser wait --url "/dashboard"
cmux browser snapshot --interactive
```

**ReadMe.io SPA documentation:**

```bash
cmux browser open https://developers.example.com/reference

# Step 1
cmux browser wait --load-state complete --timeout 15

# Step 3B — wait for sidebar render (known ReadMe.io structure)
cmux browser wait --selector "[class*='Sidebar'],[class*='rm-Sidebar'],nav.sidebar" --timeout 15

# Snapshot — now includes sidebar and body content
cmux browser snapshot --interactive
```

**Extract API endpoints:**

```bash
cmux browser open https://developers.example.com/reference
cmux browser wait --load-state complete --timeout 15
cmux browser wait --selector "[data-testid='endpoint-list'], .api-endpoints" --timeout 15

cmux browser eval "Array.from(document.querySelectorAll('h2,h3')).map(h => h.textContent.trim()).join('\n')"
```

### Phase 3: Assertions

```bash
cmux browser is visible --selector "#success-message"
cmux browser get text --selector "#result"
cmux browser url
cmux browser errors list
cmux browser console list
```

### Phase 4: Cleanup

```bash
cmux browser state save /tmp/test-session.json
cmux browser tab close
```

---

## Error Handling

### Empty Snapshot — SPA Hydration Retry

```bash
# Symptom: snapshot returns 2–5 nodes, no nav/content

# Retry 1 — more specific selector
cmux browser wait --selector "main > section, article, .page-content" --timeout 15
cmux browser snapshot --interactive

# Retry 2 — wait for specific text
cmux browser wait --text "API Reference" --timeout 15
cmux browser snapshot --interactive

# Retry 3 — query DOM directly via eval
cmux browser eval "Array.from(document.querySelectorAll('a[href]')).map(a => a.textContent + ' → ' + a.href).join('\n')"
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
4. **Validate snapshot results** — run `eval` count check; retry with Step 3B if < 10
5. **Debug on failure** — immediately collect `snapshot --interactive` + `console list` + `errors list`
6. **Screenshot evidence** — save `screenshot --out /tmp/step-N.png` at key checkpoints
7. **Fix surface early** — in multi-surface environments, capture surface into `$SURFACE` in Phase 1 and pass `--surface $SURFACE` to every subsequent command
