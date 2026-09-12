"""Prompt tiers: identity / context / volatile — optimized for provider prompt caching."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PromptBundle(BaseModel):
    identity: str
    context: str = ""
    volatile: str = ""
    skill_index: str = ""
    notices: list[str] = Field(default_factory=list)

    def stable_system_prompt(self) -> str:
        """Cacheable system prefix — excludes per-turn notices and volatile text."""
        parts = [self.identity.strip()]
        if self.context.strip():
            parts.append(self.context.strip())
        if self.skill_index.strip():
            parts.append(self.skill_index.strip())
        return "\n\n".join(p for p in parts if p)

    def volatile_system_prompt(self) -> str:
        """Per-turn system text, kept out of the cacheable prefix."""
        return self.volatile.strip()

    def system_prompt(self) -> str:
        """Alias for stable system (notices must not live here)."""
        return self.stable_system_prompt()

    def user_volatile_preamble(self) -> str:
        """Per-turn context appended ahead of the user message (cache-busting tail only)."""
        if not self.notices:
            return ""
        return "\n".join(f"[notice] {n}" for n in self.notices)


def build_runtime_notice(
    *,
    workspace: str,
    now: str,
    timezone: str,
    databases: list[tuple[str, str]],
    profile_id: str,
    preferred_skills: list[str],
    user_tools: list[str],
) -> str:
    """Compact per-turn environment context (volatile tail, never cached prefix).

    Directly targets the observed waste: repeated ``find /`` / ``ls`` scans,
    stray ``cd``, and registering a relative DB path against the wrong root.
    Keep it to a few lines.
    """
    dbs = "; ".join(f"{name} -> {path}" for name, path in databases) or "(none registered)"
    lines = [
        f"Runtime: workspace={workspace}; now={now} ({timezone}); profile={profile_id}",
        f"Registered DBs: {dbs}",
    ]
    if user_tools:
        lines.append("User tools: " + ", ".join(user_tools))
    if preferred_skills:
        lines.append("Preferred skills: " + ", ".join(preferred_skills))
    canonical = next(
        (f"{name} -> {path}" for name, path in databases if "finance" in name.lower()),
        None,
    )
    if canonical:
        lines.append(f"Canonical finance DB: {canonical}")
    lines.append(
        "Rules: the shell cwd is already the workspace — do not `cd`; never run `find /` "
        "or scan `~`; use search_files/read_file to locate files and sqlite_schema to "
        "inspect databases; register an existing workspace DB by its relative path."
    )
    return "\n".join(lines)


def build_action_notice(actions: list[Any], *, limit: int = 15) -> str:
    """Compact render of the session's action ledger for the volatile tail.

    Kept out of the cached system prefix (it changes every turn) and out of the
    summarizer transcript (bounded, not worth summarizing).
    """
    if not actions:
        return ""
    lines = ["Recent actions:"]
    for action in actions[-limit:]:
        if isinstance(action, dict):
            tool = str(action.get("tool") or "?")
            target = str(action.get("target") or "")
            ok = bool(action.get("ok", True))
            artifacts = action.get("artifacts") or []
        else:
            tool = str(getattr(action, "tool", "?"))
            target = str(getattr(action, "target", "") or "")
            ok = bool(getattr(action, "ok", True))
            artifacts = getattr(action, "artifacts", []) or []
        suffix = f" {target}" if target else ""
        if artifacts:
            suffix += " -> " + ", ".join(str(a) for a in artifacts)
        lines.append(f"- {tool}{suffix} → {'ok' if ok else 'failed'}")
    return "\n".join(lines)


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
