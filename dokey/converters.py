"""A registry of document converters, organized by the evidence each yields.

Converters are not interchangeable engines. They differ in what survives the
conversion: Docling emits a Markdown render *and* a block stream that keeps
page numbers and coordinates; MarkItDown-class tools emit Markdown and nothing
else. A registry that only knew the tools' names would let a paged document
fall silently into a text-only converter and lose its pages without a word.

So the registry's axis is evidence, and its one rule is the one the native
workbook reader established: **demand from a converter exactly the evidence
the source format itself states.**

* A *paged* format (PDF) states pages and coordinates. A converter that keeps
  them is preferred; one that does not may still be used -- it may be all the
  machine has -- but the loss is declared, counted, and recorded, never
  silent.
* A *flow* format (docx, pptx, html, epub) states no pages: pagination there
  is a rendering artifact, not a property of the document. A Markdown-only
  converter loses nothing structural, so the lightest tool present is fully
  adequate.
* A *self-describing* format (xlsx, xls) is read from its own file and never
  reaches a converter at all (see ``sheets.py``).

Discovery mirrors the rest of dokey's bring-your-own seams: whatever is on
PATH or importable is offered; an explicitly saved converter wins, because an
instruction outranks a default -- but even then the evidence rule still gets
to say what was lost.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from . import convert as convertlib

# What each source format states about itself.
PAGED_SUFFIXES = frozenset({".pdf"})
FLOW_SUFFIXES = frozenset({".docx", ".pptx", ".html", ".htm", ".epub"})

# What each known converter yields, and what it accepts. An unknown command
# saved with `dokey convert --set` is assumed to speak the reference
# converter's grammar and yield what it yields; the assumption is documented
# rather than guessed at per run.
_YIELDS: dict[str, frozenset[str]] = {
    "docling": frozenset({"markdown", "blocks"}),
    "markitdown": frozenset({"markdown"}),
    "custom": frozenset({"markdown", "blocks"}),
}
# Spreadsheets are read natively by default, but a converter route stays
# *choosable* for the OOXML kinds -- an instruction outranks a default -- and
# what each route costs is stated where the choice is made. The legacy .xls
# is not offered: the reference converter cannot open one, measured.
_ACCEPTS: dict[str, frozenset[str]] = {
    "docling": PAGED_SUFFIXES
    | FLOW_SUFFIXES
    | frozenset({".xlsx", ".xlsm", ".ods", ".xlsb"}),
    # markitdown does open the OOXML workbook kinds; what it cannot do is
    # yield the block stream that sheet identity travels in, and the
    # require-blocks rule states that refusal precisely.
    "markitdown": PAGED_SUFFIXES | FLOW_SUFFIXES | frozenset({".xlsx", ".xlsm"}),
    "custom": PAGED_SUFFIXES
    | FLOW_SUFFIXES
    | frozenset({".xlsx", ".xlsm", ".ods", ".xlsb"}),
}


def is_flow_document(path: Path) -> bool:
    """A format whose pagination is a rendering artifact, not a statement."""
    return path.suffix.lower() in FLOW_SUFFIXES


def adapter_yields(kind: str) -> frozenset[str]:
    return _YIELDS.get(kind, _YIELDS["custom"])


def accepts(kind: str, suffix: str) -> bool:
    return suffix.lower() in _ACCEPTS.get(kind, _ACCEPTS["custom"])


def accepted_suffixes(kind: str) -> frozenset[str]:
    """Which formats this kind of converter is offered for."""
    return _ACCEPTS.get(kind, _ACCEPTS["custom"])


def yields_label(kind: str) -> str:
    """How much survives this converter, in words a form can show."""
    return (
        "keeps pages (markdown + block stream)"
        if "blocks" in adapter_yields(kind)
        else "markdown only"
    )


def _discover_markitdown() -> convertlib.Converter | None:
    on_path = shutil.which("markitdown")
    if on_path:
        return convertlib.Converter((on_path,), "markitdown")
    if importlib.util.find_spec("markitdown") is not None:
        return convertlib.Converter(
            (sys.executable, "-m", "markitdown"), "markitdown"
        )
    return None


def discover() -> list[convertlib.Converter]:
    """Every converter this machine offers, richest evidence first.

    The order is the preference order: a converter that keeps pages can do
    everything a markdown-only one can, so it never hurts to try it first,
    while the reverse silently costs a paged document its pages.
    """
    found: list[convertlib.Converter] = []
    docling = convertlib.discover_converter()
    if docling is not None:
        found.append(docling)
    markitdown = _discover_markitdown()
    if markitdown is not None:
        found.append(markitdown)
    return found


@dataclass(frozen=True)
class Choice:
    """A converter selected for one input, with what that selection costs.

    ``degraded`` is the evidence rule's verdict: the source states pages and
    the chosen converter does not keep them. The choice may still stand -- an
    explicit instruction, or the only tool present -- but the verdict is
    carried so the ingest can declare the loss instead of implying pages that
    were never read.
    """

    converter: convertlib.Converter
    source: str  # "config" | "discovered"
    yields: frozenset[str]
    degraded: bool

    def display(self) -> str:
        return f"{self.converter.display()} ({self.source}; {yields_label(self.converter.kind)})"


def _pool(
    candidates: list[tuple[str, convertlib.Converter]] | None,
) -> list[tuple[str, convertlib.Converter]]:
    if candidates is not None:
        return candidates
    pool: list[tuple[str, convertlib.Converter]] = []
    saved = convertlib.load_converter()
    if saved is not None:
        pool.append(("config", saved))
    pool.extend(("discovered", found) for found in discover())
    return pool


def _by_preference(
    prefer: str, pool: list[tuple[str, convertlib.Converter]]
) -> convertlib.Converter | None:
    """The converter a name or a command points at.

    A known kind name (``markitdown``, ``docling``) picks that converter from
    what the machine offers -- the saved one first, since it may be a
    particular build of that kind. Anything else is read as a command, the
    same way ``--set`` reads one.
    """
    if prefer in _YIELDS:
        for _source, converter in pool:
            if converter.kind == prefer:
                return converter
        return None
    return convertlib.converter_from_command(prefer)


def choose(
    input_path: Path,
    *,
    require_blocks: bool = False,
    prefer: str | None = None,
    candidates: list[tuple[str, convertlib.Converter]] | None = None,
) -> Choice | None:
    """The converter for this input: flag, then saved setting, then evidence.

    The ladder is the one every dokey seam uses. ``prefer`` is this run's
    instruction and outranks everything -- but an instruction that cannot
    satisfy the caller (a scan needs the block stream; markdown-only tools
    have none) is refused in so many words rather than silently replaced,
    because doing something other than what was asked is worse than stopping.
    A saved converter wins next, whenever it accepts the format at all. Among
    discovered converters the order is evidence order.
    """
    suffix = input_path.suffix.lower()
    pool = _pool(candidates)
    if prefer:
        converter = _by_preference(prefer, pool)
        if converter is None:
            raise SystemExit(
                f"Converter not found on this machine: {prefer}\n"
                "Install it, or name a full command instead."
            )
        if not accepts(converter.kind, suffix):
            raise SystemExit(
                f"{prefer} does not open {suffix or 'this format'}. dokey "
                "reads it directly from the file; drop --converter."
            )
        yields = adapter_yields(converter.kind)
        if require_blocks and "blocks" not in yields:
            raise SystemExit(
                f"{prefer} yields markdown only, and this ingest needs the "
                "block stream -- a scan has nothing else to read, and a "
                "workbook's sheet identity travels in it. Use docling here."
            )
        return Choice(
            converter=converter,
            source="flag",
            yields=yields,
            degraded=suffix in PAGED_SUFFIXES and "blocks" not in yields,
        )
    for source, converter in pool:
        if not accepts(converter.kind, suffix):
            continue
        yields = adapter_yields(converter.kind)
        if require_blocks and "blocks" not in yields:
            continue
        return Choice(
            converter=converter,
            source=source,
            yields=yields,
            degraded=suffix in PAGED_SUFFIXES and "blocks" not in yields,
        )
    return None


def flow_install_hint() -> str:
    return (
        "No converter found for this format. dokey ships none; bring your own.\n"
        "For flow documents (docx, pptx, html, epub) the light option is enough:\n"
        "  pip install markitdown[docx,pptx]   # markdown only, no torch\n"
        "or the reference converter, which also keeps page evidence for PDFs:\n"
        "  pip install docling"
    )
