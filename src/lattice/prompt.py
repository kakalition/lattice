"""Prompt tiers: identity / context / volatile — optimized for provider prompt caching."""

from __future__ import annotations

from pydantic import BaseModel, Field

# Byte-stable routing instructions (never put per-turn notices here).
PRIMARY_ROUTING = """\
## Orchestration
You are the primary agent. Use `delegate(task, context)` to hand a bounded research, \
lookup, or analysis subtask to a secondary worker. The secondary has the same tools you \
have (minus `delegate`), so it can also run shell/write/sqlite/schedule actions when the \
task needs them; high-blast-radius actions stay approval-gated. The secondary cannot talk \
to the user, so synthesize its result yourself. Delegate only to one level — the secondary \
cannot delegate further.

### Turn routing protocol
A turn that begins with the literal marker `[route]` is a routing probe, not a normal user \
request. When you see `[route]`, reply with exactly one JSON object and nothing else:
{"complexity":"LOW|HIGH","task":"<self-contained instruction>","reason":"<short>"}
- LOW: a bounded single-pass answer or generation, at most trivial tool use, no planning.
- HIGH: multi-step work, tool orchestration, coding, ambiguity, safety-sensitive actions, \
or long-context synthesis.
For a normal turn (no `[route]` marker), answer normally and never emit this JSON.
"""

# User-facing directive for the router's worker (no classifier protocol).
WORKER_ROUTING = """\
## Worker mode
You are answering the end user directly for this bounded, single-pass task. Be concise and \
complete, and address the user in their language. Use your tools only when the task needs \
them and never invent tools you lack. High-blast-radius actions (destructive shell, sqlite \
DDL/DML, script execution) stay approval-gated. You have no `delegate` tool.
"""


class PromptBundle(BaseModel):
    identity: str
    context: str = ""
    volatile: str = ""
    skill_index: str = ""
    notices: list[str] = Field(default_factory=list)
    routing: str = PRIMARY_ROUTING

    def stable_system_prompt(self) -> str:
        """Cacheable system prefix — excludes per-turn notices and volatile text."""
        parts = [self.identity.strip()]
        if self.context.strip():
            parts.append(self.context.strip())
        if self.skill_index.strip():
            parts.append(self.skill_index.strip())
        if self.routing.strip():
            parts.append(self.routing.strip())
        return "\n\n".join(p for p in parts if p)

    def volatile_system_prompt(self) -> str:
        """Per-turn system text, kept out of the cacheable prefix."""
        return self.volatile.strip()

    def system_prompt(self) -> str:
        """Alias for stable system (notices must not live here)."""
        return self.stable_system_prompt()

    def worker_system_prompt(self) -> str:
        """User-facing worker prefix — same identity/context/skills, no classifier protocol."""
        parts = [self.identity.strip()]
        if self.context.strip():
            parts.append(self.context.strip())
        if self.skill_index.strip():
            parts.append(self.skill_index.strip())
        if WORKER_ROUTING.strip():
            parts.append(WORKER_ROUTING.strip())
        return "\n\n".join(p for p in parts if p)

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
