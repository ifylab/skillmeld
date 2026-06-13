# SPDX-License-Identifier: Apache-2.0
"""Step 1 — parse a skill body into byte-exact atoms with stable structural ids.

Boundaries come from a single version-pinned CommonMark block parser; we never split prose
into sentences (non-deterministic, library-version-fragile). Each atom is a verbatim byte
slice of the source body, addressed by a structural path (``s2/l1/i0``) and identified by a
hash over its raw bytes. The id's hash is tamper-evident; the path disambiguates two atoms
with identical text. Determinism here is the foundation the whole merge rests on.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

from markdown_it import MarkdownIt
from markdown_it.token import Token

from skillmeld.models import Atom, AtomKind, SkillDoc

# Pinned parser. Never swap presets/versions without re-baselining golden tests.
_MD = MarkdownIt("commonmark")

_ID_HASH_LEN = 16

# Block-token -> path-segment letter. Containers nest; leaves emit an atom.
_CONTAINER_LETTER = {
    "bullet_list": "l",
    "ordered_list": "l",
    "list_item": "i",
    "blockquote": "q",
}
_LEAF_LETTER = {
    "heading": "h",
    "paragraph": "p",
    "fence": "c",
    "code_block": "c",
    "html_block": "x",
    "hr": "r",
}
_LEAF_KIND = {
    "heading": AtomKind.heading,
    "fence": AtomKind.example,
    "code_block": AtomKind.example,
}


def atom_id(skill: str, path: str, text: str) -> str:
    """Stable id: ``{skill}:{path}:{sha256(raw text)[:16]}``. Hash is over raw bytes."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:_ID_HASH_LEN]
    return f"{skill}:{path}:{digest}"


def norm_key(text: str) -> str:
    """Pinned dedup key: NFC, strip, collapse inner whitespace, casefold, drop trailing .:;.

    Used only to group exact-duplicate atoms (step 2). Never enters an id or the output.
    """
    normalized = unicodedata.normalize("NFC", text).strip()
    normalized = re.sub(r"\s+", " ", normalized).casefold()
    return normalized.rstrip(".:;").strip()


class _Frame:
    __slots__ = ("counter", "prefix")

    def __init__(self, prefix: str, counter: int) -> None:
        self.prefix = prefix
        self.counter = counter


def parse_skill(doc: SkillDoc) -> list[Atom]:
    """Parse a skill body into atoms. Each atom is a verbatim byte slice of ``doc.body``."""
    skill = doc.source.name
    body_bytes = doc.body.encode("utf-8")
    line_starts = _line_starts(body_bytes)
    atoms: list[Atom] = []

    stack: list[_Frame] = [_Frame(prefix="", counter=0)]
    order = 0

    for token in _MD.parse(doc.body):
        kind = _strip_suffix(token.type)
        if token.nesting == 1 and kind in _CONTAINER_LETTER:
            segment = _segment(stack[-1], _CONTAINER_LETTER[kind])
            stack.append(_Frame(prefix=_join(stack[-1].prefix, segment), counter=0))
        elif token.nesting == -1 and kind in _CONTAINER_LETTER:
            if len(stack) > 1:
                stack.pop()
        elif _is_leaf(token, kind):
            atom = _leaf_atom(skill, stack[-1], kind, token, body_bytes, line_starts, order)
            if atom is not None:
                atoms.append(atom)
                order += 1
    return atoms


def _is_leaf(token: Token, kind: str) -> bool:
    if token.nesting == 1 and kind in ("heading", "paragraph"):
        return True
    return token.nesting == 0 and kind in _LEAF_LETTER


def _leaf_atom(
    skill: str,
    frame: _Frame,
    kind: str,
    token: Token,
    body_bytes: bytes,
    line_starts: list[int],
    order: int,
) -> Atom | None:
    if token.map is None:
        return None
    segment = _segment(frame, _LEAF_LETTER[kind])
    path = _join(frame.prefix, segment)
    start = line_starts[token.map[0]]
    end = line_starts[token.map[1]] if token.map[1] < len(line_starts) else len(body_bytes)
    text = body_bytes[start:end].decode("utf-8")
    return Atom(
        id=atom_id(skill, path, text),
        skill=skill,
        path=path,
        text=text,
        start=start,
        end=end,
        detected_kind=_detect_kind(kind, text),
        source_order=order,
        norm_key=norm_key(text),
    )


def _segment(frame: _Frame, letter: str) -> str:
    ordinal = frame.counter
    frame.counter += 1
    return f"{letter}{ordinal}"


def _join(prefix: str, segment: str) -> str:
    return f"{prefix}/{segment}" if prefix else segment


def _strip_suffix(token_type: str) -> str:
    for suffix in ("_open", "_close"):
        if token_type.endswith(suffix):
            return token_type[: -len(suffix)]
    return token_type


def _line_starts(body_bytes: bytes) -> list[int]:
    """Byte offset of each line start. ``markdown-it`` line numbers index into this."""
    starts = [0]
    for index, byte in enumerate(body_bytes):
        if byte == 0x0A:
            starts.append(index + 1)
    return starts


_IMPERATIVE = re.compile(
    r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?"
    r"(?:always|never|do not|don'?t|must|should|ensure|make sure|avoid|use|run|call|"
    r"prefer|keep|set|add|remove|check|validate|require|emit|return|write|read|"
    r"include|exclude|apply|follow)\b",
    re.IGNORECASE,
)
_TRIGGER = re.compile(r"^\s*(?:when|if|use this (?:skill|when)|trigger)\b", re.IGNORECASE)


def _detect_kind(kind: str, text: str) -> AtomKind:
    """Deterministic Python kind. Load-bearing: directive detection (Claude cannot downgrade it)."""
    if kind in _LEAF_KIND:
        return _LEAF_KIND[kind]
    stripped = text.strip()
    if _TRIGGER.match(stripped):
        return AtomKind.trigger
    if _IMPERATIVE.match(stripped):
        return AtomKind.directive
    return AtomKind.context
