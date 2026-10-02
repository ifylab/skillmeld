# SPDX-License-Identifier: Apache-2.0
"""The Codex sidecar: ``agents/openai.yaml`` derived from a skill's name and description.

Codex reads an optional ``agents/openai.yaml`` beside SKILL.md for UI metadata and invocation
policy (schema: OpenAI's skill-creator ``references/openai_yaml.md``, read 2026-09-30; every key
is optional and other agents ignore the directory). skillmeld regenerates it from the composed
skill's own name and description rather than carrying a source's, because a source sidecar's
``default_prompt`` names the source's ``$name``. Nothing here reads a body: the sidecar is
derived data, not a copy, so it sits outside the byte-trace guarantee by construction.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from skillmeld.models import SkillDoc

SIDECAR_PATH = "agents/openai.yaml"
SHORT_MIN = 25
SHORT_MAX = 64

_SENTENCE_END = re.compile(r"(?<=[.!?])\s")


def short_description(description: str) -> str | None:
    """The first sentence cut at a word boundary to at most 64 chars; None under 25.

    The schema wants 25-64 characters for the UI blurb. Padding a short sentence would mean
    inventing words, so a description that cannot supply 25 characters gets no blurb at all.
    """
    sentence = _first_sentence(description).rstrip(".!?")
    text = sentence
    if len(text) > SHORT_MAX:
        cut = text[:SHORT_MAX]
        if " " in cut:
            cut = cut[: cut.rfind(" ")]
        text = cut.rstrip(" ,;:")
    return text if len(text) >= SHORT_MIN else None


def display_name(name: str) -> str:
    """``ifc-qto`` becomes ``Ifc qto``: hyphens to spaces, first letter up, nothing invented."""
    words = name.replace("-", " ").strip()
    return words[:1].upper() + words[1:]


def default_prompt(name: str, description: str) -> str:
    """A prompt that mentions ``$<name>``, which the schema requires, built from the description.

    A description that already opens with "Use when ..." or "Use this skill to ..." keeps its own
    verb, so the prompt does not read "Use $x to use when ...".
    """
    clause = _first_sentence(description).rstrip(".!?")
    clause = clause[:1].lower() + clause[1:] if clause else "do its job"
    lowered = clause.lower()
    for lead in ("use this skill to ", "use this skill when ", "use when ", "use to ", "use "):
        if lowered.startswith(lead):
            rest = clause[len(lead) :]
            joiner = "when" if "when" in lead else "to"
            return f"Use ${name} {joiner} {rest}"
    return f"Use ${name} to {clause}"


def render_openai_yaml(doc: SkillDoc) -> str:
    """Render the sidecar text in a fixed key order. Scalars are JSON-quoted (valid YAML)."""
    name = str(doc.frontmatter.get("name", doc.source.name))
    description = str(doc.frontmatter.get("description", ""))
    disabled = doc.frontmatter.get("disable-model-invocation") is True
    lines = ["interface:", f"  display_name: {_scalar(display_name(name))}"]
    short = short_description(description)
    if short is not None:
        lines.append(f"  short_description: {_scalar(short)}")
    lines.append(f"  default_prompt: {_scalar(default_prompt(name, description))}")
    lines += ["policy:", f"  allow_implicit_invocation: {'false' if disabled else 'true'}"]
    return "\n".join(lines) + "\n"


def write_codex_sidecar(doc: SkillDoc, skill_dir: Path) -> str:
    """Write ``<skill_dir>/agents/openai.yaml``; return the path written."""
    target = skill_dir / SIDECAR_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_openai_yaml(doc), encoding="utf-8")
    return str(target)


def _first_sentence(text: str) -> str:
    stripped = " ".join(text.split())
    return _SENTENCE_END.split(stripped, maxsplit=1)[0].strip() if stripped else ""


def _scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)
