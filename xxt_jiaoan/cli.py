from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path

from .discovery import discover_course_paths
from .models import LessonItem
from .parser import align_items_with_schedule, discover_docx, parse_docx
from .status import build_status_report, pending_items_from_report, print_status_summary
from .submitter import XxtSubmitter
from .success_log import append_success_log, read_success_keys, submission_key
from .utils import read_json, write_json
from .validation import validate_item


APP_DIR = Path(".xxt_jiaoan")
DEFAULT_CONFIG = APP_DIR / "config.json"
DEFAULT_PAYLOAD = APP_DIR / "payload.json"
DEFAULT_DEBUG_DIR = APP_DIR / "debug"
DEFAULT_SUCCESS_LOG = APP_DIR / "submit_success.jsonl"
DEFAULT_BROWSER_PROFILE = APP_DIR / "browser_profile"


def print_remaining_summary(items: list[dict], title: str = "剩余未提交课次") -> None:
    by_class: dict[str, list[dict]] = {}
    for item in items:
        by_class.setdefault(item.get("class_select", ""), []).append(item)
    print(f"{title}: {len(items)} 条", flush=True)
    for class_select in sorted(by_class):
        rows = sorted(
            by_class[class_select],
            key=lambda x: int(x.get("lesson_no", "0")) if str(x.get("lesson_no", "")).isdigit() else 0,
        )
        lesson_numbers = "、".join(str(x.get("lesson_no", "")) for x in rows)
        print(f"- {class_select}: {len(rows)} 条；课次 {lesson_numbers}", flush=True)


def command_parse(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"配置文件不存在: {config_path}。请先运行 xxt-jiaoan init-config。", file=sys.stderr)
        return 2
    config = read_json(config_path)
    discovered = discover_course_paths(Path(args.root), config)
    root = Path(discovered.lesson_root or args.root)
    if discovered.schedule_file and not config.get("schedule_file"):
        config = {**config, "schedule_file": discovered.schedule_file}
    docs = discover_docx(root)
    if not docs:
        print(f"未找到教案 docx: {root}", file=sys.stderr)
        for warning in discovered.warnings:
            print(f"- {warning}", file=sys.stderr)
        return 2
    items = [parse_docx(path, config) for path in docs]
    if args.min_week:
        items = [x for x in items if x.week >= args.min_week]
    if args.max_week:
        items = [x for x in items if x.week <= args.max_week]
    schedule_warnings = align_items_with_schedule(items, root, config)
    errors = [err for item in items for err in validate_item(item)]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, [asdict(x) for x in items])
    print(f"已生成 {args.output}: {len(items)} 条")
    for warning in schedule_warnings:
        print(f"- {warning}", file=sys.stderr)
    if errors:
        print("发现需要人工确认的问题：", file=sys.stderr)
        for err in errors:
            print(f"- {err}", file=sys.stderr)
        return 2
    return 0


def command_discover(args: argparse.Namespace) -> int:
    config = read_json(Path(args.config)) if Path(args.config).exists() else {}
    discovered = discover_course_paths(Path(args.root), config)
    print(discovered.to_json())
    return 2 if args.strict and discovered.warnings else 0


def command_validate(args: argparse.Namespace) -> int:
    items = read_json(Path(args.payload))
    errors = []
    for item in items:
        errors.extend(validate_item(LessonItem(**item)))
    if errors:
        for err in errors:
            print(f"- {err}", file=sys.stderr)
        return 2
    print(f"校验通过: {len(items)} 条")
    return 0


def command_init_config(args: argparse.Namespace) -> int:
    target = Path(args.output)
    if target.exists() and not args.force:
        print(f"配置文件已存在: {target}。如需覆盖请加 --force。", file=sys.stderr)
        return 2
    target.parent.mkdir(parents=True, exist_ok=True)
    source = files("xxt_jiaoan").joinpath("config.example.json")
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"已生成配置模板: {target}")
    return 0


async def command_submit_async(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"配置文件不存在: {config_path}。请先运行 xxt-jiaoan init-config。", file=sys.stderr)
        return 2
    config = read_json(config_path)
    items = read_json(Path(args.payload))
    if args.limit:
        items = items[: args.limit]
    success_log = Path(args.success_log)
    if not args.ignore_success_log:
        done = read_success_keys(success_log)
        before = len(items)
        items = [item for item in items if submission_key(item) not in done]
        skipped = before - len(items)
        if skipped:
            print(f"已根据 {success_log} 跳过 {skipped} 条本地成功记录。", flush=True)
    if not items:
        print("没有需要提交的记录。")
        return 0
    async with XxtSubmitter(
        config,
        headless=args.headless,
        slow_mo=args.slow_mo,
        user_data_dir=args.user_data_dir,
        login_timeout=args.login_timeout,
        debug_dir=args.debug_dir,
    ) as submitter:
        if not args.no_web_record_check:
            report = await build_status_report(
                submitter,
                items,
                success_log=success_log if not args.ignore_success_log else None,
            )
            print_status_summary(report)
            items = pending_items_from_report(report)
            if not items:
                print("网页提交记录检查后，没有未提交教案。")
                return 0
        await submitter.open_apply_page()
        for i, item in enumerate(items, 1):
            action = "检查" if args.check_only else "提交"
            print(f"[{i}/{len(items)}] {action} 第{item['week']}周 {item['kind']} {item['date']}")
            await submitter.submit_one(item, check_only=args.check_only)
            if not args.check_only:
                append_success_log(success_log, item)
            if i != len(items) and not args.check_only:
                await submitter.page.get_by_text("继续添加", exact=True).click()
                await submitter.page.wait_for_timeout(2000)
    return 0


async def command_status_async(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"配置文件不存在: {config_path}。请先运行 xxt-jiaoan init-config。", file=sys.stderr)
        return 2
    config = read_json(config_path)
    items = read_json(Path(args.payload))
    async with XxtSubmitter(
        config,
        headless=args.headless,
        slow_mo=args.slow_mo,
        user_data_dir=args.user_data_dir,
        login_timeout=args.login_timeout,
        debug_dir=args.debug_dir,
    ) as submitter:
        report = await build_status_report(
            submitter,
            items,
            success_log=Path(args.success_log) if not args.ignore_success_log else None,
        )
    print_status_summary(report)
    print(f"完整报告已保存: {Path(args.debug_dir) / 'submission_status_report.json'}", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="学习通教案自动解析与提交工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("parse", help="从教案目录解析生成 payload JSON")
    p.add_argument("--root", required=True, help="课程目录或教案目录")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--output", default=str(DEFAULT_PAYLOAD))
    p.add_argument("--min-week", type=int, default=0)
    p.add_argument("--max-week", type=int, default=0)

    p = sub.add_parser("discover", help="搜索课程目录中的教案、授课计划、授课标准")
    p.add_argument("--root", required=True, help="课程目录或任意子目录")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--strict", action="store_true", help="发现结果有警告时返回非零状态码")

    p = sub.add_parser("validate", help="校验 payload JSON 字段和附件路径")
    p.add_argument("--payload", default=str(DEFAULT_PAYLOAD))

    p = sub.add_parser("init-config", help="生成本地配置模板")
    p.add_argument("--output", default=str(DEFAULT_CONFIG), help="配置文件输出路径")
    p.add_argument("--force", action="store_true", help="覆盖已存在的配置文件")

    p = sub.add_parser("submit", help="打开浏览器并按 payload 提交")
    p.add_argument("--payload", default=str(DEFAULT_PAYLOAD))
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--limit", type=int, default=0, help="只提交前 N 条，适合测试")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--slow-mo", type=int, default=50)
    p.add_argument("--user-data-dir", default=str(DEFAULT_BROWSER_PROFILE), help="Playwright 持久化浏览器资料夹，用于保存登录态")
    p.add_argument("--login-timeout", type=int, default=300, help="等待登录和填写页加载的秒数")
    p.add_argument("--debug-dir", default=str(DEFAULT_DEBUG_DIR), help="失败时保存截图和 HTML 的目录")
    p.add_argument("--success-log", default=str(DEFAULT_SUCCESS_LOG), help="成功提交后的本地 JSONL 日志")
    p.add_argument("--ignore-success-log", action="store_true", help="不按本地成功日志跳过，通常只在人工排错时使用")
    p.add_argument("--skip-success-log", dest="ignore_success_log", action="store_false", help=argparse.SUPPRESS)
    p.add_argument("--check-only", action="store_true", help="只填写并检查内容，不点击提交")
    p.add_argument("--no-web-record-check", action="store_true", help="不进入提交记录页检查是否已提交")

    p = sub.add_parser("status", help="进入提交记录页，统计哪些教案已提交/未提交")
    p.add_argument("--payload", default=str(DEFAULT_PAYLOAD))
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--headless", action="store_true")
    p.add_argument("--slow-mo", type=int, default=50)
    p.add_argument("--user-data-dir", default=str(DEFAULT_BROWSER_PROFILE), help="Playwright 持久化浏览器资料夹，用于保存登录态")
    p.add_argument("--login-timeout", type=int, default=300, help="等待登录和记录页加载的秒数")
    p.add_argument("--debug-dir", default=str(DEFAULT_DEBUG_DIR), help="保存状态报告的目录")
    p.add_argument("--success-log", default=str(DEFAULT_SUCCESS_LOG), help="本地成功 JSONL 日志")
    p.add_argument("--ignore-success-log", action="store_true", help="状态统计不参考本地成功日志")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "parse":
        return command_parse(args)
    if args.command == "discover":
        return command_discover(args)
    if args.command == "validate":
        return command_validate(args)
    if args.command == "init-config":
        return command_init_config(args)
    if args.command == "submit":
        return asyncio.run(command_submit_async(args))
    if args.command == "status":
        return asyncio.run(command_status_async(args))
    return 1
