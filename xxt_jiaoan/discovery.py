from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DOC_EXTENSIONS = {".doc", ".docx", ".pdf", ".xls", ".xlsx"}
DOCX_EXTENSION = ".docx"
IGNORED_DIRS = {
    ".git",
    ".venv",
    ".xxt_jiaoan",
    ".xxt_browser_profile",
    "__pycache__",
    "node_modules",
}


@dataclass
class CoursePaths:
    course_root: str
    lesson_root: str
    schedule_file: str
    standard_file: str
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def discover_course_paths(start: Path, config: dict[str, Any] | None = None) -> CoursePaths:
    config = config or {}
    start = start.resolve()
    base = start if start.is_dir() else start.parent
    course_root = find_course_root(base)

    lesson_root = configured_path(config, "lesson_root")
    schedule_file = configured_path(config, "schedule_file")
    standard_file = configured_path(config, "standard_file")

    warnings: list[str] = []
    if not lesson_root:
        lesson_root = find_lesson_root(base, course_root)
    if not schedule_file:
        schedule_file = find_schedule_file(base, course_root)
    if not standard_file:
        standard_file = find_standard_file(base, course_root)

    if not lesson_root:
        warnings.append("未找到教案目录。可用 --root 指向教案目录，或在配置中设置 lesson_root。")
    if not schedule_file:
        warnings.append("未找到授课计划文件。可在配置中设置 schedule_file。")
    if not standard_file:
        warnings.append("未找到授课标准文件。当前提交流程不依赖它，可按需在配置中设置 standard_file。")

    return CoursePaths(
        course_root=str(course_root),
        lesson_root=str(lesson_root or ""),
        schedule_file=str(schedule_file or ""),
        standard_file=str(standard_file or ""),
        warnings=warnings,
    )


def configured_path(config: dict[str, Any], key: str) -> Path | None:
    value = str(config.get(key, "") or "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    return path.resolve() if path.exists() else path


def find_course_root(base: Path) -> Path:
    candidates = [base, *base.parents]
    for candidate in candidates:
        if looks_like_course_root(candidate):
            return candidate
    return base


def looks_like_course_root(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    child_names = [child.name for child in safe_iterdir(path)]
    signals = 0
    if any(is_lesson_dir_name(name) for name in child_names):
        signals += 1
    if any(is_schedule_name(name) for name in child_names):
        signals += 1
    if any(is_standard_name(name) for name in child_names):
        signals += 1
    return signals >= 1


def find_lesson_root(base: Path, course_root: Path) -> Path | None:
    candidates = [(score_lesson_dir(path), path) for path in [base, *iter_dirs(course_root, max_depth=5)]]
    candidates = [(score, path) for score, path in candidates if score > 0]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], -len(item[1].parts)), reverse=True)
    return candidates[0][1]


def find_schedule_file(base: Path, course_root: Path | None = None) -> Path | None:
    search_root = course_root or find_course_root(base if base.is_dir() else base.parent)
    candidates = [(score_schedule_file(path), path) for path in iter_files(search_root, max_depth=5)]
    candidates = [(score, path) for score, path in candidates if score > 0]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], -len(item[1].parts)), reverse=True)
    return candidates[0][1]


def find_standard_file(base: Path, course_root: Path | None = None) -> Path | None:
    search_root = course_root or find_course_root(base if base.is_dir() else base.parent)
    candidates = [(score_standard_file(path), path) for path in iter_files(search_root, max_depth=5)]
    candidates = [(score, path) for score, path in candidates if score > 0]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], -len(item[1].parts)), reverse=True)
    return candidates[0][1]


def score_lesson_dir(path: Path) -> int:
    name = path.name
    if is_noise_dir(path) or is_schedule_name(name) or is_standard_name(name):
        return 0
    docx_count = count_lesson_docx(path, max_depth=3)
    if docx_count == 0:
        return 0
    score = min(docx_count, 20)
    if is_lesson_dir_name(name):
        score += 80
    if re.match(r"^\s*3[.、_\-]?\s*教案", name):
        score += 20
    return score


def count_lesson_docx(root: Path, max_depth: int) -> int:
    count = 0
    for path in iter_files(root, max_depth=max_depth):
        if is_lesson_docx(path):
            count += 1
    return count


def score_schedule_file(path: Path) -> int:
    if not path.is_file() or path.suffix.lower() != DOCX_EXTENSION or path.name.startswith("~$"):
        return 0
    text = f"{path.parent.name}/{path.name}"
    if not is_schedule_name(text):
        return 0
    score = 80
    if re.search(r"授课计划", path.name):
        score += 20
    if re.match(r"^\s*1[.、_\-]?", path.parent.name):
        score += 10
    return score


def score_standard_file(path: Path) -> int:
    if not path.is_file() or path.suffix.lower() not in DOC_EXTENSIONS or path.name.startswith("~$"):
        return 0
    text = f"{path.parent.name}/{path.name}"
    if not is_standard_name(text):
        return 0
    score = 70
    if re.search(r"授课标准|课程标准", path.name):
        score += 20
    if path.suffix.lower() == DOCX_EXTENSION:
        score += 5
    return score


def iter_dirs(root: Path, max_depth: int) -> list[Path]:
    results: list[Path] = []
    for path in iter_paths(root, max_depth=max_depth):
        if path.is_dir():
            results.append(path)
    return results


def iter_files(root: Path, max_depth: int) -> list[Path]:
    results: list[Path] = []
    for path in iter_paths(root, max_depth=max_depth):
        if path.is_file():
            results.append(path)
    return results


def iter_paths(root: Path, max_depth: int) -> list[Path]:
    root = root.resolve()
    results: list[Path] = []
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > max_depth or is_noise_dir(current):
            continue
        for child in safe_iterdir(current):
            results.append(child)
            if child.is_dir() and depth < max_depth and not is_noise_dir(child):
                stack.append((child, depth + 1))
    return results


def safe_iterdir(path: Path) -> list[Path]:
    try:
        return list(path.iterdir())
    except OSError:
        return []


def is_noise_dir(path: Path) -> bool:
    return path.name in IGNORED_DIRS or path.name.startswith(".xxt_")


def is_lesson_docx(path: Path) -> bool:
    name = path.name
    return (
        path.is_file()
        and path.suffix.lower() == DOCX_EXTENSION
        and not name.startswith("~$")
        and "续页" not in name
        and "模版" not in name
        and "模板" not in name
        and not is_schedule_name(name)
        and not is_standard_name(name)
    )


def is_lesson_dir_name(value: str) -> bool:
    return bool(re.search(r"教案|备课", value))


def is_schedule_name(value: str) -> bool:
    return bool(re.search(r"授课计划|教学进度|进度计划", value))


def is_standard_name(value: str) -> bool:
    return bool(re.search(r"授课标准|课程标准|教学标准", value))
