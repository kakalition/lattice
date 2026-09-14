"""Built-in ``profiles`` group — list and remove profiles."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from lattice.audit import audit_log
from lattice.config import ToolTier
from lattice.deps import TurnDeps
from lattice.profiles.store import list_profiles, remove_profile, validate_removable_profile
from lattice.tools.groups._common import ToolBinding, ToolGroup, ToolsetT
from lattice.tools.middleware import ToolPolicy

_GROUP = "profiles"


def _remove_precheck(ctx: RunContext[TurnDeps], args: dict[str, Any]) -> str | None:
    # Validate first so an invalid or protected id errors instead of prompting.
    return validate_removable_profile(str(args.get("profile_id") or ""), ctx.deps.settings.home)


_REMOVE_POLICY = ToolPolicy(
    precheck=_remove_precheck,
    summary=lambda _ctx, args: f"remove profile {args.get('profile_id', '')}",
)


def register_list(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def list(ctx: RunContext[TurnDeps]) -> str:
        names = list_profiles(ctx.deps.settings.home)
        return "\n".join(names) if names else "(no profiles)"

    return {"list": list}


def register_remove(toolset: ToolsetT) -> dict[str, Any]:
    @toolset.tool
    async def remove(ctx: RunContext[TurnDeps], profile_id: str) -> str:
        try:
            remove_profile(profile_id, ctx.deps.settings.home)
        except (ValueError, FileNotFoundError) as exc:
            return f"error: {exc}"
        cleared = await ctx.deps.session.clear_sticky_for_profile(profile_id)
        audit_log(
            "tool",
            {
                "name": f"{_GROUP}/remove",
                "profile_id": profile_id,
                "sticky_cleared": cleared,
            },
            home=ctx.deps.settings.home,
        )
        note = ""
        if ctx.deps.profile.id == profile_id:
            note = " (was active this turn — switch to another profile next message)"
        return f"removed profile {profile_id}; cleared {cleared} sticky mapping(s){note}"

    return {"remove": remove}


GROUP = ToolGroup(
    name=_GROUP,
    description="Profiles: list and remove agent profiles.",
    bindings=(
        ToolBinding("list", ToolTier.COLD, register_list),
        ToolBinding("remove", ToolTier.COLD, register_remove, _REMOVE_POLICY),
    ),
)
