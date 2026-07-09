from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .utils import clean_text


def submission_key(item: dict[str, Any]) -> str:
    return "|".join([
        clean_text(item.get("course_name", "")),
        clean_text(item.get("class_select", "")),
        clean_text(item.get("date", "")),
        clean_text(item.get("lesson_no", "")),
    ])


def read_success_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "key" in row:
            keys.add(row["key"])
    return keys


def append_success_log(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "key": submission_key(item),
        "submitted_at": datetime.now().isoformat(timespec="seconds"),
        "course_name": item.get("course_name", ""),
        "class_select": item.get("class_select", ""),
        "date": item.get("date", ""),
        "lesson_no": item.get("lesson_no", ""),
        "file": item.get("file", ""),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
