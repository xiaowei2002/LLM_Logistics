"""YAML 文件加载工具。"""

from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: str | Path) -> Any:
    """读取并解析 YAML 文件，返回对应的 Python 对象。"""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
