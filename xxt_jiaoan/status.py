from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .success_log import read_success_keys, submission_key
from .submitter import XxtSubmitter
from .utils import clean_text


def exact_record_matches_item(record: dict[str, str], item: dict[str, Any]) -> bool:
    course = item.get("course_name", "")
    return (
        clean_text(record.get("course_name", "").replace("*", "")) == clean_text(course)
        and clean_text(record.get("class_select", "")) == clean_text(item.get("class_select", ""))
        and clean_text(record.get("date", "")) == clean_text(item.get("date", ""))
        and clean_text(record.get("lesson_no", "")) == clean_text(item.get("lesson_no", ""))
        and clean_text(record.get("status", "")) != "已撤销"
    )


def blank_date_record_matches_item(record: dict[str, str], item: dict[str, Any]) -> bool:
    course = item.get("course_name", "")
    return (
        clean_text(record.get("course_name", "").replace("*", "")) == clean_text(course)
        and clean_text(record.get("class_select", "")) == clean_text(item.get("class_select", ""))
        and clean_text(record.get("date", "")) == ""
        and clean_text(record.get("lesson_no", "")) == clean_text(item.get("lesson_no", ""))
        and clean_text(record.get("status", "")) != "已撤销"
    )


def summarize_by_class(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(item.get("class_select", ""), []).append(item)
    summary = []
    for class_select in sorted(grouped):
        rows = sorted(
            grouped[class_select],
            key=lambda x: int(x.get("lesson_no", "0")) if str(x.get("lesson_no", "")).isdigit() else 0,
        )
        summary.append(
            {
                "class_select": class_select,
                "count": len(rows),
                "lesson_numbers": [str(x.get("lesson_no", "")) for x in rows],
                "items": rows,
            }
        )
    return summary


def print_status_summary(report: dict[str, Any]) -> None:
    print(
        "教案提交状态: "
        f"总数 {report['total_count']}；"
        f"网页精确已提交 {report['web_submitted_count']}；"
        f"网页空日期疑似已提交 {report['web_blank_date_count']}；"
        f"本地成功日志 {report['local_success_count']}；"
        f"未提交 {report['unsubmitted_count']}",
        flush=True,
    )
    for title, key in [
        ("网页精确已提交", "web_submitted_by_class"),
        ("网页空日期疑似已提交", "web_blank_date_by_class"),
        ("本地成功日志", "local_success_by_class"),
        ("未提交", "unsubmitted_by_class"),
    ]:
        print(title, flush=True)
        rows = report[key]
        if not rows:
            print("- 无", flush=True)
            continue
        for row in rows:
            print(f"- {row['class_select']}: {row['count']} 条；课次 {'、'.join(row['lesson_numbers'])}", flush=True)


def pending_items_from_report(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for class_row in report["unsubmitted_by_class"]
        for item in class_row["items"]
    ]


async def build_status_report(
    submitter: XxtSubmitter,
    items: list[dict[str, Any]],
    *,
    success_log: Path | None = None,
) -> dict[str, Any]:
    await submitter.open_record_page()
    records_by_course: dict[str, list[dict[str, str]]] = {}
    for item in items:
        course = item.get("course_name") or submitter.config["course_query"]
        if course not in records_by_course:
            records_by_course[course] = await submitter.query_record_rows(course)

    local_success_keys = read_success_keys(success_log) if success_log else set()
    web_submitted = []
    web_blank_date = []
    local_success = []
    unsubmitted = []
    for item in items:
        course = item.get("course_name") or submitter.config["course_query"]
        records = records_by_course.get(course, [])
        exact = next((record for record in records if exact_record_matches_item(record, item)), None)
        blank_date = next((record for record in records if blank_date_record_matches_item(record, item)), None)
        if exact:
            web_submitted.append({**item, "_record": exact})
        elif blank_date:
            web_blank_date.append({**item, "_record": blank_date})
        elif success_log and submission_key(item) in local_success_keys:
            local_success.append(item)
        else:
            unsubmitted.append(item)

    report = {
        "total_count": len(items),
        "web_record_count": sum(len(rows) for rows in records_by_course.values()),
        "web_submitted_count": len(web_submitted),
        "web_blank_date_count": len(web_blank_date),
        "local_success_count": len(local_success),
        "unsubmitted_count": len(unsubmitted),
        "web_submitted_by_class": summarize_by_class(web_submitted),
        "web_blank_date_by_class": summarize_by_class(web_blank_date),
        "local_success_by_class": summarize_by_class(local_success),
        "unsubmitted_by_class": summarize_by_class(unsubmitted),
        "web_records": records_by_course,
    }
    submitter.debug_dir.mkdir(parents=True, exist_ok=True)
    (submitter.debug_dir / "submission_status_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report
