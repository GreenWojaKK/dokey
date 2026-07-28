"""What a document's own filename says about it.

In the corpus this was built for, the filename is not decoration -- it is where
a document's metadata lives. ``20240315_부서명_T-101_설비명_
사건_문서종류_rev1.2.xlsx`` states a date, an owning department, an equipment tag, the
equipment's name and material, what happened to it, a document-type code and a
revision, none of which need appear inside the file. A lake that keeps only the
text throws that away.

Reading it is deliberately narrow. Only three things are claimed -- a date, an
equipment tag, a revision -- and each is claimed on **form** alone: eight digits
that make a real calendar date, letters-dash-digits in the shape a plant uses
for tag numbers, a revision marker. Nothing is decided by vocabulary. "부서명"
is a department to a reader who knows the organization, and to this module it is
simply a token, kept in order and left alone. That is the same line dokey draws
everywhere else: it records what a document says about itself, and leaves what
the words *mean* to whatever consumes the lake.

The tokens are kept whole and in order for exactly that reason. A consumer with
an organization's own vocabulary can map them; a consumer without one still has
the name as it was written, which is more than a slug.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# Separators a filename uses between fields. Whitespace included: half of the
# measured names separate on spaces and the other half on underscores, and the
# same name often does both.
_SPLIT = re.compile(r"[_\s]+")
# Eight digits that could be a date, wherever they sit -- "요약20240315"
# glues the date to the word before it, with no separator at all.
_DATE8 = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")
# The same date written with separators.
_DATE_SEP = re.compile(r"(?<!\d)(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})(?!\d)")
# An equipment tag: a short letter code, a hyphen, a number, sometimes a
# trailing item letter -- T-101, P-201, HX-3001A. The hyphen is required (a
# bare "T101" is not claimed: too much else has that shape), and a third
# dashed part disqualifies it -- "C-79-2015" is a document number wearing the
# same shape, and this corpus is full of them. The cost is a tag written in
# three parts ("T-101-A"), which is not claimed either.
_TAG = re.compile(
    r"(?<![A-Za-z0-9])([A-Z]{1,4})-(\d{2,5})([A-Z])?(?![A-Za-z0-9])(?!-\d)"
)
# A revision marker: rev2.0, rev.2, Rev-A, v1.1, r3.
_REVISION = re.compile(
    r"(?<![A-Za-z])(?:rev|ver|v|r)[.\-]?\s?(\d+(?:\.\d+)*|[A-Z])(?![A-Za-z0-9])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Found:
    """One recognized field: what was matched, and what it reads as."""

    text: str
    value: str

    def as_dict(self) -> dict:
        return {"text": self.text, "value": self.value}


@dataclass
class DocumentName:
    name: str
    stem: str
    dates: list[Found] = field(default_factory=list)
    tags: list[Found] = field(default_factory=list)
    revision: Found | None = None
    tokens: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "source_name": self.name,
            "stem": self.stem,
            "dates": [item.as_dict() for item in self.dates],
            "tags": [item.as_dict() for item in self.tags],
            "revision": self.revision.as_dict() if self.revision else None,
            "tokens": list(self.tokens),
        }


def _valid_date(year: int, month: int, day: int) -> str | None:
    """The date if the calendar has it, else None.

    A drawing number can be eight digits too. Requiring a real date in a
    plausible year is what separates 20240315 from 10120304.
    """
    if not 1900 <= year <= 2999:
        return None
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def read(path: Path | str) -> DocumentName:
    """Read the filename for the things it states in a recognizable form."""
    path = Path(path)
    stem = path.stem
    found = DocumentName(name=path.name, stem=stem, tokens=_SPLIT.split(stem.strip()))
    found.tokens = [token for token in found.tokens if token]

    seen: set[str] = set()
    for match in _DATE_SEP.finditer(stem):
        iso = _valid_date(*(int(part) for part in match.groups()))
        if iso and match.group(0) not in seen:
            seen.add(match.group(0))
            found.dates.append(Found(text=match.group(0), value=iso))
    for match in _DATE8.finditer(stem):
        iso = _valid_date(*(int(part) for part in match.groups()))
        if iso and match.group(0) not in seen:
            seen.add(match.group(0))
            found.dates.append(Found(text=match.group(0), value=iso))

    tags: list[str] = []
    for match in _TAG.finditer(stem):
        text = match.group(0)
        if text not in tags:
            tags.append(text)
            found.tags.append(Found(text=text, value=text))

    # A tag's own letters must not be read as a revision: the R of R-101 is
    # already spoken for.
    claimed = [match.span() for match in _TAG.finditer(stem)]
    revision = None
    for match in _REVISION.finditer(stem):
        if any(start <= match.start() and match.end() <= end for start, end in claimed):
            continue
        revision = Found(text=match.group(0), value=match.group(1))
    found.revision = revision
    return found


def write_document_json(output_dir: Path, source: Path | str) -> Path:
    """Record what the source document's name says, beside the manifest."""
    silver = Path(output_dir) / "silver"
    silver.mkdir(parents=True, exist_ok=True)
    path = silver / "document.json"
    path.write_text(
        json.dumps(read(source).as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path
