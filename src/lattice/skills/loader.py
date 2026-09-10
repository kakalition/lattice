"""agentskills.io skill loader."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from lattice.paths import lattice_home

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path


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


def scan_skills(home: Path | None = None, extra_dirs: list[Path] | None = None) -> list[Skill]:
    roots = [(home or lattice_home()) / "skills"]
    if extra_dirs:
        roots.extend(extra_dirs)
    skills: dict[str, Skill] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for skill_md in root.rglob("SKILL.md"):
            parsed = _parse_skill(skill_md)
            if parsed:
                skills[parsed.name] = parsed
    return list(skills.values())


def skill_index_entries(
    skills: list[Skill],
    *,
    prefer: list[str] | None = None,
    disable: list[str] | None = None,
) -> list[tuple[str, str]]:
    prefer = prefer or []
    disable = set(disable or [])
    eligible = [s for s in skills if s.name not in disable]
    preferred = [s for s in eligible if s.name in prefer]
    rest = [s for s in eligible if s.name not in prefer]
    ordered = preferred + rest
    return [(s.name, s.description) for s in ordered]
