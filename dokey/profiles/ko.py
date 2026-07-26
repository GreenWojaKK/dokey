"""Korean profile: the address ladder, enumeration order, and prose test.

Korean technical standards address a passage by a ladder of numbering series
rather than by nesting depth alone::

    3.            절 (clause)
    3.1           소절
    (1)           항
    (가)          목
    ①             세목
    ㉮            세세목

The rungs are *ranges*, not partitions -- a document may skip ``(1)`` and go
straight from ``3.1`` to ``(가)`` -- so a position resolves to the deepest rung
that encloses it, and a missing rung is counted, not invented. Off-ladder
series (``1)`` where the document elsewhere writes ``(1)``, ``가.`` where it
writes ``(가)``) are marked irregular and kept: a document's own inconsistency
must not cost it sections.

The prose test exists because layout-driven converters promote *fragments* to
headings. When a page break splits a sentence, its tail can arrive as its own
block and be labelled a section header -- ``음을 유념한다.`` was one such
heading in the measured corpus. Korean marks a sentence's end morphologically,
at the verb, so the tail is recognizable where a language-neutral rule (does it
end in a period?) is not enough: the fragment often carries no period at all.
"""
from __future__ import annotations

import re

from . import NEUTRAL, Numbering

# The canonical ladder, deepest rung last. Depths are fixed rather than derived
# from nesting so that a document that skips a rung still addresses the same
# way as one that does not.
_LADDER: tuple[tuple[str, re.Pattern[str], int, bool], ...] = (
    # 제1장 / 제 2 절 / 제3편 -- a chapter heads the document, so it is rung 1.
    # 제338조 is the same shape used to cite a statute, and these documents head
    # passages with such citations; reading it as numbering keeps the citation
    # whole and stops it being mistaken for an unnumbered title. Its rung is
    # its rung in the statute (편>장>절>관>조>항>호), so a cited article sits
    # under the clause that cites it rather than beside it.
    ("chapter", re.compile(r"^제\s*\d+\s*([장절편관항조호])"), 1, False),
    # 부록, 별표 3, 별지 제1호, 참고 -- back matter, addressed alongside clauses.
    # The corpus writes these bracketed as often as bare (``<별표 1>``,
    # ``[부록 2]``), and a bracketed annex is the same rung as a bare one: it
    # divides the document, unlike a table or figure caption, which labels an
    # object inside a division and is left off the ladder.
    (
        "appendix",
        re.compile(
            r"^[<〈《【\[（(]?\s*(부\s*[록표]|별\s*[표지첨]|붙\s*임|참\s*고)"
            r"\s*\d*\s*[>〉》】\］\])]?(?=\s|\d|$)"
        ),
        1,
        False,
    ),
    ("paren_hangul", re.compile(r"^(\(\s*[가-힣]\s*\))"), 4, False),
    ("circled_num", re.compile(r"^([①-⑳])"), 5, False),
    ("circled_hangul", re.compile(r"^([㈀-㈞㉠-㉾])"), 6, False),
    # 가. 나. -- the same rung as (가), written off-ladder.
    ("hangul_dot", re.compile(r"^([가-힣])\s*\.(?=\s)"), 4, True),
)

# 가나다 order for enumeration, four vowel rows deep -- the range measured in
# the corpus. Used to tell an enumerator (가, 나, 다 ...) from a word that
# happens to start with the same syllable.
_LEADS = "가나다라마바사아자차카타파하"
_ROWS = ("", "거너더러머버서어저처커터퍼허", "고노도로모보소오조초코토포호",
         "구누두루무부수우주추쿠투푸후")
HANGUL_ENUMERATORS: tuple[str, ...] = tuple(
    syllable for row in (_LEADS, *_ROWS[1:]) for syllable in row
)


def hangul_ordinal(syllable: str) -> int | None:
    """1-based position of a syllable in the 가나다 enumeration, or None."""
    try:
        return HANGUL_ENUMERATORS.index(syllable) + 1
    except ValueError:
        return None


# A Korean sentence ends at its verb. These are the endings a technical
# standard actually closes on; a title ends on a noun and so matches none.
_VERB_FINAL = re.compile(r"(?:다|까|랴|뇨)\s*[.?!]?$")
_NOMINAL_FINAL = re.compile(r"(?:요|함|임|음|됨|죠|네|오)\s*[.?!]$")
# A clause connective can never end a title: it promises a following clause.
_CONNECTIVE = re.compile(
    r"(?:하고|하며|하여|되어|되며|이며|이고|지만|으나|어서|아서|또는|그리고|및)$"
)
_OPENS_MID_CLAUSE = re.compile(r"^[\s:,.)\]}·]")
# One-syllable words that end a phrase in their own right, so a title ending on
# one was not cut mid-word: "관리 및" + "감독" is "관리 및 감독", not "및감독".
CONJUNCTION_TAILS = frozenset({"및", "또", "등", "혹", "내", "겸", "대", "외"})
# The statute's own hierarchy: 편 > 장 > 절 > 관 > 조 > 항 > 호. A document
# structured in 장 puts them at the top; an article it merely cites belongs
# under the clause doing the citing.
_STATUTE_RUNGS = {"편": 1, "장": 1, "절": 2, "관": 2, "조": 3, "항": 4, "호": 4}

_WS = re.compile(r"\s+")


class KoreanProfile:
    name = "ko"

    def numbering(self, title: str) -> Numbering | None:
        text = title.strip()
        for kind, pattern, depth, irregular in _LADDER:
            match = pattern.match(text)
            if not match:
                continue
            label = match.group(1)
            ordinal: tuple[int, ...] | None = None
            if kind == "hangul_dot":
                # 가. is an enumerator; 것. or 다. is a sentence tail wearing
                # the same shape, so require membership in the 가나다 series.
                position = hangul_ordinal(label)
                if position is None:
                    return None
                ordinal = (position,)
            elif kind == "paren_hangul":
                position = hangul_ordinal(label.strip("()（） "))
                if position is None:
                    return None
                ordinal = (position,)
            elif kind == "circled_num":
                ordinal = (ord(label) - 0x2460 + 1,)
            elif kind == "chapter":
                depth = _STATUTE_RUNGS.get(label, 1)
                digits = re.sub(r"\D", "", text[: match.end()])
                ordinal = (int(digits),) if digits else None
                label = text[: match.end()]
            elif kind == "appendix":
                # Keep the label as written, brackets and ordinal included, so
                # a citation reads <별표 1> rather than a bare 별표.
                digits = re.sub(r"\D", "", text[: match.end()])
                ordinal = (int(digits),) if digits else None
                label = text[: match.end()].strip()
            return Numbering(kind, depth, label, irregular=irregular, ordinal=ordinal)
        return NEUTRAL.numbering(text)

    def is_sentence_tail(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False
        if _OPENS_MID_CLAUSE.match(text):
            return True
        core = stripped.rstrip("”\"'’)")
        return bool(
            _VERB_FINAL.search(core)
            or _NOMINAL_FINAL.search(core)
            or _CONNECTIVE.search(core)
        )

    def key(self, text: str) -> str:
        # Justification stretches inter-syllable spacing ("목  적", "손 가 락"),
        # so whitespace cannot be part of a Korean line's identity.
        return _WS.sub("", text).strip().casefold()

    def join_title(self, head: str, tail: str) -> str:
        """Rejoin a title a page or column break split in two.

        A break inside a word leaves one syllable behind (``8.1.2 현`` /
        ``장 조립작업``), and closing that up restores the word. A break after a
        conjunction leaves a whole word (``… 관리 및`` / ``감독``), and there the
        space belongs. The conjunctions are a closed class, so this stays a
        grammatical rule rather than a vocabulary list.
        """
        tokens = head.split()
        tail = tail.strip()
        if tokens and len(tokens[-1]) == 1 and tokens[-1] not in CONJUNCTION_TAILS:
            return f"{head}{tail}"
        return f"{head} {tail}".strip()


KOREAN = KoreanProfile()
