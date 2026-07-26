"""Read a document's numbering conventions out of the document itself.

Which enumerator series encloses which is a *convention*, not a fact about the
language. Korean technical standards mostly run ``1.`` → ``4.1`` → ``(1)`` →
``(가)`` → ``①``, and dokey shipped that order as a table -- which is exactly
the kind of assumption that survives until it meets the next publisher. Measured
across 866 documents of the corpus it was fitted to, **1,241 of 9,159 observed
series pairs contradict it**: 138 documents put ``①`` *above* ``(가)``, 56 put
``(가)`` above ``(1)``, and roughly 200 nest an appendix inside the clauses
rather than beside them. The most common single order covers barely a third of
the documents that use more than two series.

So the order is induced per document, from evidence the document gives:

*Containment.* Two consecutive items of one series -- ``(1)`` then ``(2)`` --
bracket exactly one item of that series. Whatever other series appears between
them is nested inside it. Counting that over a document ranks the series
against each other without knowing what any of them mean.

*The prior only fills gaps.* Where a pair never co-occurs, or occurs too few
times to believe, the profile's conventional order decides. It is a
tie-breaker, not a rule.

Two shapes need care, and both come from real documents:

*A series whose ordinal never advances.* An auto-numbered list whose field
failed to resolve renders every item as ``0.`` -- five list items, five zeros
(measured in a corporate regulation). The items are real; the ordinals
are not. Such a series is marked ``unordered``: it can be nested inside others
but can never bracket anything, and its items are addressed by position rather
than by a number the document does not actually carry. A lone ``0.`` is left
alone, because a standard that opens with clause 0 (an introduction) means it.

*A series that carries its own depth.* ``4.1`` is two rungs and ``4.1.2`` is
three, by arithmetic rather than convention. Induction ranks the decimal series
as a whole; the arity adds to that rank.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import permutations

# Below this many observations a pair of series has told us nothing; the
# profile's conventional order decides instead.
MIN_PAIR_EVIDENCE = 3
# An item is a passage, not a region: two consecutive clause numbers with an
# entire annex between them are not evidence that the annex is inside the
# clause -- they are evidence that something else (a bibliography, a form)
# reused the numbering. Containment is only counted across a span an item can
# plausibly cover.
MAX_ITEM_SPAN_LINES = 120
# Series that carry more than one rung inside a single token.
MULTI_RUNG_KINDS = frozenset({"decimal", "dash"})
UNORDERED = "unordered"

_TABLE_ROW = re.compile(r"^ {0,3}\|")
_LEADING_HASHES = re.compile(r"^\s{0,3}#{1,6}\s+")


@dataclass(frozen=True)
class Token:
    kind: str
    ordinal: tuple[int, ...] | None
    line: int


@dataclass
class Ladder:
    """The rung each series occupies in one document."""

    rank: dict[str, int] = field(default_factory=dict)
    order: tuple[str, ...] = ()
    source: dict[str, str] = field(default_factory=dict)  # "observed" | "prior"
    evidence: Counter = field(default_factory=Counter)
    unordered_kinds: frozenset = frozenset()

    def depth(self, numbering) -> int:
        """The rung this token sits on, its own arity included."""
        kind = self.kind_of(numbering)
        base = self.rank.get(kind, numbering.depth)
        if kind in MULTI_RUNG_KINDS and numbering.ordinal:
            base += max(0, len(numbering.ordinal) - 2)
        return max(1, base)

    def kind_of(self, numbering) -> str:
        """The series a token belongs to, degenerate ordinals separated out."""
        if numbering.kind in self.unordered_kinds and _is_degenerate(numbering):
            return UNORDERED
        return numbering.kind

    def describe(self) -> str:
        return " > ".join(
            f"{kind}({self.source.get(kind, 'prior')[0]})" for kind in self.order
        )

    def as_dict(self) -> dict:
        return {
            "order": list(self.order),
            "rank": dict(self.rank),
            "source": dict(self.source),
            "unordered": sorted(self.unordered_kinds),
            "evidence": [
                {"outer": outer, "inner": inner, "count": count}
                for (outer, inner), count in self.evidence.most_common(12)
            ],
        }


def _is_degenerate(numbering) -> bool:
    return bool(numbering.ordinal) and all(part == 0 for part in numbering.ordinal)


def read_tokens(lines, profile) -> list[Token]:
    """Every enumerator in reading order, tables excluded (a cell is not a rung)."""
    tokens: list[Token] = []
    for index, line in enumerate(lines):
        if _TABLE_ROW.match(line):
            continue
        numbering = profile.numbering(_LEADING_HASHES.sub("", line))
        if numbering is None:
            continue
        tokens.append(Token(numbering.kind, numbering.ordinal, index))
    return tokens


def _degenerate_kinds(tokens: list[Token]) -> frozenset:
    """Series whose ordinals never advance -- auto-numbering that did not resolve.

    A single ``0.`` is a clause 0 and is left as it is; repeated zeros are a
    list whose numbers were never rendered.
    """
    zeros: Counter = Counter()
    for token in tokens:
        if token.ordinal and all(part == 0 for part in token.ordinal):
            zeros[token.kind] += 1
    return frozenset(kind for kind, count in zeros.items() if count > 1)


def _containment(tokens: list[Token], unordered: frozenset) -> Counter:
    """counts[(outer, inner)]: how often `inner` appears inside an `outer` item.

    An item is bounded by two consecutive tokens of its own series whose
    ordinals ascend by one -- the document's own statement that one item ended
    and the next began.
    """
    counts: Counter = Counter()
    kinds = [
        UNORDERED
        if token.kind in unordered
        and token.ordinal
        and all(part == 0 for part in token.ordinal)
        else token.kind
        for token in tokens
    ]
    positions: dict[str, list[int]] = defaultdict(list)
    for position, kind in enumerate(kinds):
        positions[kind].append(position)
    for outer, occurrences in positions.items():
        if outer == UNORDERED:
            continue  # no ordinals, so it can never bracket an item
        for first, second in zip(occurrences, occurrences[1:]):
            before, after = tokens[first].ordinal, tokens[second].ordinal
            if not before or not after or len(before) != len(after):
                continue
            if after[-1] != before[-1] + 1 or after[:-1] != before[:-1]:
                continue
            if tokens[second].line - tokens[first].line > MAX_ITEM_SPAN_LINES:
                continue
            for inner in kinds[first + 1 : second]:
                if inner != outer:
                    counts[(outer, inner)] += 1
    return counts


def _topological(kinds, edges, counts, key):
    """Order the series so evidence is respected and the prior decides the rest.

    Kahn's algorithm with the prior as the queue's ordering: a series is placed
    as early as the prior wants, unless something the document did puts another
    series above it. A cycle (the document nests A in B *and* B in A, which
    happens when a series is reused at two depths) is broken at its weakest
    edge, because that edge is the one the document supports least.
    """
    # Sorted throughout: a set's iteration order varies between processes, and
    # an ingest that ranks a document differently on Tuesday is not an ingest.
    remaining = {kind: set() for kind in sorted(kinds)}
    for outer, inners in edges.items():
        for inner in inners:
            if inner in remaining:
                remaining[inner].add(outer)
    order: list[str] = []
    while remaining:
        ready = sorted(kind for kind, above in remaining.items() if not above)
        if not ready:
            # Break the weakest surviving constraint rather than give up, and
            # name the pair in the tie-break so the choice is reproducible.
            weakest = min(
                (counts[(outer, inner)], outer, inner)
                for inner in sorted(remaining)
                for outer in sorted(remaining[inner])
            )
            remaining[weakest[2]].discard(weakest[1])
            continue
        chosen = min(ready, key=key)
        order.append(chosen)
        del remaining[chosen]
        for above in remaining.values():
            above.discard(chosen)
    return order


def induce(tokens: list[Token], prior: dict[str, int]) -> Ladder:
    """Rank the series this document uses, by what it does with them."""
    unordered = _degenerate_kinds(tokens)
    counts = _containment(tokens, unordered)
    kinds = {
        UNORDERED
        if token.kind in unordered
        and token.ordinal
        and all(part == 0 for part in token.ordinal)
        else token.kind
        for token in tokens
    }
    if not kinds:
        return Ladder(unordered_kinds=unordered)

    def prior_of(kind: str) -> int:
        if kind == UNORDERED:
            return 9  # an unnumbered list sits below anything numbered
        return prior.get(kind, 5)

    # Evidence enters as *constraints between pairs*, not as a score. A series
    # the document nests inside another must rank below it; a series the
    # document says nothing about keeps the place the prior gives it. Scoring
    # by total wins would push an unmentioned series below one that merely won
    # an unrelated pair -- C-49-2012 uses ``4.1`` exactly twice, and that is not
    # a statement that decimals sit under ``(1)``.
    edges: dict[str, set[str]] = {kind: set() for kind in kinds}
    decided: set[str] = set()
    for outer, inner in permutations(sorted(kinds), 2):
        above, below = counts[(outer, inner)], counts[(inner, outer)]
        if above >= MIN_PAIR_EVIDENCE and above > below:
            edges[outer].add(inner)
            decided.update((outer, inner))

    first_seen: dict[str, int] = {}
    for position, token in enumerate(tokens):
        first_seen.setdefault(
            UNORDERED
            if token.kind in unordered
            and token.ordinal
            and all(part == 0 for part in token.ordinal)
            else token.kind,
            position,
        )
    order = _topological(kinds, edges, counts, key=lambda kind: (
        prior_of(kind), first_seen.get(kind, 0), kind
    ))

    # Rank in groups, not one series per rung: two series only occupy different
    # rungs when something says so -- the document nesting one inside the other,
    # or the prior placing them at different depths. Without that, a clause and
    # an annex are peers, and forcing them apart would fold one into the other.
    # Start from the conventional ladder and let evidence move only what it
    # contradicts. Building the rungs from the evidence alone chains them: a
    # document using eight series would push its clause numbering to the eighth
    # rung merely for having eight series, and a series the document nests in
    # two places at once (a checklist's ``1.`` inside ``(1)``, its clauses at
    # the top) would drag the whole ladder with it. Overriding pair by pair
    # keeps every rung the convention already had right.
    rank = {kind: prior_of(kind) for kind in kinds}
    # Only edges that agree with the topological order are applied. A document
    # that nests a series in two places at once puts a cycle in the evidence,
    # and the sort has already chosen which edge of that cycle to keep; pushing
    # along the cycle instead would drive the rungs down without end (measured:
    # rung 34 in D-C-10-2026 before this guard).
    position = {kind: index for index, kind in enumerate(order)}
    for _ in range(len(kinds)):
        moved = False
        for outer, inners in sorted(edges.items()):
            for inner in sorted(inners):
                if inner not in rank or position[outer] >= position[inner]:
                    continue
                if rank[inner] <= rank[outer]:
                    rank[inner] = rank[outer] + 1
                    moved = True
        if not moved:
            break
    for kind in MULTI_RUNG_KINDS & kinds:
        # A multi-rung series states its own first rung: ``4.1`` is two deep.
        rank[kind] = max(rank[kind], 2)
    order = sorted(order, key=lambda kind: (rank[kind], order.index(kind)))
    return Ladder(
        rank=rank,
        order=tuple(order),
        source={kind: "observed" if kind in decided else "prior" for kind in order},
        evidence=counts,
        unordered_kinds=unordered,
    )


def induce_from_lines(lines, profile) -> Ladder:
    """Convenience: read a document's tokens and rank its series."""
    tokens = read_tokens(lines, profile)
    prior: dict[str, int] = {}
    for line in lines:
        numbering = profile.numbering(_LEADING_HASHES.sub("", line))
        if numbering is None:
            continue
        depth = numbering.depth
        if numbering.kind in MULTI_RUNG_KINDS and numbering.ordinal:
            # The series' own rung, not this token's: a document whose first
            # decimal happens to be ``5.2.2`` must not push every decimal one
            # rung deeper, because the arity is added back at lookup time.
            depth -= max(0, len(numbering.ordinal) - 2)
        prior.setdefault(numbering.kind, max(1, depth))
    return induce(tokens, prior)
