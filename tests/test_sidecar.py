# SPDX-License-Identifier: Apache-2.0
"""Codex sidecar: derived from name and description only, deterministic, valid YAML."""

from __future__ import annotations

from pathlib import Path

import yaml

from skillmeld.emit.sidecar import (
    SHORT_MAX,
    SHORT_MIN,
    default_prompt,
    display_name,
    render_openai_yaml,
    short_description,
    write_codex_sidecar,
)
from skillmeld.models import SkillDoc, SkillSource

BODY = "# IFC QTO\n\nTake off quantities from IFC.\n\n- Always validate the model first.\n"


def _doc(description: str, **extra: object) -> SkillDoc:
    front: dict[str, object] = {"name": "ifc-qto", "description": description, **extra}
    return SkillDoc(source=SkillSource(name="ifc-qto"), frontmatter=front, body=BODY)


def test_short_description_truncates_at_a_word_boundary_within_64() -> None:
    long = (
        "Extracts quantities, areas and volumes from IFC building models for cost estimating "
        "and checks them. Second sentence is ignored."
    )
    short = short_description(long)
    assert short is not None
    assert SHORT_MIN <= len(short) <= SHORT_MAX
    assert not short.endswith(" ") and long.startswith(short)
    assert short.rsplit(" ", 1)[-1] in long.split()


def test_short_description_is_omitted_under_25_chars() -> None:
    assert short_description("Takes off IFC.") is None
    assert "short_description" not in render_openai_yaml(_doc("Takes off IFC."))


def test_default_prompt_mentions_dollar_name() -> None:
    prompt = default_prompt("ifc-qto", "Extract quantities from IFC models. Then more.")
    assert prompt == "Use $ifc-qto to extract quantities from IFC models"
    assert display_name("ifc-qto") == "Ifc qto"
    when = default_prompt("ifc-qto", "Use when the task is a quantity takeoff from IFC.")
    assert when == "Use $ifc-qto when the task is a quantity takeoff from IFC"
    skill = default_prompt("ifc-qto", "Use this skill to take off quantities.")
    assert skill == "Use $ifc-qto to take off quantities"
    assert default_prompt("x", "") == "Use $x to do its job"


def test_allow_implicit_invocation_follows_disable_model_invocation() -> None:
    on = yaml.safe_load(render_openai_yaml(_doc("Extract quantities from IFC models.")))
    assert on["policy"]["allow_implicit_invocation"] is True
    off = yaml.safe_load(
        render_openai_yaml(
            _doc("Extract quantities from IFC models.", **{"disable-model-invocation": True})
        )
    )
    assert off["policy"]["allow_implicit_invocation"] is False


def test_sidecar_contains_no_body_line() -> None:
    text = render_openai_yaml(_doc("Extract quantities from IFC models for estimating."))
    for line in BODY.splitlines():
        if line.strip():
            assert line.strip() not in text


def test_render_openai_yaml_is_deterministic_and_parses() -> None:
    doc = _doc('Extract: quantities "fast" from IFC models, with care.')
    first = render_openai_yaml(doc)
    assert first == render_openai_yaml(doc)
    data = yaml.safe_load(first)
    assert list(data) == ["interface", "policy"]
    assert list(data["interface"]) == ["display_name", "short_description", "default_prompt"]
    assert data["interface"]["default_prompt"].startswith("Use $ifc-qto to extract: quantities")


def test_write_codex_sidecar_lands_under_agents(tmp_path: Path) -> None:
    written = write_codex_sidecar(_doc("Extract quantities from IFC models."), tmp_path / "ifc-qto")
    assert written.endswith("ifc-qto/agents/openai.yaml")
    assert Path(written).read_text(encoding="utf-8").startswith("interface:\n")
