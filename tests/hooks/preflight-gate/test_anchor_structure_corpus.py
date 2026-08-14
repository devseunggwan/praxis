"""Corpus test for `_structure_findings` (issue #996).

Twenty anchors were live on open PRs when this was written, and twelve of them
failed `_heading_match` — which is where the enforcement gap shows: the
PostToolUse side skips the freshness check entirely when the heading does not
parse, so a stale SHA on any of those twelve was never going to be caught.

The corpus below reproduces the *defect shapes* that measurement found, not
the anchors themselves. Those bodies live on another organisation's pull
requests and carry its repo names, PR numbers and claim text; copying them
into this repository would publish that material to keep a regression test
honest, and the test does not need it. What it needs is the structure, so each
case is a minimal sanitized body plus the corrected form of itself.

The paired corrected form is the point. A test that only asserts "the broken
body produces a finding" passes just as well when the checker has started
finding something in everything, so each defect is stated twice: once as the
body that must be caught, and once as the same body repaired, which must come
back clean.

Run: python3 -m pytest tests/hooks/preflight-gate/test_anchor_structure_corpus.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
IMPL = REPO_ROOT / "hooks" / "preflight-gate" / "anchor-comment-gate" / "impl.py"

sys.path.insert(0, str(REPO_ROOT / "hooks" / "_lib"))
_spec = importlib.util.spec_from_file_location("anchor_gate_impl", IMPL)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


SHA = "a1b2c3d4e5"

_TABLE = """
| # | 항목 | 방법 | 결과 |
|---|---|---|---|
| 1 | 엔드포인트가 200 을 준다 | 실호출 | PASS(live) |
"""

_KO_TAIL = """
<details><summary><b>미검증 없음</b></summary>

없음
</details>

<details><summary>1. 엔드포인트 — 실호출</summary>

호출 결과 본문까지 확인했다.

$ curl -s https://example.invalid/health
ok
</details>

<details><summary>갱신 이력</summary>

- rev 1 — 최초 검증.
</details>
"""

_EN_TAIL = _KO_TAIL.replace("미검증 없음", "Unverified — none").replace(
    "1. 엔드포인트 — 실호출", "Evidence 1 — 엔드포인트 실호출"
).replace("갱신 이력", "History")


def _ko(heading: str) -> str:
    return heading + "\n" + _TABLE + _KO_TAIL


def _en(heading: str) -> str:
    return heading + "\n" + _TABLE + _EN_TAIL


# (case id, broken body, repaired body, substring the finding must name)
CORPUS = [
    pytest.param(
        _ko(f"### 검증 — `{SHA}` rev 3"),
        _ko(f"### 검증 — `{SHA}` (rev 3)"),
        "SHA+rev",
        id="ko-rev-without-parens",
    ),
    pytest.param(
        _en(f"### Verification — {SHA} (rev 4)"),
        _en(f"### Verification — `{SHA}` (rev 4)"),
        "SHA+rev",
        id="en-sha-without-backticks",
    ),
    # The heading opens in ko, so every other field is read in ko — and these
    # toggles are written in en. This is the shape that costs body work rather
    # than a one-line heading fix, and it was the majority of the anchors whose
    # defect survived a correct heading.
    pytest.param(
        f"### 검증 — `{SHA}` (rev 5)\n" + _TABLE + _EN_TAIL,
        _ko(f"### 검증 — `{SHA}` (rev 5)"),
        "행별 근거 토글",
        id="ko-heading-with-en-toggles",
    ),
    # Both at once: the heading fails *and* the toggles are in the other
    # dialect. Repairing only the heading leaves the body still failing, which
    # is why "fix the parentheses" was not a complete account of the work.
    pytest.param(
        f"### 검증 — `{SHA}` rev 6\n" + _TABLE + _EN_TAIL,
        _ko(f"### 검증 — `{SHA}` (rev 6)"),
        "SHA+rev",
        id="ko-heading-and-toggles-both",
    ),
]


@pytest.mark.parametrize("broken,repaired,names", CORPUS)
def test_broken_body_is_caught(broken: str, repaired: str, names: str) -> None:
    findings = gate._structure_findings(broken)
    assert findings, "a body with a known defect produced no finding"
    assert any(names in msg for _, msg in findings), (
        f"no finding named {names!r}; got {[m for _, m in findings]}"
    )


@pytest.mark.parametrize("broken,repaired,names", CORPUS)
def test_repaired_body_is_clean(broken: str, repaired: str, names: str) -> None:
    findings = gate._structure_findings(repaired)
    assert findings == [], f"repaired body still flagged: {[m for _, m in findings]}"


@pytest.mark.parametrize("broken,repaired,names", CORPUS)
def test_structure_findings_are_blocking(broken: str, repaired: str, names: str) -> None:
    """Structure is decidable from the body, so nothing here may be `unknown`.

    `unknown` means a check did not run. A structure check always runs — it
    needs no lookup — so a structure finding arriving at that tier would be
    reporting a failure it cannot have had.
    """
    assert {tier for tier, _ in gate._structure_findings(broken)} == {"blocking"}


def test_heading_fix_alone_does_not_clear_a_mixed_body() -> None:
    """The measurement that corrected this plan, as an executable assertion."""
    heading_only_fix = f"### 검증 — `{SHA}` (rev 6)\n" + _TABLE + _EN_TAIL
    findings = gate._structure_findings(heading_only_fix)
    assert findings, "expected the en toggles to keep failing under a ko heading"
    assert all("SHA+rev" not in msg for _, msg in findings)
