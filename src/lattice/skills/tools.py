"""skills_list / skill_view tool helpers."""

from __future__ import annotations

from lattice.skills.activate import activate_skill
from lattice.skills.loader import Skill, skill_index_entries


def skills_list(
    skills: list[Skill], *, prefer: list[str] | None = None, disable: list[str] | None = None
) -> str:
    entries = skill_index_entries(skills, prefer=prefer, disable=disable)
    if not entries:
        return "(no skills)"
    return "\n".join(f"- {name}: {desc}" for name, desc in entries)


def skill_view(name: str, skills: list[Skill]) -> str:
    return activate_skill(name, skills)
