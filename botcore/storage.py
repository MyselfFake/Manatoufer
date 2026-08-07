import json
import os
import tempfile


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
        backup_path = f"{path}.corrupt"
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as source_file:
                    content = source_file.read()
                if content:
                    with open(backup_path, "w", encoding="utf-8") as backup_file:
                        backup_file.write(content)
        except OSError:
            pass

        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as file:
                json.dump({}, file, ensure_ascii=False, indent=2)
        except OSError:
            pass
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
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    temp_handle = None
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory or ".", delete=False) as temp_file:
            temp_handle = temp_file
            temp_path = temp_file.name
            json.dump(payload, temp_file, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
        return True
    except OSError:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        return False

