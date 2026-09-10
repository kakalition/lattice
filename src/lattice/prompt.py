"""Prompt tiers: identity / context / volatile."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PromptBundle:
    identity: str
    context: str = ""
    volatile: str = ""
    skill_index: str = ""
    notices: list[str] = field(default_factory=list)

    def system_prompt(self) -> str:
        """Byte-stable within a turn — notices appended as volatile tier only."""
        parts = [self.identity.strip()]
        if self.context.strip():
            parts.append(self.context.strip())
        if self.skill_index.strip():
            parts.append(self.skill_index.strip())
        if self.volatile.strip():
            parts.append(self.volatile.strip())
        if self.notices:
            parts.append("\n".join(f"[notice] {n}" for n in self.notices))
        return "\n\n".join(p for p in parts if p)


def build_skill_index_xml(entries: list[tuple[str, str]]) -> str:
    if not entries:
        return ""
    lines = ["<available_skills>"]
    for name, desc in entries:
        lines.append(f'  <skill name="{name}">{desc}</skill>')
    lines.append("</available_skills>")
    lines.append("Use skill_view(name) to load a full skill body when needed.")
    return "\n".join(lines)
