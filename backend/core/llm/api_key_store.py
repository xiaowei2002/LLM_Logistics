"""后端保存的 API Key：前端在设置页保存时传一次，聊天请求不再携带。"""

import json
from pathlib import Path

API_KEY_FILE = Path(__file__).resolve().parents[2] / "data" / "api_key.json"


def load_api_key() -> str:
    if API_KEY_FILE.exists():
        try:
            return json.loads(API_KEY_FILE.read_text(encoding="utf-8")).get("api_key", "")
        except (OSError, json.JSONDecodeError):
            return ""
    return ""


def save_api_key(key: str) -> None:
    key = key.strip()
    if not key:
        if API_KEY_FILE.exists():
            API_KEY_FILE.unlink(missing_ok=True)
        return
    API_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    API_KEY_FILE.write_text(
        json.dumps({"api_key": key}, ensure_ascii=False), encoding="utf-8"
    )
