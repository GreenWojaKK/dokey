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
# What each converter is *known* to handle well. This is an ordering hint and
# nothing more: it may put the likeliest tool first, and it may not refuse.
#
# A list of accepted extensions is a prediction, and a prediction about
# someone else's program drifts -- this one did, twice, and each time a format
# the tool had always opened was unreachable until a user was refused for no
# reason. The tools are the authority on what they open, and the only way to
# ask them that cannot be wrong is to run them. So dokey tries, and reports
# what came back. Being told "that failed, here is what it said" after a few
# seconds beats being told "not supported" by a table that was never true.
_KNOWN_FOR: dict[str, frozenset[str]] = {
    "docling": PAGED_SUFFIXES | FLOW_SUFFIXES | frozenset({".xlsx", ".xlsm", ".ods"}),
    # markitdown's render keeps sheet identity as markdown headings, one per
    # sheet, and drops everything else the file states: pictures, text boxes,
    # merges, coordinates. That cost is stated where the choice is made.
    "markitdown": PAGED_SUFFIXES | FLOW_SUFFIXES | frozenset({".xlsx", ".xls"}),
    "custom": PAGED_SUFFIXES | FLOW_SUFFIXES | frozenset({".xlsx", ".xlsm", ".ods"}),
}


def is_flow_document(path: Path) -> bool:
    """A format whose pagination is a rendering artifact, not a statement."""
    return path.suffix.lower() in FLOW_SUFFIXES


def adapter_yields(kind: str) -> frozenset[str]:
    return _YIELDS.get(kind, _YIELDS["custom"])


def known_for(kind: str, suffix: str) -> bool:
    """Whether this converter is a likely fit -- an ordering hint, not a gate."""
    return suffix.lower() in _KNOWN_FOR.get(kind, _KNOWN_FOR["custom"])


def known_suffixes(kind: str) -> frozenset[str]:
    """The formats this converter is known for; it may well open others."""
    return _KNOWN_FOR.get(kind, _KNOWN_FOR["custom"])


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
    instruction and outranks everything -- including dokey's opinion of which
    formats that tool handles, which is a hint and not a veto. What a
    converter will not open, it says itself when it is run, and that answer
    is worth more than a prediction. The one refusal kept here is about
    *evidence*, not formats: a scanned page has nothing but its image, so a
    converter that cannot yield the block stream cannot serve it at all, and
    substituting another tool would be doing something other than what was
    asked.
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
        yields = adapter_yields(converter.kind)
        if require_blocks and "blocks" not in yields:
            raise SystemExit(
                f"{prefer} yields markdown only, and this ingest needs the "
                "block stream -- a scanned page has nothing else to read. "
                "Use docling here."
            )
        return Choice(
            converter=converter,
            source="flag",
            yields=yields,
            degraded=suffix in PAGED_SUFFIXES and "blocks" not in yields,
        )
    ranked = candidates_for(input_path, require_blocks=require_blocks, candidates=pool)
    return ranked[0] if ranked else None


def candidates_for(
    input_path: Path,
    *,
    require_blocks: bool = False,
    candidates: list[tuple[str, convertlib.Converter]] | None = None,
) -> list[Choice]:
    """Every converter worth trying for this input, likeliest first.

    Nothing is dropped for the format's sake: the ordering puts the tool
    dokey knows the format for at the front, and leaves the others behind it
    to be tried if that one will not read the file. The only exclusion is
    about evidence -- a converter with no block stream cannot serve a scan,
    which is a fact about what the ingest needs rather than a prediction
    about what the tool opens.
    """
    suffix = input_path.suffix.lower()
    pool = _pool(candidates)
    ordered = sorted(
        pool,
        key=lambda item: (item[0] != "config", not known_for(item[1].kind, suffix)),
    )
    ranked: list[Choice] = []
    for source, converter in ordered:
        yields = adapter_yields(converter.kind)
        if require_blocks and "blocks" not in yields:
            continue
        ranked.append(
            Choice(
                converter=converter,
                source=source,
                yields=yields,
                degraded=suffix in PAGED_SUFFIXES and "blocks" not in yields,
            )
        )
    return ranked


def attempt(
    input_path: Path,
    convert,
    *,
    require_blocks: bool = False,
    prefer: str | None = None,
    candidates: list[tuple[str, convertlib.Converter]] | None = None,
    announce=print,
) -> tuple[Choice, tuple[Path, ...]]:
    """Run converters until one reads the document, then say which did.

    This is the whole of dokey's answer to "does this tool support that
    format": it tries. The likely fit goes first, and when it will not read
    the file, the tool's own words are shown and the next one is tried. Only
    when all of them have refused does the ingest stop -- and then the report
    is what each said, not a claim from a table about what they accept.

    An explicit instruction is not second-guessed: naming a converter means
    that one, and its failure is the answer.
    """
    pool = _pool(candidates)
    if prefer:
        choice = choose(
            input_path, require_blocks=require_blocks, prefer=prefer, candidates=pool
        )
        return choice, convert(choice)

    tried: list[tuple[Choice, str]] = []
    for choice in candidates_for(
        input_path, require_blocks=require_blocks, candidates=pool
    ):
        try:
            return choice, convert(choice)
        except convertlib.ConversionFailed as failure:
            tried.append((choice, failure.reason()))
            announce(f"{choice.converter.kind} could not read it: {failure.reason()}")
    if not tried:
        raise SystemExit(
            flow_install_hint()
            if is_flow_document(input_path)
            else convertlib.install_hint()
        )
    lines = [f"No converter here could read {input_path.name}:"]
    lines += [f"  {item.converter.kind}: {reason}" for item, reason in tried]
    lines.append(
        "Install another converter, or convert the file yourself and add the "
        "result."
    )
    raise SystemExit("\n".join(lines))


def flow_install_hint() -> str:
    return (
        "No converter found for this format. dokey ships none; bring your own.\n"
        "For flow documents (docx, pptx, html, epub) the light option is enough:\n"
        "  pip install markitdown[docx,pptx]   # markdown only, no torch\n"
        "or the reference converter, which also keeps page evidence for PDFs:\n"
        "  pip install docling"
    )
