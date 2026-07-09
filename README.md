# 学习通教案自动提交工具

这个工具用于批量提交学习通/超星“教案”表单。核心流程是：

1. 用 `python-docx` 解析教案 `.docx`，并自动读取课程目录下的 `1.授课计划` 对照日期、节次、课次、授课内容。
2. 用 Playwright 打开学习通页面，先进入“提交记录”检查哪些教案已提交、哪些未提交。
3. 只对未提交教案执行：选择课程库、选择班级 radio 并确认、填表、删除续页模板、上传续页、选择审批人、提交前检查、提交。

## 安装

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

如果使用固定的虚拟环境或内置 Python，把命令中的 `python` 替换为对应解释器路径即可。

## 配置

第一次使用先复制配置：

```powershell
Copy-Item config.example.json config.json
```

重点确认：

- `apply_url`：学习通教案填写页地址。
- `record_url`：可选。提交记录页地址；不填时脚本会从 `apply_url` 推导。
- `course_query`：兜底课程库查询关键词。payload 中每条教案有 `course_name` 时优先使用教案里的课程名。
- `approver`：审批人姓名。
- `class_map`：可选班级名称修正表。
- `schedule_file`：可选授课计划文件路径。不填时脚本会自动从课程目录下的 `1.授课计划` 查找。
- `schedule_class_order`：可选。当一个授课计划里有多个班级表格且自动匹配不准时，可显式指定班级顺序。

## 解析教案

```powershell
python xxt_jiaoan_automation.py parse `
  --root "D:\Teaching\CourseName\3.教案" `
  --config config.json `
  --output payload_course.json
```

解析时会自动：

- 读取教案正文、教材、教具、教学目标、重点难点、参考资料、教学反思等字段。
- 匹配同目录下的续页文件，例如 `1_...docx` 对应 `续页-1.docx`。
- 查找课程目录下的授课计划，对照并校准日期、开始节次、结束节次、课次、授课题目。
- 多班级课程会按班级分别匹配授课计划表。

如果缺字段或附件不存在，命令会返回错误并列出问题。

## 校验 Payload

```powershell
python xxt_jiaoan_automation.py validate `
  --payload payload_course.json
```

## 查询提交状态

提交前建议先单独查询一次：

```powershell
python xxt_jiaoan_automation.py status `
  --config config.json `
  --payload payload_course.json `
  --debug-dir debug `
  --login-timeout 180
```

`status` 会进入学习通“提交记录”页面，按课程查询并分页读取记录，然后输出：

- 网页精确已提交：课程、班级、日期、课次都匹配。
- 网页空日期疑似已提交：课程、班级、课次匹配，但网页记录日期为空。
- 本地成功日志：本地 `submit_success.jsonl` 中已有，但网页未精确匹配。
- 未提交：payload 中存在、网页和本地日志都未确认提交。

完整报告保存到：

```text
debug/submission_status_report.json
```

## 只填写并检查

不点击提交，只验证填写流程：

```powershell
python xxt_jiaoan_automation.py submit `
  --config config.json `
  --payload payload_course.json `
  --limit 1 `
  --slow-mo 100 `
  --check-only
```

检查通过会保存：

```text
debug/precheck_passed.json
```

检查失败会保存：

```text
debug/precheck_failed.json
debug/precheck_failed.png
debug/precheck_failed.html
```

## 正式提交

```powershell
python xxt_jiaoan_automation.py submit `
  --config config.json `
  --payload payload_course.json `
  --slow-mo 60 `
  --login-timeout 180 `
  --debug-dir debug
```

正式提交前，脚本会自动先执行提交状态检查，并只提交“未提交”清单中的教案。每条提交成功后会写入：

```text
submit_success.jsonl
```

重跑时默认会先按 `submit_success.jsonl` 跳过本地已成功记录，再进入网页提交记录做状态检查。

如需排错时不参考本地成功日志，可加：

```powershell
--ignore-success-log
```

如需临时跳过网页提交记录检查，可加：

```powershell
--no-web-record-check
```

## 提交前检查内容

每条教案点击提交前，脚本会检查：

- 课程、班级、日期、开始节次、结束节次、课次。
- 理论课时、实训课时、其他课时。
- 教材、教具、授课题目。
- 学情分析、教学目标、重点难点、参考资料、教学反思。
- 附件已删除 `教案续页模版.docx`，并上传对应续页。
- 审批流程包含配置中的审批人。

日期使用页面日期选择器真实点击选择，避免只写入隐藏值导致提交记录日期为空。

教学反思默认允许为空。如果某门课程要求教学反思必填，可在 `config.json` 加入：

```json
{
  "require_reflection": true
}
```

## 空日期记录

如果提交记录中出现“日期为空”的历史记录，脚本会把同课程、同班级、同课次且状态未撤销的记录归类为“网页空日期疑似已提交”，并在提交时跳过，避免重复提交。

如果需要修正这些空日期记录，建议先在学习通中撤回或删除对应记录，再重新运行 `status` 和 `submit`。

## 代码结构

- `xxt_jiaoan_automation.py`：命令行入口。
- `xxt_jiaoan/parser.py`：解析教案和授课计划，生成 payload。
- `xxt_jiaoan/validation.py`：校验 payload 字段和附件路径。
- `xxt_jiaoan/status.py`：进入提交记录页，统计已提交、疑似已提交、未提交。
- `xxt_jiaoan/submitter.py`：Playwright 页面填写、课程库选择、附件上传、审批人选择、提交前检查。
- `xxt_jiaoan/success_log.py`：本地成功提交日志。
- `xxt_jiaoan/cli.py`：命令行参数和流程编排。

## 注意

- 建议使用有界面模式，不建议 `--headless`，登录、文件上传和附件预览在有界面模式更稳。
- 页面结构如果变化，可能需要调整 `submitter.py` 中的选择器。
- 每次正式提交前都会先检查提交状态，这是防重复提交的主保护。
