"""Re-export shim for the tokenizer that used to live in this module.

Issue #1305 split the former 1,468-line module into four siblings under
`hooks/_lib/`, listed here in dependency order:

- `_shell_tokenize` — heredoc / quote / comment scanning, `safe_tokenize`,
  `iter_command_starts`, `strip_prefix`, the gh-binary helpers. Standard
  library only; the other three import from it and nothing imports back.
- `_subst` — active `$( … )` / backtick walking (`iter_command_texts`).
- `_compound` — compound / state-changing classification and the
  `compound_cascade_hint` advisory.
- `_roles` — the typed `Token` / `TokenRole` API (`tokenize_with_roles`,
  `filter_argv`).

Every name that was importable from here before the split — public and
underscore-prefixed alike — is re-exported below, so the existing
`from _hook_utils import …` preamble in each `impl.py` keeps working
unchanged. New code should import from the sub-module that defines the
name rather than from this shim. `tests/test_hook_utils_surface.py` pins
the surface, so a later edit cannot drop a name silently.

History that used to head this file: the shared pipeline was extracted
from three duplicated hook copies in issue #139, and issue #263 added the
role-aware token API after the PR #251 / #252 review rounds converged on
ad-hoc role-state reconstruction as the root cause of seven defects.
"""
from __future__ import annotations

from _shell_tokenize import (
    ENV_ASSIGN_RE,
    GH_MERGE_VALUE_FLAGS,
    HELP_FLAGS,
    PREFIX_WRAPPERS,
    SHELL_KEYWORDS,
    SHELL_SEPARATORS,
    WRAPPER_OPTS_WITH_ARG,
    _ACK_MARKER_RE,
    _EXPANSION_START,
    _GROUP_PREFIX_CHARS,
    _HEREDOC_WORD_CHARS,
    _WORD_BOUNDARY_CHARS,
    _command_spec_key,
    _heredoc_starts_on_line,
    _is_gh_binary,
    _logical_lines,
    _quote_open_at_eol,
    _read_heredoc_delim,
    _starts_unquoted_comment,
    _strip_trailing_comment,
    _unquoted_comment_start,
    has_shell_expansion,
    has_unclosed_expansion,
    heredoc_bodies,
    heredoc_delimiters,
    heredoc_sources,
    is_help_invocation,
    iter_command_starts,
    safe_tokenize,
    strip_heredoc_bodies,
    strip_prefix,
)
from _subst import (
    MAX_SUBST_DEPTH,
    _active_substitutions,
    _closing_backtick,
    _closing_paren,
    iter_command_texts,
)
from _compound import (
    COMPOUND_CASCADE_HINT,
    STATE_CHANGING_COMMANDS,
    _CURL_OUTPUT_FLAGS,
    _WGET_OUTPUT_FLAGS,
    _segment_has_redirect,
    _segment_has_state_change,
    compound_cascade_hint,
    has_state_changing_redirect,
    is_compound_command,
)
from _roles import (
    Token,
    TokenRole,
    _coalesce_subst_runs,
    _resolve_subcommand,
    filter_argv,
    tokenize_with_roles,
)

# The complete pre-split surface, grouped by the module that now defines
# each name. Kept explicit so ruff sees every import as used and so the
# surface test can compare this list against the frozen origin/main one.
__all__ = [
    # _shell_tokenize
    "ENV_ASSIGN_RE",
    "GH_MERGE_VALUE_FLAGS",
    "HELP_FLAGS",
    "PREFIX_WRAPPERS",
    "SHELL_KEYWORDS",
    "SHELL_SEPARATORS",
    "WRAPPER_OPTS_WITH_ARG",
    "_ACK_MARKER_RE",
    "_EXPANSION_START",
    "_GROUP_PREFIX_CHARS",
    "_HEREDOC_WORD_CHARS",
    "_WORD_BOUNDARY_CHARS",
    "_command_spec_key",
    "_heredoc_starts_on_line",
    "_is_gh_binary",
    "_logical_lines",
    "_quote_open_at_eol",
    "_read_heredoc_delim",
    "_starts_unquoted_comment",
    "_strip_trailing_comment",
    "_unquoted_comment_start",
    "has_shell_expansion",
    "has_unclosed_expansion",
    "heredoc_bodies",
    "heredoc_delimiters",
    "heredoc_sources",
    "is_help_invocation",
    "iter_command_starts",
    "safe_tokenize",
    "strip_heredoc_bodies",
    "strip_prefix",
    # _subst
    "MAX_SUBST_DEPTH",
    "_active_substitutions",
    "_closing_backtick",
    "_closing_paren",
    "iter_command_texts",
    # _compound
    "COMPOUND_CASCADE_HINT",
    "STATE_CHANGING_COMMANDS",
    "_CURL_OUTPUT_FLAGS",
    "_WGET_OUTPUT_FLAGS",
    "_segment_has_redirect",
    "_segment_has_state_change",
    "compound_cascade_hint",
    "has_state_changing_redirect",
    "is_compound_command",
    # _roles
    "Token",
    "TokenRole",
    "_coalesce_subst_runs",
    "_resolve_subcommand",
    "filter_argv",
    "tokenize_with_roles",
]
