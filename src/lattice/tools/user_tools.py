"""Declarative, script-backed user tools (``home/tools/<name>.yaml``).

User tools are an additive, runtime layer: they never touch ``CORE_TOOL_NAMES``
or the static ``_MODULES`` registry. Manifests are read from Lattice home on
each turn, so a newly written tool is callable on the following turn.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_core import SchemaValidator, core_schema

from lattice.deps import CORE_TOOL_NAMES, TurnDeps, maybe_approve, traced, truncate_result
from lattice.hitl.policies import script_needs_approval
from lattice.paths import lattice_home
from lattice.tools.file_safety import resolve_agent_path
from lattice.tools.script import LANG_EXTS, execute_script, format_script_result

TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DEFAULT_PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}}

# Manifests advertise a JSON schema but are not fully validated (no jsonschema
# dependency); the toolset accepts any object shape and the handler is the final
# validator. This mirrors the MCP toolset's pass-through policy.
_ANY_ARGS_VALIDATOR = SchemaValidator(schema=core_schema.any_schema())

_JSON_TYPES: dict[str, Any] = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


class ToolHandler(BaseModel):
    """Exactly one of ``path`` (a script under the agent path jail) or ``code``."""

    path: str | None = None
    code: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> ToolHandler:
        has_path = bool((self.path or "").strip())
        has_code = bool((self.code or "").strip())
        if has_path == has_code:
            raise ValueError("handler must set exactly one of path or code")
        return self


class UserToolSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: dict(DEFAULT_PARAMETERS))
    language: str = "python"
    handler: ToolHandler
    timeout_seconds: float | None = None

    @property
    def digest(self) -> str:
        """Stable content hash; an edited handler forces a toolset rebuild."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class UserTools(BaseModel):
    specs: list[UserToolSpec] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def _load_manifest(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    loaded = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError("manifest must be a mapping")
    return loaded


def _first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return str(exc)
    loc = ".".join(str(p) for p in errors[0].get("loc", ()))
    msg = errors[0].get("msg", "invalid")
    return f"{loc}: {msg}" if loc else str(msg)


def scan_user_tools(home: Path | None = None) -> UserTools:
    """Read ``home/tools/*.{yaml,yml,json}``; sort by name for byte-stable schemas."""
    root = (home or lattice_home()) / "tools"
    if not root.is_dir():
        return UserTools()

    candidates = sorted(
        [*root.glob("*.yaml"), *root.glob("*.yml"), *root.glob("*.json")],
        key=lambda p: p.name,
    )

    specs: list[UserToolSpec] = []
    errors: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        stem = path.stem
        try:
            raw = _load_manifest(path)
        except Exception as exc:
            errors.append(f"{stem}: malformed manifest ({exc})")
            continue
        name = str(raw.get("name") or stem).strip()
        if not TOOL_NAME_RE.match(name):
            errors.append(f"{stem}: invalid tool name {name!r} (use ^[a-z][a-z0-9_]*$)")
            continue
        if name in CORE_TOOL_NAMES:
            errors.append(f"{stem}: reserved core tool name")
            continue
        if name in seen:
            errors.append(f"{stem}: duplicate tool name {name!r}")
            continue
        payload = {**raw, "name": name}
        try:
            spec = UserToolSpec(**payload)
        except ValidationError as exc:
            errors.append(f"{stem}: {_first_error(exc)}")
            continue
        if spec.language not in LANG_EXTS:
            errors.append(f"{stem}: unknown language {spec.language!r}")
            continue
        seen.add(name)
        specs.append(spec)

    specs.sort(key=lambda s: s.name)
    return UserTools(specs=specs, errors=errors)


def validate_args(schema: dict[str, Any], args: dict[str, Any]) -> str | None:
    """Shallow required/type check; the handler remains the final validator."""
    if not isinstance(args, dict):
        return "arguments must be a JSON object"
    required = schema.get("required") or []
    missing = [str(key) for key in required if key not in args]
    if missing:
        return f"missing required argument(s): {', '.join(missing)}"
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        return None
    for key, value in args.items():
        raw_spec = properties.get(key)
        if not isinstance(raw_spec, dict):
            continue
        expected = raw_spec.get("type")
        py_type = _JSON_TYPES.get(str(expected))
        if py_type is None:
            continue
        # bool is a subclass of int; JSON booleans must not satisfy number/integer.
        if str(expected) in ("integer", "number") and isinstance(value, bool):
            return f"{key} must be {expected}"
        if not isinstance(value, py_type):
            return f"{key} must be {expected}"
    return None


def _handler_body(spec: UserToolSpec, ctx: Any) -> str:
    if (spec.handler.code or "").strip():
        return spec.handler.code or ""
    target = resolve_agent_path(
        spec.handler.path or "", ctx.deps.workspace, home=ctx.deps.settings.home
    )
    if not target.is_file():
        raise FileNotFoundError(f"handler not found: {target}")
    return target.read_text(encoding="utf-8", errors="replace")


class UserToolset(AbstractToolset[TurnDeps]):
    """Expose ``UserToolSpec`` manifests to the model as callable tools."""

    def __init__(self, specs: list[UserToolSpec], *, id: str | None = None) -> None:
        self.specs = tuple(specs)
        self._by_name = {spec.name: spec for spec in self.specs}
        self._id = id

    @property
    def id(self) -> str | None:
        return self._id

    async def get_tools(self, ctx: Any) -> dict[str, ToolsetTool[TurnDeps]]:
        out: dict[str, ToolsetTool[TurnDeps]] = {}
        # Sort by stable name so user tool schemas are byte-identical across
        # discovery orders, preserving provider-side cache prefixes.
        for spec in sorted(self.specs, key=lambda s: s.name):
            out[spec.name] = ToolsetTool(
                toolset=self,
                tool_def=ToolDefinition(
                    name=spec.name,
                    description=spec.description,
                    parameters_json_schema=spec.parameters or dict(DEFAULT_PARAMETERS),
                ),
                max_retries=ctx.max_retries,
                args_validator=_ANY_ARGS_VALIDATOR,
            )
        return out

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: Any, tool: ToolsetTool[TurnDeps]
    ) -> Any:
        spec = self._by_name.get(name)
        if spec is None:
            return f"unknown user tool: {name}"
        problem = validate_args(spec.parameters or DEFAULT_PARAMETERS, tool_args)
        if problem:
            return f"{name}: {problem}"
        try:
            body = _handler_body(spec, ctx)
        except Exception as exc:
            return f"{name}: handler error: {exc}"

        summary = spec.handler.path or f"inline {spec.language}"
        denied = await maybe_approve(
            ctx,
            name,
            summary,
            needs=script_needs_approval(body, language=spec.language),
            language=spec.language,
            path=spec.handler.path,
            code=body,
        )
        if denied:
            return denied

        args_json = json.dumps(tool_args, default=str)

        async def _op() -> str:
            try:
                result = await execute_script(
                    language=spec.language,
                    code=body,
                    timeout=spec.timeout_seconds or 60.0,
                    workspace=ctx.deps.workspace,
                    home=ctx.deps.settings.home,
                    cfg=ctx.deps.settings.scripts,
                    stdin=args_json,
                    env_extra={
                        "LATTICE_TOOL_ARGS": args_json,
                        "LATTICE_TOOL_NAME": spec.name,
                        "LATTICE_TOOL_LANGUAGE": spec.language,
                    },
                )
                return truncate_result(format_script_result(result))
            except Exception as exc:
                return f"{name} error: {exc}"

        return await traced(ctx, name, dict(tool_args), _op)


__all__ = [
    "DEFAULT_PARAMETERS",
    "ToolHandler",
    "UserToolSpec",
    "UserToolset",
    "UserTools",
    "scan_user_tools",
    "validate_args",
]
