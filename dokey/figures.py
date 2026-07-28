"""Which caption names which figure, when the converter did not say.

A caption is a sentence that belongs to something other than itself, and the
something is above it or below it. Which one is a **convention**, not a fact of
layout: measured over 866 Korean technical standards, a figure's caption sits
below it (2,400 pairs against 90) and a table's sits above it (1,483 against
184) -- opposite answers for objects that look alike on the page. So neither is
assumed. Each document is asked what it does, from the pairs the converter
already bound, and the corpus figures above are only a prior for a document
that binds none of its own.

The converter binds what it can and leaves the rest: of 4,140 pictures it
emitted, 1,745 carry a caption and 2,395 do not; of 4,241 tables, 847 do. And
662 caption-labelled blocks are bound to nothing at all -- text that announces
itself as naming something, naming nothing. Those are the ones worth placing,
because a caption without its object is a sentence about a picture nobody can
find, and an object without its caption is an unnamed picture.

What is written down is the binding **and how it was decided**: the converter's
own, the document's induced convention, or the corpus prior. A reader can then
believe the first kind and check the third. Objects that stay unbound are
counted rather than guessed at -- plenty of figures simply have no caption.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# What the corpus does, for a document that gives no evidence of its own.
CORPUS_PRIOR = {"picture": "below", "table": "above"}
# A caption belongs to the object it is set against, not to one across the
# page. In points, at 72/inch, this is about two inches -- wide enough for a
# caption separated from its figure by a rule or a gap, narrow enough that the
# next block down the page is not swept in.
MAX_GAP = 150.0


@dataclass(frozen=True)
class Figure:
    kind: str  # "picture" or "table"
    page: int
    ref: str
    caption: str | None = None
    caption_ref: str | None = None
    basis: str | None = None  # converter | induced | prior
    side: str | None = None  # where the caption sits: above | below
    gap: float | None = None
    section: str | None = None
    section_index: int | None = None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "page": self.page,
            "ref": self.ref,
            "caption": self.caption,
            "caption_ref": self.caption_ref,
            "basis": self.basis,
            "side": self.side,
            "gap": None if self.gap is None else round(self.gap, 1),
            "section": self.section,
            "section_index": self.section_index,
        }


@dataclass
class FigureReport:
    pictures: int = 0
    tables: int = 0
    from_converter: int = 0
    induced: int = 0
    from_prior: int = 0
    unbound_objects: int = 0
    unbound_captions: int = 0
    conventions: dict = field(default_factory=dict)

    def summary(self) -> str:
        conventions = ", ".join(
            f"{kind} {side}" for kind, side in sorted(self.conventions.items())
        )
        return (
            f"{self.pictures} picture(s), {self.tables} table(s); "
            f"captions {self.from_converter} from the converter, "
            f"{self.induced} by the document's own convention, "
            f"{self.from_prior} by the corpus prior; "
            f"{self.unbound_objects} object(s) and {self.unbound_captions} "
            f"caption(s) left unbound"
            + (f" [{conventions}]" if conventions else "")
        )

    def as_dict(self) -> dict:
        return {
            "pictures": self.pictures,
            "tables": self.tables,
            "captions_from_converter": self.from_converter,
            "captions_induced": self.induced,
            "captions_from_prior": self.from_prior,
            "objects_unbound": self.unbound_objects,
            "captions_unbound": self.unbound_captions,
            "conventions": dict(self.conventions),
        }


def _box(item: dict) -> tuple[dict, int | None]:
    prov = (item.get("prov") or [{}])[0]
    return prov.get("bbox") or {}, prov.get("page_no")


def _top(box: dict) -> float:
    """The upper edge, in a coordinate system where larger means higher.

    PDF space puts the origin at the bottom left, so ``t`` is already the top
    edge and larger is higher. A spreadsheet's grid is the other way up, and
    its rows count downwards, so the sign is flipped.
    """
    if str(box.get("coord_origin", "BOTTOMLEFT")).upper() == "TOPLEFT":
        return -float(box.get("t", 0.0))
    return float(box.get("t", 0.0))


def _bottom(box: dict) -> float:
    if str(box.get("coord_origin", "BOTTOMLEFT")).upper() == "TOPLEFT":
        return -float(box.get("b", 0.0))
    return float(box.get("b", 0.0))


def _side_and_gap(caption: dict, obj: dict) -> tuple[str, float]:
    """Where the caption sits relative to the object, and how far off."""
    if _top(caption) > _top(obj):
        return "above", max(0.0, _bottom(caption) - _top(obj))
    return "below", max(0.0, _bottom(obj) - _top(caption))


def _kind_of(container: str) -> str:
    return "picture" if container == "pictures" else "table"


def _induce(sides: dict[str, list[str]]) -> dict[str, str]:
    """Each kind's convention, from the document's own bound pairs."""
    convention = {}
    for kind, observed in sides.items():
        if not observed:
            continue
        above = observed.count("above")
        convention[kind] = "above" if above * 2 > len(observed) else "below"
    return convention


def read_figures(
    blocks_path: Path, sections: list | None = None, pages: list | None = None
) -> tuple[list[Figure], FigureReport]:
    """Bind every caption to the object it names, and say how it was decided.

    ``sections`` and ``pages`` (the same page ranges the manifest is built
    from) place each object in the section that contains it, so a figure can be
    cited the way a clause is.
    """
    document = json.loads(Path(blocks_path).read_text(encoding="utf-8"))
    texts = {item.get("self_ref"): item for item in document.get("texts") or []}
    report = FigureReport()

    objects: list[tuple[str, dict]] = []
    for container in ("pictures", "tables"):
        for item in document.get(container) or []:
            objects.append((_kind_of(container), item))
    report.pictures = sum(1 for kind, _ in objects if kind == "picture")
    report.tables = sum(1 for kind, _ in objects if kind == "table")

    # First pass: what the converter already decided, and what it implies about
    # this document's habits.
    bound_caption_refs: set[str] = set()
    sides: dict[str, list[str]] = {"picture": [], "table": []}
    decided: dict[str, tuple[dict, str, float, str]] = {}
    for kind, item in objects:
        captions = item.get("captions") or []
        if not captions:
            continue
        obj_box, obj_page = _box(item)
        for reference in captions:
            ref = reference.get("$ref")
            caption = texts.get(ref)
            bound_caption_refs.add(ref)
            if caption is None:
                continue
            cap_box, cap_page = _box(caption)
            if not obj_box or not cap_box or cap_page != obj_page:
                decided[item["self_ref"]] = (caption, "converter", 0.0, "")
                continue
            side, gap = _side_and_gap(cap_box, obj_box)
            sides[kind].append(side)
            decided[item["self_ref"]] = (caption, "converter", gap, side)
            break

    convention = _induce(sides)
    report.conventions = dict(convention)

    # Second pass: the captions nobody claimed, offered to the objects that
    # have none, on the side this document puts them.
    free_captions = [
        item
        for item in document.get("texts") or []
        if item.get("label") == "caption" and item.get("self_ref") not in bound_caption_refs
    ]
    for kind, item in objects:
        if item["self_ref"] in decided:
            continue
        obj_box, obj_page = _box(item)
        if not obj_box or obj_page is None:
            continue
        wanted = convention.get(kind) or CORPUS_PRIOR[kind]
        basis = "induced" if kind in convention else "prior"
        best: tuple[float, dict, str] | None = None
        for caption in free_captions:
            cap_box, cap_page = _box(caption)
            if cap_page != obj_page or not cap_box:
                continue
            side, gap = _side_and_gap(cap_box, obj_box)
            if side != wanted or gap > MAX_GAP:
                continue
            if best is None or gap < best[0]:
                best = (gap, caption, side)
        if best is None:
            continue
        gap, caption, side = best
        decided[item["self_ref"]] = (caption, basis, gap, side)
        free_captions.remove(caption)

    figures: list[Figure] = []
    for kind, item in objects:
        _, page = _box(item)
        chosen = decided.get(item["self_ref"])
        section_title, section_index = _locate(page, sections, pages)
        if chosen is None:
            report.unbound_objects += 1
            figures.append(
                Figure(
                    kind=kind,
                    page=page or 0,
                    ref=item.get("self_ref", ""),
                    section=section_title,
                    section_index=section_index,
                )
            )
            continue
        caption, basis, gap, side = chosen
        counter = {
            "converter": "from_converter",
            "induced": "induced",
            "prior": "from_prior",
        }[basis]
        setattr(report, counter, getattr(report, counter) + 1)
        figures.append(
            Figure(
                kind=kind,
                page=page or 0,
                ref=item.get("self_ref", ""),
                caption=(caption.get("orig") or caption.get("text") or "").strip(),
                caption_ref=caption.get("self_ref"),
                basis=basis,
                side=side or None,
                gap=gap,
                section=section_title,
                section_index=section_index,
            )
        )
    report.unbound_captions = len(free_captions)
    return figures, report


def _locate(
    page: int | None, sections: list | None, pages: list | None
) -> tuple[str | None, int | None]:
    """The section whose pages contain this one."""
    if page is None or not sections or not pages:
        return None, None
    for index, (section, span) in enumerate(zip(sections, pages), start=1):
        start, end = span
        if start <= page <= end:
            return section.title, index
    return None, None


def write_figures(output_dir: Path, figures: list[Figure]) -> Path:
    silver = Path(output_dir) / "silver"
    silver.mkdir(parents=True, exist_ok=True)
    path = silver / "figures.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for figure in figures:
            handle.write(json.dumps(figure.as_dict(), ensure_ascii=False) + "\n")
    return path
