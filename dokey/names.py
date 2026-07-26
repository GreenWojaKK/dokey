from __future__ import annotations

import re


class ArtifactNamer:
    """Assign each section its artifact folder and file name.

    The name says the section's title and nothing else. Earlier layouts
    prefixed both the folder and the file with ordinals
    (``003_1_목_적/001_1_목_적.md``), which repeated three times over what the
    title already said -- and where a section is its own parent, the folder
    held exactly one file. So: a section that is its own parent gets no folder,
    a child sits in a folder named after its parent, and the ordinal is dropped.
    Order and page ranges live in the manifest, which is where a consumer reads
    them anyway.

    Uniqueness still has to hold, because a compound document restarts its
    numbering and can hold two sections called ``1. 목적``. The second one to
    appear gets a ``_2`` suffix rather than overwriting the first.
    """

    def __init__(self) -> None:
        self._used: dict[str, set[str]] = {}

    def name(self, *, title: str, parent: str, suffix: str) -> tuple[str, str]:
        folder = "" if not parent or parent == title else slugify(parent)
        stem = slugify(title)
        taken = self._used.setdefault(folder, set())
        candidate, ordinal = stem, 2
        while candidate.casefold() in taken:
            candidate = f"{stem}_{ordinal}"
            ordinal += 1
        taken.add(candidate.casefold())
        return folder, f"{candidate}{suffix}"


def slugify(value: str, max_length: int = 90) -> str:
    # Keep Unicode word characters so a Korean/CJK section title stays a
    # readable folder name ("제1절_사업의_개념") instead of degrading to its
    # stray ASCII digits; punctuation and filesystem-hostile characters all
    # fall in the non-word class and are collapsed to underscores.
    slug = re.sub(r"\W+", "_", value).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return (slug or "untitled")[:max_length].rstrip("_")
