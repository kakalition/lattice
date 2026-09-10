"""Activate skill body into turn context (via tool result, not system rewrite)."""

from __future__ import annotations

from lattice.skills.loader import Skill, scan_skills


def find_skill(name: str, skills: list[Skill] | None = None) -> Skill | None:
    skills = skills if skills is not None else scan_skills()
    for skill in skills:
        if skill.name == name:
            return skill
    return None


def activate_skill(name: str, skills: list[Skill] | None = None) -> str:
    skill = find_skill(name, skills)
    if not skill:
        return f"skill not found: {name}"
    return f"# Skill: {skill.name}\n\n{skill.body}"
