# SPDX-License-Identifier: Apache-2.0
"""Grounding: scan a repository into a use-case profile. Stays local; never leaves the machine.

Deterministic only. We gather facts (languages, dependencies, conventions); the host Claude
turns the evidence into the profile summary and the inferred task list.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Iterator
from pathlib import Path

from skillmeld.models import RepoEvidence, UseCaseProfile

IGNORE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "dist",
        "build",
        "target",
        "vendor",
        ".idea",
        ".vscode",
        ".next",
    }
)

LANGUAGE_BY_EXT: dict[str, str] = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".cpp": "C++",
    ".c": "C",
    ".swift": "Swift",
    ".scala": "Scala",
    ".sh": "Shell",
    ".sql": "SQL",
}

FRAMEWORK_SIGNS: dict[str, str] = {
    "flask": "Flask",
    "django": "Django",
    "fastapi": "FastAPI",
    "starlette": "Starlette",
    "pydantic": "Pydantic",
    "sqlalchemy": "SQLAlchemy",
    "pandas": "pandas",
    "numpy": "NumPy",
    "torch": "PyTorch",
    "tensorflow": "TensorFlow",
    "react": "React",
    "next": "Next.js",
    "vue": "Vue",
    "svelte": "Svelte",
    "express": "Express",
}

CONFIG_BY_FILE: dict[str, str] = {
    "ruff.toml": "ruff",
    ".ruff.toml": "ruff",
    ".eslintrc": "eslint",
    ".eslintrc.json": "eslint",
    "eslint.config.js": "eslint",
    ".prettierrc": "prettier",
    ".editorconfig": "editorconfig",
    "tsconfig.json": "tsconfig",
    "mypy.ini": "mypy",
    ".pre-commit-config.yaml": "pre-commit",
}

_README_NAMES = ("README.md", "README.rst", "README.txt", "README")
_README_LIMIT = 1200
_MAX_FILES = 20000
_TEST_EXTS = frozenset({".py", ".ts", ".js", ".rs", ".go"})


def ground(repo: Path) -> UseCaseProfile:
    """Scan a repo and derive its use-case profile (deterministic fields only)."""
    return profile_from(scan(repo))


def scan(repo: Path) -> RepoEvidence:
    """Walk a repo and collect deterministic evidence. Skips vendored and build directories."""
    root = repo.resolve()
    file_counts: dict[str, int] = {}
    manifests: list[str] = []
    dependencies: list[str] = []
    config_files: list[str] = []
    has_tests = False

    for index, path in enumerate(_walk(root)):
        if index >= _MAX_FILES:
            break
        rel = path.relative_to(root).as_posix()
        ext = path.suffix.lower()
        if ext:
            file_counts[ext] = file_counts.get(ext, 0) + 1
        config = CONFIG_BY_FILE.get(path.name)
        if config:
            config_files.append(config)
        if _is_manifest(path.name, ext):
            manifests.append(rel)
            dependencies.extend(_parse_manifest(path))
        if not has_tests and "test" in rel.lower() and ext in _TEST_EXTS:
            has_tests = True

    top_dirs: list[str] = []
    if root.is_dir():
        top_dirs = sorted(
            p.name for p in root.iterdir() if p.is_dir() and p.name not in IGNORE_DIRS
        )

    return RepoEvidence(
        root=str(root),
        file_counts=file_counts,
        manifests=sorted(manifests),
        dependencies=sorted(set(dependencies)),
        config_files=sorted(set(config_files)),
        top_dirs=top_dirs,
        readme_excerpt=_readme_excerpt(root),
        has_tests=has_tests,
    )


def profile_from(evidence: RepoEvidence) -> UseCaseProfile:
    """Derive deterministic profile fields. summary and tasks are left for the host Claude."""
    conventions = list(evidence.config_files)
    if evidence.has_tests:
        conventions.append("tests")
    return UseCaseProfile(
        summary="",
        languages=_languages(evidence.file_counts),
        frameworks=_frameworks(evidence.dependencies),
        conventions=conventions,
        tasks=[],
    )


def _walk(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for filename in filenames:
            yield Path(dirpath) / filename


def _is_manifest(name: str, ext: str) -> bool:
    if name in {"pyproject.toml", "package.json", "Cargo.toml", "go.mod"}:
        return True
    return name.startswith("requirements") and ext == ".txt"


def _languages(file_counts: dict[str, int]) -> list[str]:
    totals: dict[str, int] = {}
    for ext, count in file_counts.items():
        language = LANGUAGE_BY_EXT.get(ext)
        if language:
            totals[language] = totals.get(language, 0) + count
    return [name for name, _ in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))]


def _frameworks(dependencies: list[str]) -> list[str]:
    found: list[str] = []
    for dep in dependencies:
        lowered = dep.lower()
        for sign, label in FRAMEWORK_SIGNS.items():
            if sign in lowered and label not in found:
                found.append(label)
    return sorted(found)


def _readme_excerpt(root: Path) -> str:
    for name in _README_NAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8", errors="ignore").strip()[:_README_LIMIT]
    return ""


def _parse_manifest(path: Path) -> list[str]:
    try:
        name = path.name
        if name == "pyproject.toml":
            return _deps_from_pyproject(path)
        if name == "package.json":
            return _deps_from_package_json(path)
        if name == "Cargo.toml":
            return _deps_from_cargo(path)
        if name == "go.mod":
            return _deps_from_go_mod(path)
        return _deps_from_requirements(path)
    except (OSError, ValueError):
        return []


def _deps_from_pyproject(path: Path) -> list[str]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    names: list[str] = []
    project = data.get("project")
    if isinstance(project, dict):
        deps = project.get("dependencies")
        if isinstance(deps, list):
            names.extend(_req_name(str(item)) for item in deps)
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for group in optional.values():
                if isinstance(group, list):
                    names.extend(_req_name(str(item)) for item in group)
    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        for group in groups.values():
            if isinstance(group, list):
                names.extend(_req_name(str(item)) for item in group)
    return [name for name in names if name]


def _deps_from_package_json(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    names: list[str] = []
    if isinstance(data, dict):
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            section = data.get(key)
            if isinstance(section, dict):
                names.extend(str(dep) for dep in section)
    return names


def _deps_from_cargo(path: Path) -> list[str]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    deps = data.get("dependencies")
    return [str(dep) for dep in deps] if isinstance(deps, dict) else []


def _deps_from_go_mod(path: Path) -> list[str]:
    names: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith(("module", "go ", "require (", ")", "//")):
            continue
        token = line.removeprefix("require ").split()
        if token:
            names.append(token[0])
    return names


def _deps_from_requirements(path: Path) -> list[str]:
    names: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue
        name = _req_name(line)
        if name:
            names.append(name)
    return names


_REQ_NAME = re.compile(r"^[A-Za-z0-9._-]+")


def _req_name(spec: str) -> str:
    match = _REQ_NAME.match(spec.strip())
    return match.group(0).lower() if match else ""
