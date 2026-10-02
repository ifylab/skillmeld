# SPDX-License-Identifier: Apache-2.0
"""Install the ``/skillmeld`` driver skill from the installed package, with no clone and no Node.

The skill (``SKILL.md`` plus ``scripts/run.sh``) ships inside the wheel under
``skillmeld/driver``; a development checkout finds it at the repository's ``skills/skillmeld``.
It is written to an agent skills directory, by default the shared ``.agents/skills/`` that
Codex, Cursor, Gemini CLI, Copilot and the other readers scan (``~/.agents/skills/`` at user
scope). Claude Code users take the plugin route instead. Once installed, the skill's ``run.sh``
finds the engine on PATH, which is where ``uv tool install skillmeld`` put it.
"""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

from skillmeld.emit.package import InstallConflict, _occupied, _remove_entry
from skillmeld.emit.targets import UNIVERSAL_PROJECT, UNIVERSAL_USER

DRIVER_NAME = "skillmeld"


def driver_source() -> Path:
    """Where the packaged driver skill lives: the wheel's copy, else the checkout's."""
    packaged = Path(str(resources.files("skillmeld") / "driver"))
    if (packaged / "SKILL.md").is_file():
        return packaged
    checkout = Path(__file__).resolve().parents[2] / "skills" / DRIVER_NAME
    if (checkout / "SKILL.md").is_file():
        return checkout
    raise FileNotFoundError("the skillmeld driver skill is not bundled with this installation")


def default_root(scope: str, *, project_root: Path, home: Path) -> Path:
    """The shared skills folder for ``scope``: ``.agents/skills`` or ``~/.agents/skills``."""
    if scope == "user":
        return home / UNIVERSAL_USER[2:]
    return project_root / UNIVERSAL_PROJECT


def install_driver(root: Path, *, force: bool = False) -> list[str]:
    """Copy the driver skill to ``<root>/skillmeld/``; refuse an existing entry unless ``force``."""
    source = driver_source()
    target = root / DRIVER_NAME
    if _occupied(target):
        if not force:
            raise InstallConflict([str(target)])
        _remove_entry(target)
    target.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        rel = path.relative_to(source)
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        if dest.suffix == ".sh":
            dest.chmod(0o755)
        written.append(str(dest))
    return written
