# SPDX-License-Identifier: Apache-2.0
"""Parse-step tests: byte-faithful slices, stable structural ids, deterministic boundaries.

These guard the foundation the whole merge rests on. If atoms are not verbatim slices or
ids drift across re-parse, byte-traceability is unprovable.
"""

from __future__ import annotations

import hashlib

from skillmeld.merge.parse import atom_id, norm_key, parse_skill
from skillmeld.models import AtomKind, SkillDoc, SkillSource

SAMPLE = """# IFC quantity takeoff

Extract quantities from IFC models.

- Always validate the schema first.
- Use IfcOpenShell for parsing.
  - Prefer the latest release.

When the user asks for a takeoff, run the extractor.

```python
import ifcopenshell
model = ifcopenshell.open(path)
```

## Notes

See the docs for edge cases.
"""


def _doc(body: str, name: str = "ifc-qto") -> SkillDoc:
    return SkillDoc(source=SkillSource(name=name), body=body)


def test_every_atom_is_a_byte_faithful_slice() -> None:
    doc = _doc(SAMPLE)
    body_bytes = doc.body.encode("utf-8")
    atoms = parse_skill(doc)
    assert atoms
    for atom in atoms:
        assert body_bytes[atom.start : atom.end].decode("utf-8") == atom.text


def test_ids_are_stable_across_reparse() -> None:
    doc = _doc(SAMPLE)
    first = [a.id for a in parse_skill(doc)]
    second = [a.id for a in parse_skill(doc)]
    assert first == second
    assert len(first) == len(set(first))  # unique within the skill


def test_id_hashes_raw_text() -> None:
    atom = parse_skill(_doc("Always validate inputs.\n"))[0]
    expected = hashlib.sha256(atom.text.encode("utf-8")).hexdigest()[:16]
    assert atom.id == f"ifc-qto:{atom.path}:{expected}"


def test_structural_paths_encode_nesting() -> None:
    paths = [a.path for a in parse_skill(_doc(SAMPLE))]
    # top-level heading, paragraph, list, trigger paragraph, fence, heading, paragraph
    assert "h0" in paths  # first heading
    assert any(p.startswith("l") and "/i" in p for p in paths)  # list items nested
    assert any("/i" in p and "/l" in p for p in paths)  # nested sub-list item


def test_editing_one_atom_leaves_others_ids_unchanged() -> None:
    before = {a.path: a.id for a in parse_skill(_doc(SAMPLE))}
    edited = SAMPLE.replace("See the docs for edge cases.", "See the manual for edge cases.")
    after = {a.path: a.id for a in parse_skill(_doc(edited))}
    changed = [p for p in before if before[p] != after.get(p)]
    # Only the edited paragraph's id changes; every other atom is identical.
    assert len(changed) == 1


def test_fence_with_blank_line_is_one_atom() -> None:
    body = "```python\nx = 1\n\ny = 2\n```\n"
    atoms = parse_skill(_doc(body))
    fences = [a for a in atoms if a.detected_kind is AtomKind.example]
    assert len(fences) == 1
    assert fences[0].text == body


def test_no_sentence_splitting_within_a_paragraph() -> None:
    body = "Validate the input. Then call the API. Return the result.\n"
    atoms = parse_skill(_doc(body))
    assert len(atoms) == 1
    assert atoms[0].text == body


def test_detected_kind_directive_and_trigger() -> None:
    atoms = parse_skill(_doc(SAMPLE))
    heading = next(a for a in atoms if a.text.startswith("# IFC"))
    assert heading.detected_kind is AtomKind.heading
    directive = next(a for a in atoms if "Always validate the schema" in a.text)
    assert directive.detected_kind is AtomKind.directive
    trigger = next(a for a in atoms if a.text.strip().startswith("When the user asks"))
    assert trigger.detected_kind is AtomKind.trigger


def test_norm_key_collapses_trivial_differences() -> None:
    assert norm_key("Use TLS.") == norm_key("use   tls")
    assert norm_key("Use TLS. ") == norm_key("USE TLS")
    assert norm_key("Different text") != norm_key("Use TLS")


def test_atom_id_is_pure_function() -> None:
    assert atom_id("s", "p0", "hello") == atom_id("s", "p0", "hello")
    assert atom_id("s", "p0", "hello") != atom_id("s", "p1", "hello")
    assert atom_id("s", "p0", "hello") != atom_id("s", "p0", "world")


def test_identical_text_two_places_distinct_ids() -> None:
    body = "## A\n\nSame line here.\n\n## B\n\nSame line here.\n"
    atoms = parse_skill(_doc(body))
    same = [a for a in atoms if a.text.strip() == "Same line here."]
    assert len(same) == 2
    assert same[0].id != same[1].id  # different paths disambiguate
    assert same[0].norm_key == same[1].norm_key  # but dedup sees them as equal
