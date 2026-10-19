"""PIN gate and last-used UI state for a packing-shed PC."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shared import CONFIG_DIR, REFERENCE_DIR

STATE_PATH = REFERENCE_DIR / "ui_state.json"
AUTH_PATH = CONFIG_DIR / "auth.json"


def load_auth() -> dict[str, Any]:
    if not AUTH_PATH.exists():
        return {"pin": ""}
    try:
        return json.loads(AUTH_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"pin": ""}


def pin_required() -> bool:
    pin = str(load_auth().get("pin") or "").strip()
    return bool(pin)


def pin_ok(entered: str) -> bool:
    if not pin_required():
        return True
    return str(entered or "").strip() == str(load_auth().get("pin")).strip()


def load_ui_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_ui_state(payload: dict[str, Any]) -> None:
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
