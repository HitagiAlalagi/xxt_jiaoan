from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LessonItem:
    file: str
    course_name: str
    course_type: str
    raw_class: str
    date: str
    start_period: str
    end_period: str
    lesson_no: str
    theory_hours: str
    practice_hours: str
    other_hours: str
    textbook: str
    teaching_aids: str
    topic: str
    analysis: str
    objectives: str
    key_points: str
    references: str
    reflection: str
    week: int
    kind: str
    class_select: str
    continuation_file: str
