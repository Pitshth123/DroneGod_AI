"""
settings_io.py — บันทึก / ส่งออก / โหลด การตั้งค่า cockpit (JSON)
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

SETTINGS_VERSION = 1


def default_settings_path() -> str:
    root = os.path.join(os.path.expanduser("~"), ".swarmgod")
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, "cockpit_settings.json")


def save_json(path: str, data: Dict[str, Any]) -> str:
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = dict(data)
    payload.setdefault("version", SETTINGS_VERSION)
    payload["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return path


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("settings file must be a JSON object")
    return data


def try_load_default() -> Optional[Dict[str, Any]]:
    path = default_settings_path()
    if not os.path.isfile(path):
        return None
    return load_json(path)
