# SPDX-License-Identifier: Apache-2.0
"""Where each coding agent reads skills from: the path table behind ``emit --install-for``.

Pure data and resolution, no I/O. Every row comes from the agent's own documentation as read on
2026-09-30 (``doc_url``); re-verify a row before changing it. Most agents read the cross-client
``.agents/skills/`` convention at both scopes, so one copy there reaches all of them, and it
never makes an agent load the same set twice through two directories it scans (Cursor reads both
``.cursor/skills/`` and ``.agents/skills/``, for example). An agent that does not read the
convention at a scope gets its native directory instead. Claude Code is the only target that
reads its own frontmatter dialect; every other target is rendered spec-only.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

UNIVERSAL_PROJECT = ".agents/skills"
UNIVERSAL_USER = "~/.agents/skills"
# ``--install-for agents`` writes the bare convention folder with no agent named; ``all`` is
# every real agent in the table.
UNIVERSAL = "agents"
ALL = "all"
SCOPES = ("project", "user")


@dataclass(frozen=True)
class InstallTarget:
    agent: str
    project_dir: str
    user_dir: str
    universal_scopes: frozenset[str]
    spec_only: bool
    doc_url: str


@dataclass(frozen=True)
class ResolvedTarget:
    """One directory to write: the agents sharing it and the render dialect it needs."""

    path_rel: str
    agents: tuple[str, ...]
    spec_only: bool
    scope: str


_BOTH = frozenset(SCOPES)
_USER_ONLY = frozenset({"user"})
_NONE: frozenset[str] = frozenset()

INSTALL_TARGETS: dict[str, InstallTarget] = {
    "claude-code": InstallTarget(
        "claude-code",
        ".claude/skills",
        "~/.claude/skills",
        _NONE,
        False,
        "https://code.claude.com/docs/en/skills",
    ),
    "codex": InstallTarget(
        "codex",
        UNIVERSAL_PROJECT,
        UNIVERSAL_USER,
        _BOTH,
        True,
        "https://learn.chatgpt.com/docs/build-skills",
    ),
    "cursor": InstallTarget(
        "cursor",
        ".cursor/skills",
        "~/.cursor/skills",
        _BOTH,
        True,
        "https://cursor.com/docs/context/skills",
    ),
    "gemini-cli": InstallTarget(
        "gemini-cli",
        ".gemini/skills",
        "~/.gemini/skills",
        _BOTH,
        True,
        "https://geminicli.com/docs/cli/skills/",
    ),
    "github-copilot": InstallTarget(
        "github-copilot",
        ".github/skills",
        "~/.copilot/skills",
        _BOTH,
        True,
        "https://docs.github.com/en/copilot/concepts/agents/about-agent-skills",
    ),
    "windsurf": InstallTarget(
        "windsurf",
        ".devin/skills",
        "~/.codeium/windsurf/skills",
        _BOTH,
        True,
        "https://docs.devin.ai/desktop/cascade/skills",
    ),
    "opencode": InstallTarget(
        "opencode",
        ".opencode/skills",
        "~/.config/opencode/skills",
        _BOTH,
        True,
        "https://opencode.ai/docs/skills/",
    ),
    "goose": InstallTarget(
        "goose",
        UNIVERSAL_PROJECT,
        UNIVERSAL_USER,
        _BOTH,
        True,
        "https://goose-docs.ai/docs/guides/context-engineering/using-skills/",
    ),
    "amp": InstallTarget(
        "amp",
        UNIVERSAL_PROJECT,
        "~/.config/amp/skills",
        _BOTH,
        True,
        "https://ampcode.com/docs/customize/skills",
    ),
    "kiro": InstallTarget(
        "kiro",
        ".kiro/skills",
        "~/.kiro/skills",
        _NONE,
        True,
        "https://kiro.dev/docs/skills/",
    ),
    "junie": InstallTarget(
        "junie",
        ".junie/skills",
        "~/.junie/skills",
        _BOTH,
        True,
        "https://junie.jetbrains.com/docs/agent-skills.html",
    ),
    "factory": InstallTarget(
        "factory",
        ".factory/skills",
        "~/.factory/skills",
        _USER_ONLY,
        True,
        "https://docs.factory.com/cli/configuration/skills",
    ),
    "roo": InstallTarget(
        "roo",
        ".roo/skills",
        "~/.roo/skills",
        _BOTH,
        True,
        "https://roocodeinc.github.io/Roo-Code/features/skills",
    ),
    UNIVERSAL: InstallTarget(
        UNIVERSAL,
        UNIVERSAL_PROJECT,
        UNIVERSAL_USER,
        _BOTH,
        True,
        "https://agentskills.io/client-implementation/adding-skills-support",
    ),
}

AGENT_NAMES: tuple[str, ...] = tuple(name for name in INSTALL_TARGETS if name != UNIVERSAL)

# Root-level markers a repo carries when it is already set up for an agent, mapped to the install
# target that agent reads. A bare ``.agents/skills`` or ``AGENTS.md`` names no single agent, so it
# maps to the universal folder.
DETECTION_MARKERS: tuple[tuple[str, str], ...] = (
    (".claude", "claude-code"),
    (".codex", "codex"),
    (".cursor", "cursor"),
    (".gemini", "gemini-cli"),
    (".github/copilot-instructions.md", "github-copilot"),
    (".github/skills", "github-copilot"),
    (".devin", "windsurf"),
    (".windsurf", "windsurf"),
    (".opencode", "opencode"),
    (".kiro", "kiro"),
    (".junie", "junie"),
    (".factory", "factory"),
    (".roo", "roo"),
    (".agents/skills", UNIVERSAL),
    ("AGENTS.md", UNIVERSAL),
)


def parse_agents(value: str) -> list[str]:
    """Parse a comma-separated ``--install-for`` value; ``all`` expands to every real agent.

    Unknown names raise ``ValueError`` naming the closest known agent, so a typo never silently
    installs nowhere.
    """
    names: list[str] = []
    for raw in value.split(","):
        token = raw.strip().lower()
        if not token:
            continue
        expanded = list(AGENT_NAMES) if token == ALL else [token]
        for name in expanded:
            if name not in INSTALL_TARGETS:
                known = ", ".join([*AGENT_NAMES, UNIVERSAL, ALL])
                close = difflib.get_close_matches(name, [*AGENT_NAMES, UNIVERSAL], n=1)
                hint = f"; did you mean {close[0]}?" if close else ""
                raise ValueError(f"unknown agent {name!r}{hint} (known: {known})")
            if name not in names:
                names.append(name)
    if not names:
        raise ValueError("--install-for names no agent")
    return names


def resolve_targets(agents: list[str], *, scope: str, native: bool = False) -> list[ResolvedTarget]:
    """Map agents to the directories to write, one entry per distinct directory.

    An agent that reads the ``.agents/skills`` convention at ``scope`` resolves to that folder
    unless ``native`` asks for its own directory; others resolve to their native directory. Agents
    sharing a directory share one copy. A directory is spec-only unless every agent reading it
    reads the Claude Code dialect, which in practice means only ``.claude/skills``.
    """
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SCOPES)}; got {scope!r}")
    by_path: dict[str, list[str]] = {}
    spec_only: dict[str, bool] = {}
    for agent in agents:
        target = INSTALL_TARGETS.get(agent)
        if target is None:
            raise ValueError(f"unknown agent {agent!r}")
        universal = scope in target.universal_scopes and not native
        if universal:
            path = UNIVERSAL_PROJECT if scope == "project" else UNIVERSAL_USER
        else:
            path = target.project_dir if scope == "project" else target.user_dir
        names = by_path.setdefault(path, [])
        if agent not in names:
            names.append(agent)
        spec_only[path] = spec_only.get(path, True) and target.spec_only
    return [
        ResolvedTarget(path, tuple(names), spec_only[path], scope)
        for path, names in by_path.items()
    ]


def agents_for_markers(found: list[str]) -> list[str]:
    """The install targets implied by root markers, in table order, the universal folder last."""
    implied = {agent for marker, agent in DETECTION_MARKERS if marker in found}
    ordered = [name for name in AGENT_NAMES if name in implied]
    if UNIVERSAL in implied:
        ordered.append(UNIVERSAL)
    return ordered
