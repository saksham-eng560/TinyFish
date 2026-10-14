"""Validate every structured LLM reply with Pydantic (docs/HIREFLOW_PLAN.md §5.2).

The JSON schemas in :mod:`app.services.llm_schemas` (what the providers are asked for) are turned
into Pydantic models here, so a reply is checked field by field: types, enums, nested objects and
lists. Missing keys get the schemas' "nothing here" value first ("" / [] / 0 / false), and ``null``
is read the same way, so only replies that are actually wrong fail. A failed reply is retried once
and then left to the next provider; when they all fail, callers fall back to their heuristics.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError, create_model


class InvalidOutput(ValueError):
    """The reply doesn't match the schema."""


def _or(default: Any) -> BeforeValidator:
    return BeforeValidator(lambda v: default if v is None else v)


def _as_text(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return value


def _enum_value(value: Any, allowed: tuple[str, ...]) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else ""


def _type(schema: dict[str, Any], name: str) -> Any:
    kind = schema.get("type")
    if schema.get("enum"):
        # Lenient: case is ignored and an unknown or missing value becomes "" (callers fall back to their default)
        allowed = tuple(schema["enum"])
        return Annotated[Literal[("", *allowed)], BeforeValidator(lambda v: _enum_value(v, allowed))]  # type: ignore[misc]
    if kind == "object" or "properties" in schema:
        return Annotated[_model_for(json.dumps(schema, sort_keys=True), name), _or({})]
    if kind == "array":
        items = schema.get("items")
        inner = _type(items, f"{name}Item") if isinstance(items, dict) else Any
        return Annotated[list[inner], _or([])]  # type: ignore[valid-type]
    if kind == "string":  # "gpa": 3.8 or "start_date": 2021 is a fine answer to a text field
        return Annotated[str, BeforeValidator(_as_text)]
    simple = {"integer": (int, 0), "number": (float, 0.0), "boolean": (bool, False)}
    if kind in simple:
        python_type, default = simple[kind]
        return Annotated[python_type, _or(default)]
    return Any


@lru_cache(maxsize=128)
def _model_for(schema_json: str, name: str) -> type[BaseModel]:
    schema = json.loads(schema_json)
    fields: dict[str, Any] = {}
    for key, sub in (schema.get("properties") or {}).items():
        fields[key] = (_type(sub, f"{name}_{key}"), ...)
    return create_model(name, __config__=ConfigDict(extra="allow"), **fields)


def model_for(schema: dict[str, Any], name: str = "LLMReply") -> type[BaseModel]:
    """The Pydantic model for one of the JSON schemas in ``llm_schemas``."""
    return _model_for(json.dumps(schema, sort_keys=True), name)


def _resume(value: Any, where: str) -> None:
    """Resumes are checked with the resume's own model, which also accepts the looser shapes models
    produce (a certification as plain text, an award as an object) and normalises them later."""
    from app.schemas.resume_content import ResumeContent, normalize_resume

    if not isinstance(value, dict):
        raise InvalidOutput(f"{where}: Input should be a JSON object")
    try:
        ResumeContent.model_validate(normalize_resume(value))
    except (ValidationError, TypeError, ValueError, AttributeError) as exc:
        raise InvalidOutput(f"{where}: {exc}") from exc


def validate_reply(value: dict[str, Any], schema: dict[str, Any] | None) -> dict[str, Any]:
    """The reply, checked and normalised (``"85"`` -> 85, ``null`` -> ""), or :class:`InvalidOutput`."""
    if not schema:
        return value
    from app.services import llm_schemas
    from app.services.llm import fill_defaults

    if schema is llm_schemas.RESUME_SCHEMA:
        _resume(value, "resume")
        return value
    if schema is llm_schemas.TAILORED_RESUME_SCHEMA:
        _resume(value.get("tailored_resume"), "tailored_resume")
        changes = value.get("changes_made") or []
        if not isinstance(changes, list):
            raise InvalidOutput("changes_made: Input should be a valid list")
        return {**value, "changes_made": [str(c) for c in changes]}

    try:
        checked = model_for(schema).model_validate(fill_defaults(value, schema))
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5])
        raise InvalidOutput(problems) from exc
    return checked.model_dump()
