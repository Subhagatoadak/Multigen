"""Output parsing and formatting helpers."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any, Mapping, Type

from pydantic import BaseModel, ValidationError


def parse_llm_json(text: str, model: Type[BaseModel] | None = None) -> Any:
    """Parse LLM output as JSON and optionally validate with a Pydantic model."""

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError("LLM output is not valid JSON")
    if model:
        try:
            return model.model_validate(data)  # type: ignore[attr-defined]
        except AttributeError:
            return model.parse_obj(data)  # type: ignore[call-arg]
    return data


def validate_with_model(model: Type[BaseModel], data: Mapping[str, Any]) -> BaseModel:
    """Validate a mapping using a Pydantic model."""

    try:
        return model.model_validate(data)  # type: ignore[attr-defined]
    except AttributeError:
        return model.parse_obj(data)  # type: ignore[call-arg]


def to_json(data: Any, *, indent: int = 2) -> str:
    return json.dumps(data, indent=indent, ensure_ascii=False)


def to_xml(data: Mapping[str, Any], root_tag: str = "root") -> str:
    root = ET.Element(root_tag)
    for key, value in data.items():
        child = ET.SubElement(root, str(key))
        child.text = str(value)
    return ET.tostring(root, encoding="unicode")


def to_html(data: Mapping[str, Any], title: str = "Output") -> str:
    rows = "".join(f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in data.items())
    return f"<html><head><title>{title}</title></head><body><table>{rows}</table></body></html>"


def to_python_repr(data: Any) -> str:
    return repr(data)


def build_output(data: Any, fmt: str = "json", **kwargs: Any) -> str:
    """Render data in a desired format (json|xml|html|python|text)."""

    fmt = fmt.lower()
    if fmt == "json":
        return to_json(data, indent=kwargs.get("indent", 2))
    if fmt == "xml":
        return to_xml(data if isinstance(data, Mapping) else {"value": data}, root_tag=kwargs.get("root_tag", "root"))
    if fmt == "html":
        return to_html(data if isinstance(data, Mapping) else {"value": data}, title=kwargs.get("title", "Output"))
    if fmt == "python":
        return to_python_repr(data)
    return str(data)


__all__ = ["parse_llm_json", "validate_with_model", "build_output", "to_json", "to_xml", "to_html", "to_python_repr"]
