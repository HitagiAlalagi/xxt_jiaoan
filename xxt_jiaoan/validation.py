from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path

from .models import LessonItem
from .utils import clean_text


def validate_item(item: LessonItem) -> list[str]:
    errors = []
    required = [
        "file", "course_type", "date", "start_period", "end_period", "lesson_no",
        "theory_hours", "practice_hours", "topic", "analysis", "objectives",
        "key_points", "references", "class_select", "continuation_file",
        "textbook", "teaching_aids",
    ]
    data = asdict(item)
    for key in required:
        if not clean_text(str(data.get(key, ""))):
            errors.append(f"{item.file}: missing {key}")
    for key in ("theory_hours", "practice_hours", "other_hours"):
        value = clean_text(str(data.get(key, "")))
        if value and not re.fullmatch(r"\d+(?:\.\d+)?", value):
            errors.append(f"{item.file}: invalid {key}: {value}")
    if item.continuation_file and not Path(item.continuation_file).exists():
        errors.append(f"{item.file}: continuation_file not found: {item.continuation_file}")
    if item.file and not Path(item.file).exists():
        errors.append(f"{item.file}: lesson file not found")
    return errors
