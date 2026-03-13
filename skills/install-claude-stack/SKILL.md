---
name: install-claude-stack
description: >
  Install optimal Claude Code plugin stack (superpowers + oh-my-claudecode + context7 + serena).
  Verifies existing installations, installs only missing plugins.
  Triggers on "install claude stack", "setup claude", "claude 환경 설치".
---

# Install Claude Stack

Plugin-based Claude Code optimal stack installation.

## Stack Components

| Plugin | Marketplace | Role |
|--------|-------------|------|
| **superpowers** | `claude-plugins-official` | TDD/workflow, brainstorming, writing-plans |
| **oh-my-claudecode** | `omc` | HUD, 32 agents, autopilot/ralph/ultrawork |
| **context7** | `claude-plugins-official` | Up-to-date library documentation |
| **serena** (optional) | `claude-plugins-official` | Semantic code analysis (requires LSP setup) |

## Usage

```bash
/install-claude-stack                     # Verify → Install missing (interactive)
/install-claude-stack --auto              # Full automatic installation
/install-claude-stack --check             # Verify only (no installation)
/install-claude-stack --module <name>     # Install specific module only
```

### Module Options

| Module | Description |
|--------|-------------|
| `superpowers` | Superpowers plugin only |
| `omc` | oh-my-claudecode plugin only |
| `context7` | Context7 plugin only |
| `serena` | Serena plugin only |

---

## Workflow

### Phase 1: Prerequisites Verification

Check Claude Code CLI is installed:

```bash
claude --version
```

If not installed, guide user to https://claude.ai/code

### Phase 2: Plugin Verification

Check each plugin's installation status:

```bash
# Check enabled plugins in settings.json
cat ~/.claude/settings.json | grep -A 20 "enabledPlugins"
```

**Expected plugins:**
- `superpowers@claude-plugins-official`
- `oh-my-claudecode@omc`
- `context7@claude-plugins-official`
- `serena@claude-plugins-official`

### Phase 3: Installation

Install only missing plugins:

#### 3.1 Add Marketplaces

```
/plugin marketplace add claude-plugins-official
/plugin marketplace add omc
```

#### 3.2 Install Plugins

**Superpowers:**
```
/plugin install superpowers@claude-plugins-official
```

**oh-my-claudecode:**
```
/plugin install oh-my-claudecode@omc
```

**Context7:**
```
/plugin install context7@claude-plugins-official
```

**Serena (optional — skip if LSP not needed):**
```
/plugin install serena@claude-plugins-official
```

### Phase 4: Post-Install Setup

**oh-my-claudecode setup (recommended):**
```
/oh-my-claudecode:omc-setup
```

### Phase 5: Verification

Re-verify all plugins and generate report:

```
╔══════════════════════════════════════════════════════════════╗
║              Claude Stack Installation Report                 ║
╠══════════════════════════════════════════════════════════════╣
║  Plugins                                                      ║
║  ├─ superpowers@claude-plugins-official    ✓                 ║
║  ├─ oh-my-claudecode@omc                   ✓                 ║
║  ├─ context7@claude-plugins-official       ✓                 ║
║  └─ serena@claude-plugins-official         ✓                 ║
╠══════════════════════════════════════════════════════════════╣
║  Features Available                                           ║
║  ├─ TDD/Workflow (superpowers)             ✓                 ║
║  ├─ HUD StatusLine (omc)                   ✓                 ║
║  ├─ 32 Agents (omc)                        ✓                 ║
║  ├─ Autopilot/Ralph/Ultrawork (omc)        ✓                 ║
║  ├─ Latest Docs (context7)                 ✓                 ║
║  └─ Code Analysis (serena)                 ✓                 ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Plugin Details

### superpowers

| Skill | Description |
|-------|-------------|
| `test-driven-development` | TDD enforcement |
| `brainstorming` | Requirements exploration |
| `writing-plans` | Implementation planning |
| `executing-plans` | Plan execution with checkpoints |
| `systematic-debugging` | Structured debugging |
| `verification-before-completion` | Pre-commit verification |

### oh-my-claudecode

| Feature | Description |
|---------|-------------|
| HUD | Real-time status display |
| autopilot | Full autonomous execution |
| ralph | Persistent loop until completion |
| ultrawork | Maximum parallel execution |
| 32 agents | Specialized task agents |

### context7

| Feature | Description |
|---------|-------------|
| `resolve-library-id` | Find library documentation |
| `get-library-docs` | Fetch latest docs |

Usage: Include "use context7" in prompts.

### serena

| Feature | Description |
|---------|-------------|
| Semantic analysis | Code structure understanding |
| Symbol navigation | Find/replace symbols |
| Memory system | Persistent project context |

---

## Error Handling

| Error Type | Handling |
|------------|----------|
| Marketplace not found | Retry add command |
| Plugin install failed | Check network → Retry |
| Plugin conflict | Disable conflicting plugin |

---

## Post-Installation

After successful installation:

```
╔══════════════════════════════════════════════════════════════╗
║                    Installation Complete!                     ║
╠══════════════════════════════════════════════════════════════╣
║  Next Steps:                                                  ║
║                                                              ║
║  1. Run OMC setup:                                           ║
║     /oh-my-claudecode:omc-setup                              ║
║                                                              ║
║  2. Try these commands:                                      ║
║     "use context7: React 19"    # Latest docs               ║
║     "autopilot: build X"        # Auto execution            ║
║     "tdd: implement Y"          # Test-first                ║
║                                                              ║
║  3. View HUD status in terminal statusline                   ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Reference

- [Superpowers](https://github.com/anthropics/claude-code-superpowers)
- [oh-my-claudecode](https://github.com/anthropics/oh-my-claudecode)
- [Context7](https://context7.com/)
- [Serena](https://github.com/oraios/serena)
