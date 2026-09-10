"""Prompt tiers: identity / context / volatile — optimized for provider prompt caching."""

from __future__ import annotations

from dataclasses import dataclass, field

# Byte-stable routing instructions (never put per-turn notices here).
PRIMARY_ROUTING = """\
## Orchestration
You are the primary agent. Prefer `delegate(task, context)` for bounded research, lookup, \
or analysis that a secondary worker can finish with read-only tools (web, files, sqlite read). \
Synthesize the secondary's result for the user. Do not ask the secondary to talk to the user \
or call schedule/clarify/shell/write tools.
"""


@dataclass
class PromptBundle:
    identity: str
    context: str = ""
    volatile: str = ""
    skill_index: str = ""
    notices: list[str] = field(default_factory=list)
    routing: str = PRIMARY_ROUTING

    def stable_system_prompt(self) -> str:
        """Cacheable system prefix — excludes per-turn notices."""
        parts = [self.identity.strip()]
        if self.context.strip():
            parts.append(self.context.strip())
        if self.skill_index.strip():
            parts.append(self.skill_index.strip())
        if self.routing.strip():
            parts.append(self.routing.strip())
        if self.volatile.strip():
            parts.append(self.volatile.strip())
        return "\n\n".join(p for p in parts if p)

    def system_prompt(self) -> str:
        """Alias for stable system (notices must not live here)."""
        return self.stable_system_prompt()

    def user_volatile_preamble(self) -> str:
        """Per-turn context appended ahead of the user message (cache-busting tail only)."""
        if not self.notices:
            return ""
        return "\n".join(f"[notice] {n}" for n in self.notices)


def build_skill_index_xml(entries: list[tuple[str, str]]) -> str:
    if not entries:
        return ""
    # Sort for stable tool/prompt bytes across processes
    lines = ["<available_skills>"]
    for name, desc in sorted(entries, key=lambda x: x[0]):
        lines.append(f'  <skill name="{name}">{desc}</skill>')
    lines.append("</available_skills>")
    lines.append("Use skill_view(name) to load a full skill body when needed.")
    return "\n".join(lines)
