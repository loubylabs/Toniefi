"""The Review Shelf is gone as a screen, so it has to be gone as a word.

Four scans. Product copy, design sources, application source and the test
suite may not name the retired step: a paragraph, a message or a test's own
name and docstring pointing at "review" describes a screen that is not in the
build. Those three scans share one pattern, because "preview" has to survive
in all of them. It is the chapter player's own word for playing a track
without sending it, and it is the name of the playlist endpoint the README
documents.

The test-suite scan cannot include this file: its whole job is to spell out
the retired phrasing on purpose, so it is skipped by path rather than pinned
line by line. A handful of tests elsewhere in the suite exist precisely to
assert the word is gone (a 404 on the deleted `/review` route, a docstring
naming what a refusal message must not say); those are pinned as exact-text
exemptions the same way the application scan already pins its one legitimate
use, rather than reworded, because the assertion itself is the point.

A tracked document under docs/ may say "review", because a spec legitimately
records the design review that produced it, so docs/ gets the narrower list of
phrases that only ever described the deleted screen.
"""
from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def _gitignored() -> frozenset[Path]:
    """Every path under ROOT that git is told to ignore.

    A scan root is a directory, and two of them also hold gitignored local
    scratch: `.impeccable` caches per-file state keyed by absolute path, and
    `docs/` carries working notes the repository never tracks. One stale cache
    entry naming a module deleted months ago used to fail this guard on a
    working copy while a clean clone passed it, which is the worst failure a
    gate can have: it reports on files the repository does not carry, and the
    obvious way to quiet it is to prune the scan.

    Ignored files are excluded rather than untracked ones, so a document
    written but not yet staged is still scanned. That is exactly when the
    retired phrasing comes back.
    """
    listing = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z",
         "--others", "--ignored", "--exclude-standard"],
        capture_output=True, check=True, text=True,
    ).stdout
    return frozenset(ROOT / name for name in listing.split("\0") if name)

PRODUCT_SOURCES = ("README.md", "PRODUCT.md", "DESIGN.md", ".impeccable")
APP_SOURCES = ("app",)
# The word boundary is what separates the retired step from "preview" and
# "previewing": the chapter player's own word for playing a track without
# sending it, and the name of the `/api/playlist/preview` endpoint the README
# documents. Neither can be reworded, and neither is the deleted screen.
# Nothing weaker would do: a bare "review" reports every preview control in
# the interface and every mention of that endpoint, and the temptation would
# then be to prune the scan until it went quiet. Nothing stronger is needed
# either, which STILL_CAUGHT below is here to keep proving.
WORKFLOW_REVIEW = re.compile(r"\breview", re.IGNORECASE)

# The phrasings this gate exists to refuse, next to the ones it has to let
# through. A pattern loosened far enough to quiet a scan would pass the second
# list and start failing the first, which is the failure this pins down.
STILL_CAUGHT = (
    "the Review Shelf holds every forged collection",
    "a collection reordered during Review",
    "unpacks in the reviewed order",
    "open for review when forged",
    "ready to review",
)
STILL_ALLOWED = (
    "`POST /api/playlist/preview` returns those numbers",
    "the chapter player previews a track without sending it",
)

# The one legitimate use left in the application, named rather than pattern
# matched. The Impeccable direction contract at the top of the shell records
# the design review that produced this interface, which is something that
# happened during the build, not a step the operator is being sent to. It is
# pinned to its exact line, so rewording it or adding a second use under the
# same exemption fails here and has to be argued for again.
APP_EXEMPTIONS = (
    (
        "app/static/index.html",
        "FINISH: unreviewed and undocumented is unfinished; this build ends with "
        "the finish review, the verdict, and DESIGN.md",
    ),
)

TEST_SOURCES = ("tests",)
# This file is nothing but the retired phrasing, spelled out on purpose so it
# can be matched elsewhere. Scanning it would have the guard fail on its own
# pattern list, which proves nothing about the suite it is meant to police.
TEST_SELF_EXEMPT = "tests/test_retired_vocabulary.py"

# The legitimate uses left in the test suite, named rather than pattern
# matched, the same way the application scan pins its one. Each of these
# exists to assert the retired step is gone, not to describe a workflow the
# build still has, so rewording it would blunt the assertion it makes. Each is
# pinned to its exact line: rewording it, or adding a second use under the
# same exemption, fails here and has to be argued for again.
TEST_EXEMPTIONS = (
    (
        "tests/test_static_shell.py",
        'assert not {"/review", "/review/{slug}"} & paths',
    ),
    (
        "tests/test_static_shell.py",
        'assert client.get("/review").status_code == 404',
    ),
    (
        "tests/test_static_shell.py",
        'assert client.get("/review/the-wind-in-the-willows").status_code == 404',
    ),
    (
        "tests/test_push_batch.py",
        '"""Five preconditions, five messages, none of them naming a review step.',
    ),
)

DOC_SOURCES = ("docs",)
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


def _scanned_files(name: str) -> list[tuple[Path, str]]:
    """Every readable file under one configured root.

    A root that is missing, misspelled or unreadable fails here rather than
    scanning nothing quietly. A guard that reports success on an empty scan is
    worse than no guard, because it certifies a codebase it never opened.
    """
    target = ROOT / name
    assert target.exists(), f"{name} is a configured scan root and is not there."
    paths = sorted(target.rglob("*")) if target.is_dir() else [target]
    files = []
    for path in paths:
        # A stale .pyc still holds the string its source has since dropped, so
        # compiled output is not evidence about the source either way, and a
        # gitignored file is not part of the repository at all.
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path in _gitignored():
            continue
        try:
            files.append((path, path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            # A mock screenshot. There is nothing here to read.
            continue
    assert files, f"{name} contributed no readable file, so nothing was scanned."
    return files


def _hits(names: tuple[str, ...], pattern: re.Pattern[str]) -> list[str]:
    return [
        f"{path.relative_to(ROOT)}:{number}: {line.strip()}"
        for name in names
        for path, text in _scanned_files(name)
        for number, line in enumerate(text.splitlines(), start=1)
        if pattern.search(line)
    ]


def test_the_scan_pattern_still_catches_every_retired_phrasing():
    """The gate that guards the gate.

    Two merges have now brought the word back in prose that had to be rewritten
    by hand. The cheap way out of the next one is to widen the boundary until
    the scan goes quiet, so what the pattern catches is asserted here rather
    than argued for in a comment.
    """
    assert [text for text in STILL_CAUGHT if not WORKFLOW_REVIEW.search(text)] == []
    assert [text for text in STILL_ALLOWED if WORKFLOW_REVIEW.search(text)] == []


def test_no_product_or_design_source_names_the_retired_review_step():
    assert _hits(PRODUCT_SOURCES, WORKFLOW_REVIEW) == []


def test_no_application_source_sends_the_operator_to_a_review():
    """Every message the operator can be shown, plus the source around it.

    A failure here is a message naming a screen this build does not have, and
    an operator told to review something has nowhere to go and no idea what to
    do instead.
    """
    hits = _hits(APP_SOURCES, WORKFLOW_REVIEW)
    for name, text in APP_EXEMPTIONS:
        exempted = [
            hit for hit in hits
            if hit.startswith(f"{name}:") and hit.endswith(f": {text}")
        ]
        # A dead exemption is a rule nobody follows any more, and leaving it
        # would silently re-open the file it names.
        assert len(exempted) == 1, f"{name} no longer carries its exempted line: {text}"
        hits = [hit for hit in hits if hit not in exempted]
    assert hits == []


def test_no_test_names_or_describes_the_retired_review_step():
    """Every test file except this one's own list of the phrases it hunts.

    A failure here is a test whose name or docstring resurrects the retired
    step, the way a merge once brought back "the reviewed order" as if the
    workflow still had one. This file is excluded by path rather than pinned
    line by line, because its job is to enumerate the retired phrasing on
    purpose.
    """
    hits = [
        hit for hit in _hits(TEST_SOURCES, WORKFLOW_REVIEW)
        if not hit.startswith(f"{TEST_SELF_EXEMPT}:")
    ]
    for name, text in TEST_EXEMPTIONS:
        exempted = [
            hit for hit in hits
            if hit.startswith(f"{name}:") and hit.endswith(f": {text}")
        ]
        # A dead exemption is a rule nobody follows any more, and leaving it
        # would silently re-open the test it names.
        assert len(exempted) == 1, f"{name} no longer carries its exempted line: {text}"
        hits = [hit for hit in hits if hit not in exempted]
    assert hits == []


def test_no_tracked_document_names_the_deleted_review_screen():
    """Every tracked file under docs/, not just the specs.

    The reference documentation the README links to lives here too, and it
    carries the same workflow prose the product sources do. A root scoped to
    docs/specs would have let the deleted screen back in one directory over.
    """
    assert _hits(DOC_SOURCES, RETIRED_PHRASES) == []
