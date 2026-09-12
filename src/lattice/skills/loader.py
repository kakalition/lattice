"""agentskills.io skill loader."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from lattice.paths import lattice_home

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


class Skill(BaseModel):
    name: str
    description: str
    body: str
    path: Path


class SkillsReport(BaseModel):
    skills: list[Skill] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def _parse_skill(path: Path) -> Skill | None:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if match:
        meta = yaml.safe_load(match.group(1)) or {}
        body = match.group(2).strip()
    else:
        meta = {}
        body = text.strip()
    name = str(meta.get("name") or path.parent.name)
    description = str(meta.get("description") or body.splitlines()[0][:120] if body else name)
    return Skill(name=name, description=description, body=body, path=path)


def _validate_skill(skill: Skill) -> str | None:
    """Return a human-readable reason for rejecting a skill, else ``None``."""
    folder = skill.path.parent.name
    if not SKILL_NAME_RE.match(skill.name):
        return f"invalid skill name {skill.name!r}"
    if skill.name != folder:
        return f"skill name {skill.name!r} does not match folder {folder!r}"
    if not skill.description.strip():
        return "empty description"
    return None


def _skill_roots(home: Path | None, extra_dirs: list[Path] | None) -> list[Path]:
    roots = [(home or lattice_home()) / "skills"]
    if extra_dirs:
        roots.extend(extra_dirs)
    return roots


def scan_skills_report(
    home: Path | None = None, extra_dirs: list[Path] | None = None
) -> SkillsReport:
    """Scan skill roots and validate each ``SKILL.md``.

    Invalid skills are skipped and reported in ``errors`` (the caller surfaces
    them as ``[notice]``); valid skills keep working, including the lenient
    name/description fallbacks hand-written skills rely on.
    """
    skills: dict[str, Skill] = {}
    errors: list[str] = []
    for root in _skill_roots(home, extra_dirs):
        if not root.is_dir():
            continue
        for skill_md in sorted(root.rglob("SKILL.md")):
            folder = skill_md.parent.name
            try:
                parsed = _parse_skill(skill_md)
            except Exception as exc:  # unreadable/bad YAML frontmatter
                errors.append(f"{folder}: {exc}")
                continue
            if parsed is None:
                errors.append(f"{folder}: unreadable skill")
                continue
            problem = _validate_skill(parsed)
            if problem:
                errors.append(f"{folder}: {problem}")
                continue
            skills[parsed.name] = parsed
    return SkillsReport(skills=list(skills.values()), errors=errors)


def scan_skills(home: Path | None = None, extra_dirs: list[Path] | None = None) -> list[Skill]:
    return scan_skills_report(home, extra_dirs=extra_dirs).skills


def scan_skills_for(
    home: Path | None = None,
    profile: Any | None = None,
    *,
    extra_dirs: list[Path] | None = None,
) -> SkillsReport:
    """Rescan including a profile's own ``skills/`` dir (same-turn freshness).

    Any call that re-reads skills mid-turn must reproduce this extra-dir logic or
    profile-local skills disappear from the result.
    """
    dirs = list(extra_dirs or [])
    root = getattr(profile, "root", None)
    if root is not None and (Path(root) / "skills").is_dir():
        dirs.append(Path(root) / "skills")
    return scan_skills_report(home, extra_dirs=dirs)


def skill_index_entries(
    skills: list[Skill],
    *,
    prefer: list[str] | None = None,
    disable: list[str] | None = None,
) -> list[tuple[str, str]]:
    prefer = list(dict.fromkeys(prefer or []))
    disabled = set(disable or [])
    eligible = [s for s in skills if s.name not in disabled]
    by_name = {s.name: s for s in eligible}
    preferred = [by_name[name] for name in prefer if name in by_name]
    rest = [s for s in eligible if s.name not in prefer]
    ordered = preferred + rest
    return [(s.name, s.description) for s in ordered]
