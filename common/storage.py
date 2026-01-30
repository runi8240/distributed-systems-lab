import json
import os
from typing import Any, Dict


def _merge_defaults(default: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for key, value in default.items():
        if key not in data:
            merged[key] = value
        elif isinstance(value, dict) and isinstance(data.get(key), dict):
            merged[key] = _merge_defaults(value, data[key])
        else:
            merged[key] = data[key]
    for key, value in data.items():
        if key not in merged:
            merged[key] = value
    return merged


def load_json(path: str, default: Dict[str, Any]) -> Dict[str, Any]:
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return default
    return _merge_defaults(default, data)


def save_json_atomic(path: str, data: Dict[str, Any]) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)
