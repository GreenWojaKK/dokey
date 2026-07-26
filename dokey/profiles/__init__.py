"""Language profiles: the parts of unitizing that are not language-neutral.

dokey's core stays general -- a heading is a heading, a repeat is a repeat --
but deciding *what a numbering token means* and *whether a line is prose or a
page mark* needs the conventions of the language the document is written in.
Those conventions live here, one module per language, so that no single
language is wired into the core.

A profile answers three questions about a line of text:

``numbering(title)``
    Does this title carry a numbering token, and how deep does that token sit
    on the document's address ladder? (``5.`` is one rung, ``5.1`` two.)
``is_sentence_tail(text)``
    Is this prose -- the tail of a sentence a page break cut in half -- rather
    than a title? Layout-driven converters promote such fragments to headings.
``key(text)``
    The comparison form used to decide two lines are "the same line", so a
    running mark still counts as a repeat when justification stretched its
    spacing (``목  적`` and ``목적``).

The neutral profile below is the default and assumes nothing beyond Arabic
numerals and Latin punctuation. :func:`resolve` picks a language profile from
the text when asked to (``profile="auto"``), which is how ``dokey auto`` on a
Korean render gets the Korean ladder without the Korean rules being compiled
into every other document's path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Numbering:
    """A numbering token found at the head of a title.

    ``depth`` is the rung on the document's address ladder, 1-based: ``5.`` is
    1, ``5.1`` is 2. ``irregular`` marks a series that is real numbering but
    off the canonical ladder (``1)`` where the document elsewhere uses ``(1)``);
    it is kept and counted, never discarded, so that a document's own
    inconsistency does not silently drop sections.

    ``ordinal`` is the token read as a position -- ``(5, 1)`` for ``5.1``,
    ``(3,)`` for ``③`` -- so that two tokens can be asked whether they are
    consecutive. That question is what lets dokey see a *gap*: something
    standing between ``11.1`` and ``11.2`` took no number in a document that
    numbers everything at that rung.
    """

    kind: str
    depth: int
    label: str
    irregular: bool = False
    ordinal: tuple[int, ...] | None = None


_WS = re.compile(r"\s+")

# Numbering shapes that need no language knowledge. Order matters: the first
# match wins, so the more specific pattern (a decimal chain) precedes the more
# general one (a single integer).
_NEUTRAL_NUMBERING: tuple[tuple[str, re.Pattern[str], int], ...] = (
    ("decimal", re.compile(r"^(\d+(?:\s*\.\s*\d+)+)\s*\.?(?=\s|$|[^\d.])"), 0),
    # 5-4-2: the same ladder written with dashes. Kept tight -- two digits per
    # rung, no spaces -- so that a document code (``10 - 2026``) and a range
    # (``1-2일``) stay out of it.
    ("dash", re.compile(r"^(\d{1,2}(?:-\d{1,2})+)(?=[\s.]|$)"), 0),
    ("integer", re.compile(r"^(\d+)\s*\.(?=\s|$)"), 1),
    ("paren_num", re.compile(r"^(\(\s*\d+\s*\))"), 3),
    ("num_paren", re.compile(r"^(\d+\s*\))(?=\s|$)"), 3),
    ("roman", re.compile(r"^([IVXLC]+|[Ⅰ-Ⅻ]+)\s*[.)](?=\s|$)"), 1),
    ("alpha", re.compile(r"^([A-Za-z])\s*[.)](?=\s)"), 3),
)


class NeutralProfile:
    """Language-neutral defaults: numerals and Latin punctuation only."""

    name = "none"

    def numbering(self, title: str) -> Numbering | None:
        text = title.strip()
        for kind, pattern, depth in _NEUTRAL_NUMBERING:
            match = pattern.match(text)
            if not match:
                continue
            label = match.group(1)
            if kind in ("decimal", "dash"):
                separator = r"\s*-\s*" if kind == "dash" else r"\s*\.\s*"
                parts = [part for part in re.split(separator, label) if part]
                if not _consistent_decimal(parts):
                    return None
                ordinal = tuple(int(part) for part in parts)
                return Numbering(kind, len(parts), label, ordinal=ordinal)
            digits = re.sub(r"\D", "", label)
            return Numbering(
                kind,
                depth,
                label,
                irregular=kind == "num_paren",
                ordinal=(int(digits),) if digits else None,
            )
        return None

    def is_sentence_tail(self, text: str) -> bool:
        return False

    def key(self, text: str) -> str:
        return _WS.sub(" ", text).strip().casefold()

    def join_title(self, head: str, tail: str) -> str:
        """Rejoin a title the layout split in two."""
        return f"{head} {tail}".strip()


def _consistent_decimal(parts: list[str]) -> bool:
    """Reject a decimal *number* wearing a section number's clothes.

    ``0. 5 mm``, ``16.0.26 m3`` and the cover date ``2013. 11.`` tokenize
    exactly like ``5.1`` and ``11.14``. A section number counts up from 1 and
    stays small at every rung, so require both: no zero rung, and no rung longer
    than two digits -- which is what tells a clause number from a year. A
    document that really reaches section 100.1 loses nothing a reader would
    notice.
    """
    if any(not part.isdigit() for part in parts):
        return False
    if any(int(part) == 0 for part in parts):
        return False
    return all(len(part) <= 2 for part in parts)


NEUTRAL = NeutralProfile()


def is_successor(before: Numbering | None, after: Numbering | None) -> bool:
    """Do these two tokens sit next to each other in one numbering series?

    ``5.1`` then ``5.2``: yes. ``5.1`` then ``5.3``: no -- something is missing
    and dokey must not reason as if the run were complete. ``5.1`` then ``6.``:
    no; they are different rungs.
    """
    if before is None or after is None:
        return False
    if before.kind != after.kind:
        return False
    if not before.ordinal or not after.ordinal:
        return False
    if len(before.ordinal) != len(after.ordinal):
        return False
    return (
        before.ordinal[:-1] == after.ordinal[:-1]
        and after.ordinal[-1] == before.ordinal[-1] + 1
    )

_HANGUL = re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏]")
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def detect(text: str, *, sample: int = 200_000) -> str:
    """Name the language profile this text calls for, or ``"none"``.

    Detection is a letter-share test on a leading sample, not a language
    classifier: it only has to be right about which *ladder* applies.
    """
    head = text[:sample]
    letters = len(_LETTER.findall(head))
    if not letters:
        return "none"
    if len(_HANGUL.findall(head)) / letters >= 0.2:
        return "ko"
    return "none"


def resolve(name: str | None, text: str = ""):
    """Return the profile object for ``name`` (``"auto"`` detects from text)."""
    if name is None or name == "auto":
        name = detect(text)
    if name in ("none", "neutral"):
        return NEUTRAL
    if name == "ko":
        from .ko import KOREAN

        return KOREAN
    raise ValueError(f"Unknown profile: {name!r} (known: auto, none, ko)")


AVAILABLE = ("auto", "none", "ko")
