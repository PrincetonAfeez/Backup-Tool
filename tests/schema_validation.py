"""Minimal JSON Schema subset validator for Schema/ contract tests (stdlib only)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "Schema"


def load_schema_file(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def validate_document(document: Any, schema: dict[str, Any], *, path: str = "$") -> list[str]:
    """Validate ``document`` against a small JSON Schema subset used in Schema/."""

    errors: list[str] = []

    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(document, dict):
            return [f"{path}: expected object"]
        errors.extend(_validate_object(document, schema, path=path))
    elif schema_type == "array":
        if not isinstance(document, list):
            return [f"{path}: expected array"]
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(document):
                errors.extend(validate_document(item, item_schema, path=f"{path}[{index}]"))
    elif schema_type == "string":
        if not isinstance(document, str):
            errors.append(f"{path}: expected string")
        else:
            errors.extend(_validate_string(document, schema, path=path))
    elif schema_type == "integer":
        if not isinstance(document, int) or isinstance(document, bool):
            errors.append(f"{path}: expected integer")
        else:
            errors.extend(_validate_number(document, schema, path=path))
    elif schema_type == "number":
        if not isinstance(document, (int, float)) or isinstance(document, bool):
            errors.append(f"{path}: expected number")
        else:
            errors.extend(_validate_number(document, schema, path=path))
    elif schema_type == "boolean":
        if not isinstance(document, bool):
            errors.append(f"{path}: expected boolean")

    if "const" in schema and document != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}")

    if "enum" in schema and document not in schema["enum"]:
        errors.append(f"{path}: expected one of {schema['enum']!r}")

    return errors


def _validate_object(document: dict[str, Any], schema: dict[str, Any], *, path: str) -> list[str]:
    errors: list[str] = []
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    for key in required:
        if key not in document:
            errors.append(f"{path}: missing required property {key!r}")

    if schema.get("additionalProperties") is False:
        extra = set(document) - set(properties)
        for key in sorted(extra):
            errors.append(f"{path}.{key}: additional property not allowed")

    property_names = schema.get("propertyNames")
    if isinstance(property_names, dict):
        for key in document:
            errors.extend(validate_document(key, property_names, path=f"{path}.{key}"))

    for key, value in document.items():
        prop_schema = properties.get(key)
        if prop_schema is None:
            continue
        if "oneOf" in prop_schema:
            errors.extend(_validate_one_of(value, prop_schema["oneOf"], path=f"{path}.{key}"))
        else:
            errors.extend(validate_document(value, prop_schema, path=f"{path}.{key}"))

    return errors


def _validate_one_of(document: Any, options: list[dict[str, Any]], *, path: str) -> list[str]:
    branch_errors: list[str] = []
    for option in options:
        errors = validate_document(document, option, path=path)
        if not errors:
            return []
        branch_errors.extend(errors)
    return [f"{path}: value does not match any oneOf branch"] + branch_errors[:3]


def _validate_string(document: str, schema: dict[str, Any], *, path: str) -> list[str]:
    errors: list[str] = []
    min_length = schema.get("minLength")
    if min_length is not None and len(document) < min_length:
        errors.append(f"{path}: string shorter than minLength {min_length}")
    pattern = schema.get("pattern")
    if pattern is not None and re.fullmatch(pattern, document) is None:
        errors.append(f"{path}: string does not match pattern {pattern!r}")
    return errors


def _validate_number(document: int | float, schema: dict[str, Any], *, path: str) -> list[str]:
    errors: list[str] = []
    minimum = schema.get("minimum")
    if minimum is not None and document < minimum:
        errors.append(f"{path}: value {document} below minimum {minimum}")
    maximum = schema.get("maximum")
    if maximum is not None and document > maximum:
        errors.append(f"{path}: value {document} above maximum {maximum}")
    return errors
