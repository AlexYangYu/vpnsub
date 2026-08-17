from __future__ import annotations

import re
from typing import Any

import yaml

FORBIDDEN_YAML_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


class SafeConfigError(ValueError):
    """Configuration error whose message is safe to expose in status output."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def load_yaml_bytes(payload: bytes, *, source: str) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SafeConfigError("invalid_utf8", f"{source} is not valid UTF-8") from exc
    return load_yaml_text(text, source=source)


def load_yaml_text(text: str, *, source: str) -> dict[str, Any]:
    if FORBIDDEN_YAML_CONTROLS.search(text):
        raise SafeConfigError(
            "forbidden_control", f"{source} contains forbidden control characters"
        )
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SafeConfigError("invalid_yaml", f"{source} is not valid YAML") from exc
    if not isinstance(value, dict):
        raise SafeConfigError("invalid_root", f"{source} must contain a YAML mapping")
    return value


def dump_yaml(config: dict[str, Any]) -> str:
    return yaml.safe_dump(
        config,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=4096,
    )
