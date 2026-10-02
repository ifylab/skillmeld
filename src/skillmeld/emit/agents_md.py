# SPDX-License-Identifier: Apache-2.0
"""An AGENTS.md block that points a repo's agents at the skills skillmeld installed.

No agent documents reading skills out of AGENTS.md; every one discovers them from its skills
directory and injects name plus description. The block is therefore a pointer for the reader of
the file, never the skill itself: it names each installed skill, its description, and the
directory each agent scans, and it is marker-delimited so a re-run replaces its own block and
leaves everything else in the file alone. Nothing here reads a body.
"""

from __future__ import annotations

import re
from pathlib import Path

START_TEMPLATE = "<!-- skillmeld:start {set} -->"
END = "<!-- skillmeld:end -->"


def render_block(
    set_name: str,
    skills: list[tuple[str, str]],
    directories: list[tuple[str, list[str]]],
) -> str:
    """The block text: heading, the directories per agent, one line per skill, a provenance line."""
    lines = [
        START_TEMPLATE.format(set=set_name),
        f"## Skills composed by skillmeld ({set_name})",
        "",
    ]
    if directories:
        listed = ", ".join(
            f"`{path}/`" + (f" ({', '.join(agents)})" if agents else "")
            for path, agents in directories
        )
        lines.append(f"Skill directories: {listed}")
    else:
        lines.append("Skill directories: see the emit output")
    lines.append("")
    lines.extend(f"- `{name}`: {description}" for name, description in skills)
    lines += [
        "",
        f"Provenance: `PROVENANCE-{set_name}.md` in each skill directory. Every instruction "
        "traces byte-for-byte to the sources it lists; skillmeld authored only the names and "
        "descriptions.",
        END,
    ]
    return "\n".join(lines) + "\n"


def upsert_block(path: Path, set_name: str, block: str) -> str:
    """Create the file, replace this set's existing block, or append. Byte-idempotent."""
    start = START_TEMPLATE.format(set=set_name)
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(END) + r"\n?", re.DOTALL)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(block, encoding="utf-8")
        return "created"
    text = path.read_text(encoding="utf-8")
    if pattern.search(text):
        updated = pattern.sub(lambda _match: block, text, count=1)
        action = "replaced"
    else:
        if not text or text.endswith("\n\n"):
            separator = ""
        elif text.endswith("\n"):
            separator = "\n"
        else:
            separator = "\n\n"
        updated = text + separator + block
        action = "appended"
    if updated != text:
        path.write_text(updated, encoding="utf-8")
    return action
