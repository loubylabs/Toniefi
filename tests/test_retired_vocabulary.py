"""The Review Shelf is gone as a screen, so it has to be gone as a word.

Two scans, because two kinds of source have two different claims on the word.
Product copy and design sources may not say "review" at all: every use there
named a workflow step the product no longer has. A tracked spec may, because a
spec legitimately records the design review that produced it, so specs get the
narrower list of phrases that only ever described the deleted screen.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PRODUCT_SOURCES = ("README.md", "PRODUCT.md", "DESIGN.md", ".impeccable")
BARE_REVIEW = re.compile(r"review", re.IGNORECASE)

SPEC_SOURCES = ("docs/specs",)
RETIRED_PHRASES = re.compile("|".join((
    r"review shelf",
    r"review assignment",
    r"review queue",
    r"review screen",
    r"review step",
    r"review stage",
    r"focused review",
    r"open review",
    r"awaiting review",
    r"pending review",
    r"needs review",
    r"under review",
    r"in review",
    # "to review" also covers "ready to review" and "return to review", and
    # "for review" also covers "ready for review", which is the bare form that
    # let .impeccable/design.json through the gate this replaces.
    r"to review",
    r"for review",
    r"for your review",
    r"/review",
)), re.IGNORECASE)


def _readable_files(names: tuple[str, ...]):
    for name in names:
        target = ROOT / name
        paths = sorted(target.rglob("*")) if target.is_dir() else [target]
        for path in paths:
            if not path.is_file():
                continue
            try:
                yield path, path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # A mock screenshot. There is nothing here to read.
                continue


def _hits(names: tuple[str, ...], pattern: re.Pattern[str]) -> list[str]:
    return [
        f"{path.relative_to(ROOT)}:{number}: {line.strip()}"
        for path, text in _readable_files(names)
        for number, line in enumerate(text.splitlines(), start=1)
        if pattern.search(line)
    ]


def test_no_product_or_design_source_names_the_retired_review_step():
    assert _hits(PRODUCT_SOURCES, BARE_REVIEW) == []


def test_no_tracked_spec_names_the_deleted_review_screen():
    assert _hits(SPEC_SOURCES, RETIRED_PHRASES) == []
