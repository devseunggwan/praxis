#!/usr/bin/env python3
"""Shared AskUserQuestion option-text collector (B3, #599).

`collect_option_texts` was duplicated byte-for-byte (modulo one docstring
line) in two advisory-nudge hooks:
  - output-block-falsify-advisory/impl.py  (T2 confidence-anchoring scan)
  - pre-output-falsification-gate/impl.py  (Lane A evaluative-marker scan)

Both now import from here so the option-flattening logic cannot drift across
the two gates.

Scope note (B3 minimal, #599): ONLY this collector is shared. The
`(Recommended)`/`Falsified:` token taxonomies stay per-hook — output-block T1
hard-`deny`s on a literal `(Recommended)` label, while pre-output Lane A is
stderr-advisory only. Unifying those token sets would blur the deny-vs-advisory
boundary and is intentionally NOT done here. The `_has_falsified_line` helper
likewise stays in output-block: it has a single consumer (pre-output uses a
different `if .* wrong` regex gate, not a `Falsified:` exact-prefix check), so
hoisting it would be premature abstraction.
"""
from __future__ import annotations


def collect_option_texts(options: list) -> list[str]:
    """Collect each option's label + description into a single text list.

    Non-dict entries are skipped; only string label/description values are
    included. Order is label-then-description, per option, in input order.
    """
    texts: list[str] = []
    for o in options:
        if not isinstance(o, dict):
            continue
        label = o.get("label")
        if isinstance(label, str):
            texts.append(label)
        desc = o.get("description")
        if isinstance(desc, str):
            texts.append(desc)
    return texts
