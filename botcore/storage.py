import json
import os


def build_storage_path(base_file: str, storage_filename: str) -> str:
    base_dir = os.path.dirname(os.path.abspath(base_file))
    return os.path.join(base_dir, storage_filename)


def load_json_mapping(path: str) -> dict[str, dict[str, dict]]:
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(payload, dict):
        return {}

    sanitized: dict[str, dict[str, dict]] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        submap: dict[str, dict] = {}
        for subkey, subvalue in value.items():
            if isinstance(subkey, str) and isinstance(subvalue, dict):
                submap[subkey] = subvalue
        if submap:
            sanitized[key] = submap
    return sanitized


def save_json_mapping(path: str, payload: dict[str, dict[str, dict]]) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False

