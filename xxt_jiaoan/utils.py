from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "utf-16-le", "gb18030"):
        try:
            return json.loads(raw.decode(enc))
        except Exception:
            continue
    raise UnicodeDecodeError("json", raw, 0, 1, f"cannot decode {path}")


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def comparable_text(value: str) -> str:
    text = clean_text(value)
    return re.sub(r"(?<=[A-Za-z])\s+(?=[A-Za-z_$][A-Za-z0-9_$]*\()", "", text)
