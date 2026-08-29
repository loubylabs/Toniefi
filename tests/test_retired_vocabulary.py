"""The Review Shelf is gone as a screen, so it has to be gone as a word.

Three scans, because three kinds of source have three different claims on the
word. Product copy and design sources may not say "review" at all: every use
there named a workflow step the product no longer has. Application source may
not say it either, because a message that tells the operator to review
something names a screen that is not in the build, but "preview" is the audio
player's own word for something else and stays. A tracked spec may say it,
because a spec legitimately records the design review that produced it, so
specs get the narrower list of phrases that only ever described the deleted
screen.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PRODUCT_SOURCES = ("README.md", "PRODUCT.md", "DESIGN.md", ".impeccable")
BARE_REVIEW = re.compile(r"review", re.IGNORECASE)

APP_SOURCES = ("app",)
# The word boundary is what separates the retired step from "preview" and
# "previewing", which are the chapter player's own word for playing a track
# without sending it. Nothing weaker would do: a bare "review" reports every
# preview control in the interface, and the temptation would then be to prune
# the scan until it went quiet.
WORKFLOW_REVIEW = re.compile(r"\breview", re.IGNORECASE)

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
        # compiled output is not evidence about the source either way.
        if not path.is_file() or "__pycache__" in path.parts:
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


def test_no_product_or_design_source_names_the_retired_review_step():
    assert _hits(PRODUCT_SOURCES, BARE_REVIEW) == []


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


def test_no_tracked_spec_names_the_deleted_review_screen():
    assert _hits(SPEC_SOURCES, RETIRED_PHRASES) == []
