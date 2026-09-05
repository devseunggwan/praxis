"""AGENTS.md word budget (issue #1306).

AGENTS.md (CLAUDE.md is a symlink to it) is loaded into every session, so
every word in it costs context on every turn. This test caps the file so
developer-only content keeps moving to CONTRIBUTING.md instead of accreting
here.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_MD = REPO_ROOT / "AGENTS.md"

# Whitespace-split words via str.split(). Do not compare against `wc -w`: in
# the C locale it skips tokens made only of non-ASCII characters (the em-dash
# and arrow separators this file uses), so it reads ~30 words low. The number
# is deliberately round: a ceiling that forces a move-or-trim decision, not a
# precise token estimate.
WORD_BUDGET = 1_000


def test_agents_md_is_within_word_budget() -> None:
    words = len(AGENTS_MD.read_text(encoding="utf-8").split())
    assert words <= WORD_BUDGET, (
        f"AGENTS.md is {words} whitespace-split words; the budget is {WORD_BUDGET}. "
        "AGENTS.md is loaded into every session, so every word there costs "
        "context on every turn. Move developer-only content (setup, tooling, "
        "procedures never executed in-session) to CONTRIBUTING.md and leave a "
        "one-line pointer in AGENTS.md — see CONTRIBUTING.md → 'Keeping "
        "AGENTS.md small' (issue #1306). Do not raise the budget to make "
        "this pass."
    )
