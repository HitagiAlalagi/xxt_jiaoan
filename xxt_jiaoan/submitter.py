from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .utils import clean_text


class XxtSubmitter:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        headless: bool = False,
        slow_mo: int = 0,
        user_data_dir: str = ".xxt_browser_profile",
        login_timeout: int = 300,
        debug_dir: str = "debug",
    ):
        self.config = config
        self.headless = headless
        self.slow_mo = slow_mo
        self.user_data_dir = user_data_dir
        self.login_timeout = login_timeout
        self.debug_dir = Path(debug_dir)
        self.browser = None
        self.context = None
        self.page = None

    async def __aenter__(self):
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright 未安装。请先运行: python -m pip install -r requirements.txt && python -m playwright install chromium") from exc
        self.pw = await async_playwright().start()
        self.context = await self.pw.chromium.launch_persistent_context(
            self.user_data_dir,
            headless=self.headless,
            slow_mo=self.slow_mo,
            accept_downloads=True,
        )
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.context:
            await self.context.close()
        await self.pw.stop()

    async def open_apply_page(self):
        assert self.page
        await self.page.goto(self.config["apply_url"], wait_until="domcontentloaded")
        print(f"如出现登录页，请在打开的浏览器中登录学习通。脚本会等待填写页加载，最长 {self.login_timeout} 秒。", flush=True)
        await self.page.locator("input.ctat_input_btn:not(.subform-tip)").wait_for(timeout=self.login_timeout * 1000)

    def record_url(self) -> str:
        if self.config.get("record_url"):
            return self.config["record_url"]
        return self.config["apply_url"].replace("/apply.html", "/record.html").replace("&newApply=1", "")

    async def open_record_page(self):
        assert self.page
        await self.page.goto(self.record_url(), wait_until="domcontentloaded")
        print(f"正在打开提交记录页；如出现登录页，请在浏览器中登录学习通，最长等待 {self.login_timeout} 秒。", flush=True)
        await self.page.locator("input.opt_txt_input").first.wait_for(timeout=self.login_timeout * 1000)

    async def query_record_rows(self, course_name: str) -> list[dict[str, str]]:
        page = self.page
        inputs = page.locator("input.opt_txt_input:visible")
        input_count = await inputs.count()
        for i in range(min(input_count, 5)):
            await inputs.nth(i).fill("")
        if input_count > 1:
            await inputs.nth(1).fill(course_name)
        query = page.get_by_text("查询", exact=True)
        await query.click()
        await page.wait_for_timeout(1500)
        rows: list[dict[str, str]] = []
        seen_pages: set[str] = set()
        for _ in range(30):
            page_rows = await page.evaluate(
                """() => {
                const rows = Array.from(document.querySelectorAll('.vxe-table--body-wrapper tbody tr'));
                return rows.map((tr) => {
                    const cells = Array.from(tr.querySelectorAll('td')).map((td) => (td.innerText || td.textContent || '').trim());
                    return {
                        teacher: cells[0] || '',
                        department: cells[1] || '',
                        course_name: (cells[2] || '').replace(/\\*$/, ''),
                        class_select: cells[3] || '',
                        date: cells[7] || '',
                        start_period: cells[8] || '',
                        end_period: cells[9] || '',
                        lesson_no: cells[10] || '',
                        theory_hours: cells[11] || '',
                        practice_hours: cells[12] || '',
                        other_hours: cells[13] || '',
                        textbook: cells[14] || '',
                        teaching_aids: cells[15] || '',
                        topic: cells[16] || '',
                        status: cells[26] || '',
                        current_approver: cells[27] || '',
                    };
                }).filter((row) => row.course_name || row.class_select || row.date || row.lesson_no);
            }"""
            )
            page_key = json.dumps(page_rows, ensure_ascii=False, sort_keys=True)
            if page_key in seen_pages:
                break
            seen_pages.add(page_key)
            rows.extend(page_rows)
            has_next = await page.evaluate(
                """() => {
                    const candidates = Array.from(document.querySelectorAll(
                        '.vxe-pager--next-btn, .btn-next, button[aria-label*="下一"], li[title*="下一页"]'
                    ));
                    const next = candidates.find((el) => {
                        const style = window.getComputedStyle(el);
                        const text = (el.innerText || el.getAttribute('title') || el.getAttribute('aria-label') || '').trim();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && !el.className.includes('disabled')
                            && el.getAttribute('disabled') === null
                            && (text.includes('下一') || el.className.includes('next'));
                    });
                    if (!next) return false;
                    next.click();
                    return true;
                }"""
            )
            if not has_next:
                break
            await page.wait_for_timeout(1000)
        deduped: dict[str, dict[str, str]] = {}
        for row in rows:
            key = "|".join([
                clean_text(row.get("course_name", "")),
                clean_text(row.get("class_select", "")),
                clean_text(row.get("date", "")),
                clean_text(row.get("lesson_no", "")),
                clean_text(row.get("status", "")),
                clean_text(row.get("topic", "")),
            ])
            deduped[key] = row
        return list(deduped.values())

    def record_matches_item(self, record: dict[str, str], item: dict[str, Any]) -> bool:
        course = item.get("course_name") or self.config["course_query"]
        record_date = clean_text(record.get("date", ""))
        return (
            clean_text(record.get("course_name", "").replace("*", "")) == clean_text(course)
            and clean_text(record.get("class_select", "")) == clean_text(item["class_select"])
            and clean_text(record.get("lesson_no", "")) == clean_text(item["lesson_no"])
            and clean_text(record.get("status", "")) != "已撤销"
            and (record_date == clean_text(item["date"]) or record_date == "")
        )

    async def split_unsubmitted_items(self, items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        await self.open_record_page()
        records_by_course: dict[str, list[dict[str, str]]] = {}
        for item in items:
            course = item.get("course_name") or self.config["course_query"]
            if course not in records_by_course:
                records_by_course[course] = await self.query_record_rows(course)

        submitted = []
        unsubmitted = []
        for item in items:
            course = item.get("course_name") or self.config["course_query"]
            matched = next((record for record in records_by_course.get(course, []) if self.record_matches_item(record, item)), None)
            if matched:
                submitted.append({"item": item, "record": matched})
            else:
                unsubmitted.append(item)

        self.debug_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "submitted_count": len(submitted),
            "unsubmitted_count": len(unsubmitted),
            "submitted": [
                {
                    "course_name": row["item"].get("course_name", ""),
                    "class_select": row["item"].get("class_select", ""),
                    "date": row["item"].get("date", ""),
                    "lesson_no": row["item"].get("lesson_no", ""),
                    "status": row["record"].get("status", ""),
                    "topic": row["record"].get("topic", ""),
                }
                for row in submitted
            ],
            "unsubmitted": [
                {
                    "course_name": item.get("course_name", ""),
                    "class_select": item.get("class_select", ""),
                    "date": item.get("date", ""),
                    "lesson_no": item.get("lesson_no", ""),
                    "topic": item.get("topic", ""),
                }
                for item in unsubmitted
            ],
        }
        (self.debug_dir / "submission_record_check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"提交记录检查完成: 已提交 {len(submitted)} 条，未提交 {len(unsubmitted)} 条。", flush=True)
        return unsubmitted, submitted

    async def select_course(self, item: dict[str, Any]):
        page = self.page
        course_query = item.get("course_name") or self.config["course_query"]
        course_section = page.locator("li[id='60']")
        for attempt in range(4):
            await course_section.locator("input.ctat_input_btn:not(.subform-tip)").click()
            search_inputs = course_section.locator("input.opt_txt_input:visible")
            await search_inputs.first.fill(course_query)
            query_button = course_section.get_by_text("查询", exact=True)
            await query_button.click()
            await page.wait_for_timeout(1200)
            candidates: list[str | None] = []
            rows = await course_section.locator(".vxe-table--body-wrapper table tbody tr:visible").all()
            for row in rows:
                if item["class_select"] in await row.inner_text():
                    candidates.append(await row.get_attribute("rowid"))
            if attempt >= len(candidates):
                break
            rowid = candidates[attempt]
            row = course_section.locator(f".vxe-table--body-wrapper table tbody tr[rowid='{rowid}']:visible")
            fixed_radio = course_section.locator(f".vxe-table--fixed-left-wrapper tr[rowid='{rowid}'] .vxe-radio--icon:visible")
            if await fixed_radio.count():
                await fixed_radio.first.click(force=True)
            else:
                row_box = await row.first.bounding_box()
                if not row_box:
                    await self.save_debug("course_radio_not_found")
                    raise RuntimeError(f"课程库中找不到对应 radio: {item['class_select']}")
                await page.mouse.click(row_box["x"] - 31, row_box["y"] + row_box["height"] / 2)
            await page.wait_for_timeout(300)
            confirm_buttons = course_section.get_by_text("确认", exact=True)
            clicked_confirm = False
            for i in range(await confirm_buttons.count()):
                confirm = confirm_buttons.nth(i)
                if await confirm.is_visible():
                    await confirm.click(force=True)
                    clicked_confirm = True
                    break
            if not clicked_confirm:
                await self.save_debug("course_confirm_not_found")
                raise RuntimeError("课程库弹窗中找不到确认按钮")
            await page.wait_for_timeout(1500)
            body = await page.locator("body").inner_text()
            if course_query in body and item["class_select"] in body:
                return
            if not await course_section.locator(".vxe-table--body-wrapper:visible").count():
                await page.wait_for_timeout(300)
        await self.save_debug("course_select_failed")
        raise RuntimeError(f"课程库选择后未回填课程或班级: {course_query} / {item['class_select']}")

    async def set_date(self, date: str):
        page = self.page
        target = datetime.strptime(date, "%Y-%m-%d")
        field = page.locator("xpath=//label[@title='日期']/ancestor::li[1]")
        await field.scroll_into_view_if_needed()
        await field.locator(".el-date-editor, .custom-date-picker, .el-input__wrapper").last.click(force=True)
        panel = page.locator(".el-picker-panel:visible").last
        await panel.wait_for(timeout=5000)

        for _ in range(48):
            labels = await panel.locator(".el-date-picker__header-label").all_inner_texts()
            if len(labels) < 2:
                raise RuntimeError("日期选择器没有显示年月")
            shown_year = int(re.search(r"\d+", labels[0]).group(0))
            shown_month = int(re.search(r"\d+", labels[1]).group(0))
            delta = (target.year - shown_year) * 12 + (target.month - shown_month)
            if delta == 0:
                break
            button = ".arrow-right" if delta > 0 else ".arrow-left"
            await panel.locator(button).click()
            await page.wait_for_timeout(150)
        else:
            raise RuntimeError(f"日期选择器无法切换到目标月份: {date}")

        days = panel.locator("td.available:not(.prev-month):not(.next-month) .el-date-table-cell__text")
        for i in range(await days.count()):
            day = days.nth(i)
            if (await day.inner_text()).strip() == str(target.day):
                await day.click(force=True)
                await page.wait_for_timeout(500)
                actual = await self.date_field_value()
                if date not in actual:
                    raise RuntimeError(f"日期选择后没有回填到页面: expected={date}, actual={actual}")
                return
        raise RuntimeError(f"日期选择器中找不到目标日期: {date}")
        await page.wait_for_timeout(500)

    async def fill_input(self, index: int, value: str):
        loc = self.page.locator("input").nth(index)
        await loc.scroll_into_view_if_needed()
        await loc.click()
        await loc.press("Control+A")
        await loc.fill(value or "")

    async def fill_input_by_keyboard(self, index: int, value: str):
        loc = self.page.locator("input").nth(index)
        await loc.scroll_into_view_if_needed()
        await loc.click()
        await loc.press("Control+A")
        await loc.press("Backspace")
        if value:
            await loc.type(value)
        await loc.press("Tab")

    async def set_input_by_index(self, index: int, value: str):
        loc = self.page.locator("input").nth(index)
        await loc.scroll_into_view_if_needed()
        await loc.evaluate(
            """(el, value) => {
                el.value = value || '';
                el.setAttribute('title', value || '');
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
            }""",
            value or "",
        )

    async def set_labeled_input(self, label: str, value: str):
        loc = self.page.locator(f"xpath=//label[@title='{label}']/ancestor::li[1]//input").last
        await loc.scroll_into_view_if_needed()
        await loc.evaluate(
            """(el, value) => {
                el.value = value || '';
                el.setAttribute('title', value || '');
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
            }""",
            value or "",
        )

    async def labeled_input_value(self, label: str) -> str:
        loc = self.page.locator(f"xpath=//label[@title='{label}']/ancestor::li[1]//input").last
        return await loc.evaluate("(el) => el.value || el.getAttribute('title') || ''")

    async def date_field_value(self) -> str:
        field = self.page.locator("xpath=//label[@title='日期']/ancestor::li[1]")
        return await field.evaluate(
            """(el) => {
                const picker = el.querySelector('.custom-date-picker');
                const visible = el.innerText || '';
                const values = Array.from(el.querySelectorAll('input')).map((input) => input.value || input.getAttribute('title') || '');
                return [picker?.getAttribute('title') || '', visible, ...values].join(' ').trim();
            }"""
        )

    async def fill_editor(self, frame_selector: str, value: str):
        frame = self.page.frame_locator(frame_selector)
        body = frame.locator("body")
        await self.page.locator(frame_selector).scroll_into_view_if_needed()
        await body.click()
        await body.press("Control+A")
        await body.press("Backspace")
        await body.fill(value or "")

    async def fill_form(self, item: dict[str, Any]):
        await self.set_labeled_input("课程类型", item["course_type"])
        await self.set_date(item["date"])
        for label, key in [
            ("开始节次", "start_period"),
            ("结束节次", "end_period"),
            ("课次", "lesson_no"),
            ("教材", "textbook"),
            ("教具", "teaching_aids"),
            ("授课题目", "topic"),
        ]:
            await self.set_labeled_input(label, item.get(key, ""))
        await self.fill_input_by_keyboard(20, item.get("topic", ""))
        for idx, key in [
            (13, "theory_hours"),
            (15, "practice_hours"),
            (17, "other_hours"),
        ]:
            await self.fill_input_by_keyboard(idx, item.get(key, ""))
        editors = ["analysis", "objectives", "key_points", "references", "reflection"]
        for i, key in enumerate(editors):
            await self.fill_editor(f"#ueditor_{i}", item[key])

    async def upload_continuation(self, item: dict[str, Any]):
        page = self.page
        await self.close_attachment_preview()
        section = page.locator(".fsw-ul-item-fileupload")
        await section.scroll_into_view_if_needed()
        text = await section.inner_text()
        if "教案续页模版.docx" in text:
            row = section.locator(".gmulfile", has_text="教案续页模版.docx").first
            box = await row.bounding_box()
            if not box:
                raise RuntimeError("找不到模板附件行")
            await row.scroll_into_view_if_needed()
            await row.hover(force=True)
            await page.wait_for_timeout(500)
            delete_buttons = row.locator(".file-del-btn")
            clicked = False
            for i in range(await delete_buttons.count()):
                delete_button = delete_buttons.nth(i)
                try:
                    await delete_button.click(timeout=1500, force=True)
                    clicked = True
                    break
                except Exception:
                    continue
            if not clicked:
                box = await row.bounding_box()
                if not box:
                    await self.save_debug("template_delete_failed")
                    raise RuntimeError("找不到模板附件行")
                await page.mouse.click(box["x"] + box["width"] - 24, box["y"] + box["height"] / 2)
            await page.wait_for_timeout(500)
            await self.close_attachment_preview()
            await self.confirm_delete_if_needed()
            await page.wait_for_timeout(1000)
            if "教案续页模版.docx" in await section.inner_text():
                await self.save_debug("template_delete_failed")
                raise RuntimeError("模板附件删除失败")
        if Path(item["continuation_file"]).name not in await section.inner_text():
            await section.locator(".btn-upload-file").click()
            await page.wait_for_timeout(500)
            async with page.expect_file_chooser() as fc_info:
                await section.locator(".upload-warp-pop input[type=file]").click(force=True)
            chooser = await fc_info.value
            await chooser.set_files(item["continuation_file"])
            await page.wait_for_timeout(2500)

    async def close_attachment_preview(self):
        close_buttons = self.page.locator(".icon-close, .close, [class*='close']", has_text=re.compile(r"^$|关闭|×"))
        for i in range(await close_buttons.count()):
            close = close_buttons.nth(i)
            try:
                if await close.is_visible(timeout=300):
                    await close.click(timeout=1000, force=True)
                    await self.page.wait_for_timeout(500)
                    return
            except Exception:
                continue
        try:
            await self.page.keyboard.press("Escape")
            await self.page.wait_for_timeout(300)
        except Exception:
            pass

    async def confirm_delete_if_needed(self):
        dialog = self.page.locator(".confirmWindow, .form_open_pop, .model-popup, .common-pop").filter(has_text="确认删除")
        if not await dialog.count():
            return
        for i in range(await dialog.count()):
            pop = dialog.nth(i)
            try:
                if not await pop.is_visible(timeout=300):
                    continue
                delete_button = pop.locator(".confirm, .btn-blue, a, button, span", has_text=re.compile(r"^删除$"))
                for j in range(await delete_button.count()):
                    button = delete_button.nth(j)
                    if await button.is_visible(timeout=300):
                        await button.click(timeout=1500, force=True)
                        await self.page.wait_for_timeout(800)
                        return
            except Exception:
                continue

    async def select_approver(self):
        page = self.page
        flow = page.locator(".approve_flow")
        if self.config["approver"] in await flow.inner_text():
            return
        if "展开" in await flow.inner_text():
            await flow.get_by_text("展开", exact=True).click()
        await flow.locator(".g_addLead_new").click()
        await page.wait_for_timeout(1000)
        pop = page.locator(".common-pop.organize-user__pop")
        search = pop.locator(".search-input__content")
        if await search.count():
            await search.fill(self.config["approver"])
            await page.wait_for_timeout(800)
        approver = self.config["approver"]
        rows = pop.locator(".organize-user__info", has_text=approver)
        names = pop.locator(".organize-user__info-name", has_text=approver)
        radios = pop.locator(f"input[type=radio][aria-label='{approver}']")
        clicked = False
        for candidates in (rows, names, radios):
            for i in range(await candidates.count()):
                candidate = candidates.nth(i)
                try:
                    await candidate.scroll_into_view_if_needed(timeout=2000)
                    await candidate.click(timeout=2000, force=True)
                    clicked = True
                    break
                except Exception:
                    continue
            if clicked:
                break
        if not clicked:
            box = await rows.first.bounding_box() if await rows.count() else None
            if box:
                await page.mouse.click(box["x"] + box["width"] - 18, box["y"] + box["height"] / 2)
                clicked = True
        if not clicked:
            await self.save_debug("approver_not_visible")
            print(await pop.inner_text(), file=sys.stderr)
            raise RuntimeError(f"审批人列表中没有可点击的 {approver}")
        await page.wait_for_timeout(500)
        ok_buttons = pop.locator(".pop-btn-blue, .pop-ok, button, span", has_text=re.compile(r"^确定(?:\\(1\\))?$"))
        for i in range(await ok_buttons.count()):
            ok = ok_buttons.nth(i)
            try:
                await ok.scroll_into_view_if_needed(timeout=1000)
                await ok.click(timeout=2000, force=True)
                break
            except Exception:
                continue
        else:
            pop_box = await pop.bounding_box()
            if not pop_box:
                raise RuntimeError("审批人弹窗中没有可点击的确定按钮")
            await page.mouse.click(pop_box["x"] + pop_box["width"] / 2, pop_box["y"] + pop_box["height"] - 38)
        await page.wait_for_timeout(1200)

    async def save_debug(self, name: str):
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", name)
        await self.page.screenshot(path=str(self.debug_dir / f"{safe}.png"), full_page=True)
        (self.debug_dir / f"{safe}.html").write_text(await self.page.content(), encoding="utf-8")

    async def precheck(self, item: dict[str, Any]):
        page = self.page
        inputs = await page.locator("input").evaluate_all("(els) => els.map(e => e.value || '')")
        body = await page.locator("body").inner_text()
        attach = await page.locator(".fsw-ul-item-fileupload").inner_text()
        flow = await page.locator(".approve_flow").inner_text()
        course = item.get("course_name") or self.config["course_query"]
        continuation_name = Path(item["continuation_file"]).name
        actual_date = await self.date_field_value()
        report = {
            "course": {"expected": course, "actual": course if course in body else "", "ok": course in body},
            "class": {"expected": item["class_select"], "actual": item["class_select"] if item["class_select"] in body else "", "ok": item["class_select"] in body},
            "date": {"expected": item["date"], "actual": actual_date, "ok": item["date"] in actual_date},
            "start_period": {"expected": item["start_period"], "actual": await self.labeled_input_value("开始节次"), "ok": await self.labeled_input_value("开始节次") == item["start_period"]},
            "end_period": {"expected": item["end_period"], "actual": await self.labeled_input_value("结束节次"), "ok": await self.labeled_input_value("结束节次") == item["end_period"]},
            "lesson": {"expected": item["lesson_no"], "actual": await self.labeled_input_value("课次"), "ok": await self.labeled_input_value("课次") == item["lesson_no"]},
            "theory_hours": {"expected": item["theory_hours"], "actual": inputs[13], "ok": inputs[13] == item["theory_hours"]},
            "practice_hours": {"expected": item["practice_hours"], "actual": inputs[15], "ok": inputs[15] == item["practice_hours"]},
            "other_hours": {"expected": item.get("other_hours", ""), "actual": inputs[17], "ok": inputs[17] == item.get("other_hours", "")},
            "textbook": {"expected": item["textbook"], "actual": await self.labeled_input_value("教材"), "ok": await self.labeled_input_value("教材") == item["textbook"]},
            "teaching_aids": {"expected": item["teaching_aids"], "actual": await self.labeled_input_value("教具"), "ok": await self.labeled_input_value("教具") == item["teaching_aids"]},
            "topic": {
                "expected": item["topic"],
                "actual": await self.labeled_input_value("授课题目") or (inputs[20] if len(inputs) > 20 else ""),
                "ok": (await self.labeled_input_value("授课题目") == item["topic"]) or (len(inputs) > 20 and inputs[20] == item["topic"]),
            },
            "attachment": {
                "expected": continuation_name,
                "actual": clean_text(attach),
                "ok": continuation_name in attach and "教案续页模版.docx" not in attach,
            },
            "approver": {"expected": self.config["approver"], "actual": clean_text(flow), "ok": self.config["approver"] in flow},
        }
        required_editors = ["analysis", "objectives", "key_points", "references"]
        if item.get("reflection") or self.config.get("require_reflection", False):
            required_editors.append("reflection")
        for i, key in enumerate(["analysis", "objectives", "key_points", "references", "reflection"]):
            text = clean_text(await page.frame_locator(f"#ueditor_{i}").locator("body").inner_text())
            expected = clean_text(item[key])
            report[key] = {"expected": expected, "actual": text, "ok": text == expected}
            if key in required_editors:
                report[f"{key}_nonempty"] = {"expected": "非空", "actual": "非空" if text else "空", "ok": bool(text)}
        failed = [k for k, row in report.items() if not row["ok"]]
        summary = {
            "course": course,
            "class": item["class_select"],
            "date": item["date"],
            "lesson_no": item["lesson_no"],
            "topic": item["topic"],
            "textbook": item["textbook"],
            "teaching_aids": item["teaching_aids"],
            "continuation_file": continuation_name,
            "approver": self.config["approver"],
        }
        if failed:
            await self.save_debug("precheck_failed")
            debug = {
                "failed": failed,
                "summary": summary,
                "checks": report,
                "input_count": len(inputs),
                "inputs": inputs[:25],
                "flow": flow,
                "attachment": attach,
                "body_excerpt": body[:2000],
            }
            (self.debug_dir / "precheck_failed.json").write_text(json.dumps(debug, ensure_ascii=False, indent=2), encoding="utf-8")
            raise RuntimeError(f"提交前检查失败: {failed}")
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        (self.debug_dir / "precheck_passed.json").write_text(
            json.dumps({"summary": summary, "checks": report}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            f"提交前检查通过: {course} / {item['class_select']} / {item['date']} / 第{item['lesson_no']}次课 / {item['topic']}",
            flush=True,
        )
        return report

    async def submit_one(self, item: dict[str, Any], *, check_only: bool = False):
        await self.select_course(item)
        await self.fill_form(item)
        await self.upload_continuation(item)
        await self.select_approver()
        await self.precheck(item)
        if check_only:
            print("已完成填写和内容检查；--check-only 模式不会点击提交。", flush=True)
            return
        await self.page.locator(".appyl_btm_submit").click()
        await self.page.wait_for_timeout(3000)
        if "提交成功" not in await self.page.locator("body").inner_text():
            raise RuntimeError("提交后未看到提交成功")
