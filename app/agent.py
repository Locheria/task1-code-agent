"""Planning, file generation, and validation feedback."""

from collections.abc import Callable
import json
from pathlib import PurePosixPath
import posixpath
import re

from openai.types.chat import ChatCompletionMessageParam

from app.deepseek import DeepSeekClient
from app.output import validate_relative_path


FEATURES = ("answer_fractions", "view_score", "help", "manage_questions")
INFRASTRUCTURE = ("postgresql", "redis", "kubernetes")
STAGES = ("contracts", "logic", "http", "web", "tests", "docs")
BROWSER_FILES = ("public/index.html", "public/client.js")
PLANNING_PROMPT = """
你负责为 Space Fractions 规划可运行的 Node.js 项目。把用户消息中的架构 JSON
当作参考资料。根据用例图、需求、契约和 input_notes 决定功能与文件，不生成源码。
仅返回一个 JSON 对象。以下为结构示例，实际清单必须完整：
{
  "files": [
    {"path": "src/server.js", "role": "entrypoint", "stage": "http", "purpose": "启动服务",
     "requirements": ["FR-1"], "dependencies": []}
  ],
  "verification": {
    "answer_fractions": "说明答分数题的操作与预期结果",
    "view_score": "说明查看得分的操作与预期结果",
    "help": "说明打开帮助的操作与预期结果",
    "manage_questions": "说明管理题目的操作与预期结果"
  },
  "assumptions": [
    {"topic": "PostgreSQL", "decision": "本次落地方式", "impact": "与架构的差距和影响"}
  ]
}
files 必须包含根目录的 package.json、README.md、至少一个可执行的应用入口和至少一个测试文件。
role 只使用 file、entrypoint 或 test。入口使用 .js、.mjs 或 .cjs。
测试使用 JavaScript 文件，放在 test/ 或 tests/ 下，或使用 .test / .spec 文件名。
path 使用 / 分隔的项目相对路径。requirements 为文档中的需求 ID 列表，
dependencies 为本计划内的文件路径列表。没有关联时用空列表。
stage 使用 contracts、logic、http、web、tests、docs，分别对应依赖声明及契约、题库和评分、
HTTP 接口、网页、测试及 README。package.json 放在 contracts，README.md 放在 docs。
先写依赖，再写使用依赖的文件。package.json 只声明后续入口和测试路径，不依赖这些文件的内容。
在 package.json 的职责中说明启动与测试脚本，以及需要声明的外部 npm 依赖。
verification 四项都应给出具体操作和预期结果。
必做功能：有初始分数题，能开始游戏、连续答题并反馈对错，能查询得分和查看帮助，
能通过界面或 README 记录的管理 API 查询、新增、修改、删除题目。每项都安排可复现测试。
浏览器页面是必做功能。计划必须包含 public/index.html 和 public/client.js，stage 为 web。
启动后 GET / 必须返回游戏 HTML，不能只提供 JSON API。首页提供开始游戏、选答或输入答案、
对错反馈、下一题、当前得分和帮助，客户端使用后端实际支持的方法、路径与字段。
在 verification 中写明如何从浏览器验收，并安排 HTTP 测试断言 GET / 返回 200 和 text/html。
必做验收：npm install、npm test、npm start 可在项目根目录运行。计划须明确包依赖、
模块导出、HTTP 方法、路径和请求响应字段，并安排 HTTP 集成测试覆盖关键用户流程。
如 OpenAPI、用例图和文字存在差异，在 assumptions 记下来源和最终采用的约定；
已有 API 字段应保留，新增字段应在计划与测试中说明。功能和接口完整性不得作为简化项。
允许简化的基础设施：可用带初始题目的内存题库代替 PostgreSQL，省略 Redis、
RabbitMQ、Kubernetes，使用单进程。assumptions 逐项记录 PostgreSQL、Redis 和
Kubernetes 的决定与影响，包含对持久化、跨进程共享、部署能力的影响。
如采用简化，README.md 的职责须写明与原架构的差距和实际影响。
"""


class PlanError(ValueError):
    """A planning error suitable for feedback to the model and the UI."""


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _text_list(value: object) -> bool:
    return isinstance(value, list) and all(_text(item) for item in value)


def validate_plan(plan: object) -> list[str]:
    if not isinstance(plan, dict):
        return ["根节点必须是 JSON 对象。"]

    errors = []
    files = plan.get("files")
    paths = set()
    normalized_paths = set()
    dependencies = []
    has_entrypoint = has_test = False
    if not isinstance(files, list) or not files:
        errors.append("files 必须是非空数组。")
    else:
        for index, file in enumerate(files):
            field = f"files[{index}]"
            if not isinstance(file, dict):
                errors.append(f"{field} 必须是对象。")
                continue
            path = file.get("path")
            if _text(path):
                try:
                    validate_relative_path(path)
                except ValueError as error:
                    errors.append(f"{field}.path：{error}")
                else:
                    normalized = path.casefold()
                    if normalized in normalized_paths:
                        errors.append(f"{field}.path 与前面的路径重复（Windows 不区分大小写）。")
                    normalized_paths.add(normalized)
                    paths.add(path)
            else:
                errors.append(f"{field}.path 必须是非空字符串。")

            role = file.get("role")
            if "stage" in file and file["stage"] not in STAGES:
                errors.append(f"{field}.stage 必须是 {', '.join(STAGES)} 之一。")
            if role not in ("file", "entrypoint", "test"):
                errors.append(f"{field}.role 必须是 file、entrypoint 或 test。")
            elif role in ("entrypoint", "test") and _text(path):
                is_javascript = path.lower().endswith((".js", ".mjs", ".cjs"))
                if role == "entrypoint":
                    has_entrypoint |= is_javascript
                else:
                    has_test |= is_javascript and (
                        path.startswith(("test/", "tests/"))
                        or re.search(r"\.(?:test|spec)\.[cm]?js$", path) is not None
                    )
            if not _text(file.get("purpose")):
                errors.append(f"{field}.purpose 必须是非空字符串。")
            if not _text_list(file.get("requirements")):
                errors.append(f"{field}.requirements 必须是字符串数组。")
            if _text_list(file.get("dependencies")):
                dependencies.extend((field, dependency) for dependency in file["dependencies"])
            else:
                errors.append(f"{field}.dependencies 必须是路径字符串数组。")

        for path in ("package.json", "README.md", *BROWSER_FILES):
            if path not in paths:
                errors.append(f"files 缺少必需文件 {path}。")
        if not has_entrypoint:
            errors.append("files 缺少 role=entrypoint 的 JavaScript 应用入口。")
        if not has_test:
            errors.append("files 缺少 role=test 的 JavaScript 测试文件。")
        if any(parent in normalized_paths for path in normalized_paths
               for parent in _parent_paths(path)):
            errors.append("files 中有同一路径同时用作文件和目录。")
        for field, dependency in dependencies:
            if dependency not in paths:
                errors.append(f"{field}.dependencies 引用了计划外的路径 {dependency!r}。")
        if not errors:
            try:
                list(_generation_batches(files))
            except PlanError as error:
                errors.append(str(error))

    verification = plan.get("verification")
    if not isinstance(verification, dict):
        errors.append("verification 必须是对象。")
    else:
        for feature in FEATURES:
            if not _text(verification.get(feature)):
                errors.append(f"verification.{feature} 必须提供验证步骤和预期结果。")

    assumptions = plan.get("assumptions")
    topics = set()
    if not isinstance(assumptions, list):
        errors.append("assumptions 必须是对象数组。")
    else:
        for index, assumption in enumerate(assumptions):
            field = f"assumptions[{index}]"
            if not isinstance(assumption, dict):
                errors.append(f"{field} 必须是对象。")
                continue
            for key in ("topic", "decision", "impact"):
                if not _text(assumption.get(key)):
                    errors.append(f"{field}.{key} 必须是非空字符串。")
            if _text(assumption.get("topic")):
                topics.add(assumption["topic"].strip().casefold())
        for topic in INFRASTRUCTURE:
            if topic not in topics:
                errors.append(f"assumptions 缺少 {topic} 的落地决定和影响。")
    return errors


def _parent_paths(path: str):
    while "/" in path:
        path = path.rsplit("/", 1)[0]
        yield path


def _read_plan(response) -> dict:
    if not response.choices:
        raise PlanError("模型未返回候选结果。")
    choice = response.choices[0]
    if choice.finish_reason != "stop":
        raise PlanError(f"规划未完整结束（finish_reason={choice.finish_reason}）。请缩短计划描述。")
    content = choice.message.content
    if not content or not content.strip():
        raise PlanError("模型返回空白内容，请输出完整 JSON 对象。")
    try:
        plan = json.loads(content)
    except json.JSONDecodeError as error:
        raise PlanError(f"JSON 第 {error.lineno} 行第 {error.colno} 列无法解析。") from None
    errors = validate_plan(plan)
    if errors:
        raise PlanError("\n".join(errors))
    return plan


def plan_project(client: DeepSeekClient, architecture: dict, *, max_revisions: int = 2) -> dict:
    """Return a validated plan, or report the last validation failure."""
    if max_revisions < 0:
        raise ValueError("max_revisions 必须非负。")
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": PLANNING_PROMPT},
        {"role": "user", "content": json.dumps(architecture, ensure_ascii=False)},
    ]
    for attempt in range(max_revisions + 1):
        response = client.complete(messages, json_output=True)
        try:
            return _read_plan(response)
        except PlanError as error:
            if attempt == max_revisions:
                raise PlanError(f"规划在 {attempt + 1} 次尝试后仍未通过校验。\n{error}") from None
            if response.choices and response.choices[0].message.content:
                messages.append({"role": "assistant", "content": response.choices[0].message.content})
            messages.append({
                "role": "user",
                "content": f"上一次规划有以下问题，请修正并重新返回完整 JSON 对象。\n{error}",
            })
    raise AssertionError("规划循环未返回结果")


def _stage(file: dict) -> str:
    if file["path"] == "package.json":
        return "contracts"
    if file["path"] == "README.md":
        return "docs"
    if file["role"] == "test":
        return "tests"
    if file["path"] in BROWSER_FILES:
        return "web"
    return file.get("stage", "http")


def _generation_batches(files: list[dict]):
    pending = {file["path"]: file for file in files}
    completed = set()
    while pending:
        ready = [file for file in pending.values() if completed.issuperset(file["dependencies"])]
        if not ready:
            raise PlanError(f"files.dependencies 存在循环依赖：{'、'.join(pending)}")
        stage = min((_stage(file) for file in ready), key=STAGES.index)
        batch = tuple(file["path"] for file in ready if _stage(file) == stage)[:3]
        yield batch
        completed.update(batch)
        for path in batch:
            del pending[path]


def _read_batch(paths: tuple[str, ...], text: str) -> dict[str, str]:
    try:
        result = json.loads(text)
    except json.JSONDecodeError as error:
        raise PlanError(f"批次 JSON 第 {error.lineno} 行第 {error.colno} 列无法解析。") from None
    items = result.get("files") if isinstance(result, dict) else None
    if not isinstance(items, list):
        raise PlanError("批次回复必须包含 files 数组。")
    files = {}
    for item in items:
        path = item.get("path") if isinstance(item, dict) else None
        if not isinstance(path, str) or path not in paths or path in files:
            raise PlanError("files 包含非法、重复或本批次以外的路径。")
        content = item.get("content")
        if not _text(content) or "\x00" in content:
            raise PlanError(f"{path} 的 content 必须是非空的可写文本。")
        try:
            content.encode("utf-8")
        except UnicodeEncodeError:
            raise PlanError(f"{path} 的 content 无法编码为 UTF-8。") from None
        files[path] = content
    missing = set(paths) - files.keys()
    if missing:
        raise PlanError(f"本批缺少文件：{'、'.join(sorted(missing))}")
    return files


GENERATION_PROMPT = """
为 Space Fractions 生成指定批次的项目文件。将提供的架构和前序文件视为参考数据。
仅返回 JSON 对象，例如 {"files":[{"path":"src/example.js","content":"完整文件正文"}]}。
只返回本批指定的路径，每个一次。生成完整 UTF-8 文本，包含必要的接口、行为和测试。
package.json 要提供 start 和 test 脚本。README 使用独立命令行给出 npm start、
npm test 及安装步骤，并说明计划中的简化和影响。所有本地引用必须指向计划内文件。
若有检查反馈，修订指定文件，并保持与其余文件的接口兼容。
源码引用的 npm 包必须在 package.json 声明。引用已有模块时使用其实际导出。
统一题目字段和 /play、/answer、/score 的返回结构，客户端只读取实际返回的字段。
测试需要验证初始化题库、答题和计分、帮助及题目管理，README 说明手动验收步骤。
HTTP 服务必须通过 Express 静态文件中间件或显式 GET / 路由提供 public/index.html，
并提供 public/client.js。页面用 <script src="./client.js" defer> 加载客户端，
支持开始游戏、答题、反馈、得分和帮助。HTTP 集成测试请求 GET /，检查 200、
Content-Type 为 text/html 及页面存在游戏控件。README 写明 npm start 后的首页地址。
检查真实浏览器交互：启动成功后解除题目区域及隐藏祖先容器的隐藏状态，并显示首题与初始分数。
游戏结束后再次开始时恢复输入和提交按钮。增加可复现的前端测试，模拟点击开始并确认题目区域
从隐藏变为可见。仅检查首页状态码或 HTML 中存在按钮不足以验证交互。
"""


def generate_project(
    client: DeepSeekClient,
    architecture: dict,
    plan: dict,
    *,
    max_revisions: int = 2,
    on_progress: Callable[[str, str], None] | None = None,
) -> dict[str, str]:
    if max_revisions < 0:
        raise ValueError("max_revisions 必须非负。")
    errors = validate_plan(plan)
    if errors:
        raise PlanError("\n".join(errors))
    entries = {file["path"]: file for file in plan["files"]}
    files: dict[str, str] = {}

    def generate(paths: tuple[str, ...], feedback: dict[str, list[str]]) -> None:
        def build_messages(batch: tuple[str, ...]) -> list[ChatCompletionMessageParam]:
            entries_in_batch = [entries[path] for path in batch]
            ids = {item for entry in entries_in_batch for item in entry["requirements"]}
            rows = [row for row in architecture["requirements"] if ids.intersection(row["ids"])]
            section_ids = {"A", "B", "C", "D", "K"}
            if any(_stage(entry) in ("contracts", "docs") for entry in entries_in_batch):
                section_ids.update(("E", "L"))
            if any(_stage(entry) == "tests" for entry in entries_in_batch):
                section_ids.add("H")
            context_paths = {path for entry in entries_in_batch for path in entry["dependencies"]}
            if any(_stage(entry) in ("web", "tests") for entry in entries_in_batch):
                context_paths.update(
                    path for path in files if _stage(entries[path]) in ("contracts", "http")
                )
            if batch != ("package.json",):
                context_paths.add("package.json")
            for path in batch:
                for problem in feedback.get(path, []):
                    context_paths.update(other for other in files if other in problem)
            payload = {
                "plan": plan,
                "requested_paths": batch,
                "sections": [s for s in architecture["sections"] if s["id"] in section_ids],
                "requirements": rows,
                "contracts": architecture["contracts"],
                "diagrams": [
                    diagram for diagram in architecture["diagrams"]
                    if diagram["name"] == "UseCaseDiagram"
                    or (diagram["name"] and any(diagram["name"] in row["diagrams"] for row in rows))
                ],
                "input_notes": architecture["input_notes"],
                "previous_files": {path: files[path] for path in sorted(context_paths) if path in files},
                "current_files": {path: files[path] for path in batch if path in files},
                "feedback": {path: feedback[path] for path in batch if path in feedback},
            }
            return [
                {"role": "system", "content": GENERATION_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ]

        def validate(batch: tuple[str, ...], text: str) -> None:
            _read_batch(batch, text)

        for batch, text in client.generate_batches(paths, build_messages, validate=validate):
            files.update(_read_batch(batch, text))

    batches = list(_generation_batches(plan["files"]))
    if on_progress:
        on_progress("生成文件", f"正在按依赖生成 {len(entries)} 个文件。")
    for batch in batches:
        generate(batch, {})

    for revision in range(max_revisions + 1):
        if on_progress:
            on_progress("检查", "正在检查生成文件。" if revision == 0 else f"正在复查第 {revision} 轮修订。")
        feedback = check_project(plan, files)
        if not feedback:
            return files
        if revision == max_revisions:
            details = "\n".join(f"{path}：{'; '.join(issues)}" for path, issues in feedback.items())
            raise PlanError(f"项目校验失败，已修订 {revision} 轮。\n{details}")
        if on_progress:
            on_progress("生成文件", f"检查发现 {len(feedback)} 个文件需要修订，正在进行第 {revision + 1} 轮修订。")
        for batch in batches:
            affected = tuple(path for path in batch if path in feedback)
            if affected:
                generate(affected, feedback)
    raise AssertionError("项目检查循环未返回结果")


JS_REFERENCE = re.compile(
    r"""\b(?:from\s+|import\s*(?:\(\s*)?|require\s*\(\s*)["']([^"'\r\n]+)["']"""
)
NAMED_REQUIRE = re.compile(r"""\b(?:const|let|var)\s*\{([^{}]+)\}\s*=\s*require\(\s*["'](\.{1,2}/[^"']+)["']""")
OBJECT_EXPORT = re.compile(r"\bmodule\.exports\s*=\s*\{\s*([\w,\s]+)\s*\}\s*;?\s*$")
NODE_BUILTINS = set("""
assert async_hooks buffer child_process cluster console constants crypto dgram diagnostics_channel
dns domain events fs http http2 https module net os path perf_hooks process punycode querystring
readline repl stream string_decoder sys timers tls trace_events tty url util v8 vm wasi
worker_threads zlib
""".split())
ASSET_REFERENCE = re.compile(r"""\b(?:src|href)\s*=\s*["'](\.{1,2}/[^"'\r\n]+)["']""", re.I)
README_COMMAND = re.compile(r"(?m)^\s*(?:[$>]\s*)?(npm\s+(?:run\s+)?)([\w:.-]+)\b")


def check_project(plan: dict, files: dict[str, str]) -> dict[str, list[str]]:
    errors: dict[str, list[str]] = {}
    planned = {item["path"] for item in plan["files"]}

    def report(path: str, message: str) -> None:
        errors.setdefault(path, []).append(message)

    def check_target(path: str, reference: str, *, module: bool = False) -> str | None:
        target = reference.split("?", 1)[0].split("#", 1)[0]
        target = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
        candidates = [target]
        if module and not PurePosixPath(target).suffix:
            for extension in (".js", ".mjs", ".cjs", ".json"):
                candidates.extend((target + extension, f"{target}/index{extension}"))
        for candidate in candidates:
            if candidate in files:
                return candidate
        report(path, f"本地引用 {reference!r} 的目标不存在（解析路径 {target!r}）。")
        return None

    for path in sorted(planned - files.keys()):
        report(path, "生成清单缺少此文件。")
    if "public/index.html" in files:
        page = files["public/index.html"]
        if not re.search(r"<html\b", page, re.I):
            report("public/index.html", "浏览器首页须提供完整的 HTML 文档。")
        if not re.search(r"""<script\b[^>]*\bsrc\s*=\s*["'](?:\./|/)?client\.js["']""", page, re.I):
            report("public/index.html", "浏览器首页须加载 public/client.js。")
        server_files = {
            path: files[path] for item in plan["files"]
            if (path := item["path"]) in files and item.get("role") != "test"
            and not path.startswith("public/")
            and PurePosixPath(path).suffix.lower() in (".js", ".mjs", ".cjs")
        }
        has_home_handler = any(
            re.search(r"""\.get\s*\(\s*["']/["']""", content)
            or ("express.static" in content and re.search(r"""["'](?:\./)?public["']""", content))
            for content in server_files.values()
        )
        if not has_home_handler:
            entry = next((item["path"] for item in plan["files"] if item.get("role") == "entrypoint"), None)
            server = next((path for path, content in server_files.items()
                           if re.search(r"""["']express["']""", content)), entry)
            if server:
                report(server, "缺少首页服务。请提供 GET / 并加载 public/index.html 与 public/client.js。")

    scripts = None
    declared_packages = None
    if "package.json" in files:
        try:
            package = json.loads(files["package.json"])
        except json.JSONDecodeError as error:
            report("package.json", f"第 {error.lineno} 行第 {error.colno} 列不是有效 JSON。")
        else:
            if not isinstance(package, dict):
                report("package.json", "根节点必须是 JSON 对象。")
            else:
                declared_packages = set()
                for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
                    packages = package.get(field, {})
                    if not isinstance(packages, dict) or not all(_text(value) for value in packages.values()):
                        report("package.json", f"{field} 必须是包名到版本字符串的映射。")
                    else:
                        declared_packages.update(packages)
                for field in ("main", "module"):
                    if field in package:
                        if _text(package[field]):
                            check_target("package.json", package[field], module=True)
                        else:
                            report("package.json", f"{field} 必须是非空路径字符串。")
                scripts = package.get("scripts")
                if not isinstance(scripts, dict):
                    report("package.json", "scripts 必须是对象，且包含 start 和 test。")
                    scripts = None
                else:
                    for name in ("start", "test"):
                        if not _text(scripts.get(name)):
                            report("package.json", f"scripts.{name} 必须是非空命令。")
                    for name, command in scripts.items():
                        if not _text(command):
                            report("package.json", f"scripts.{name} 必须是非空命令。")
                            continue
                        for target in re.findall(
                            r"""(?<!\S)["']?((?:\./)?[\w./-]+\.[cm]?js)["']?(?=\s|$|[;&])""",
                            command,
                        ):
                            check_target("package.json", target)
                        for nested in re.findall(r"\bnpm\s+run\s+([\w:.-]+)", command):
                            if not _text(scripts.get(nested)):
                                report("package.json", f"scripts.{name} 引用了未声明的脚本 {nested}。")

    if scripts is not None and "README.md" in files:
        documented = set()
        for prefix, name in README_COMMAND.findall(files["README.md"]):
            if "run" not in prefix.split() and name not in ("start", "test"):
                continue
            documented.add(name)
            if not _text(scripts.get(name)):
                report("README.md", f"文档命令引用了 package.json 中未声明的脚本 {name}。")
        for name in ("start", "test"):
            if name not in documented and _text(scripts.get(name)):
                report("README.md", f"缺少 package.json 中 {name} 脚本对应的独立 npm 命令。")

    for path, content in files.items():
        suffix = PurePosixPath(path).suffix.lower()
        if suffix in (".js", ".mjs", ".cjs"):
            local_modules = {}
            for reference in dict.fromkeys(JS_REFERENCE.findall(content)):
                if reference.startswith(("./", "../")):
                    local_modules[reference] = check_target(path, reference, module=True)
                    continue
                if declared_packages is None or not re.fullmatch(r"(?:@[\w.-]+/)?[\w.-]+(?:/[\w./-]+)*", reference):
                    continue
                parts = reference.split("/")
                package_name = "/".join(parts[:2]) if reference.startswith("@") else parts[0]
                if package_name not in NODE_BUILTINS and package_name not in declared_packages:
                    report("package.json", f"{path} 引用了未声明的 npm 包 {package_name!r}。")
            for bindings, reference in NAMED_REQUIRE.findall(content):
                target = local_modules.get(reference)
                exports = OBJECT_EXPORT.search(files[target]) if target else None
                if exports is None:
                    continue
                exported = {name.strip() for name in exports[1].split(",")}
                for binding in bindings.split(","):
                    name = binding.split(":", 1)[0].strip()
                    if name.isidentifier() and name not in exported:
                        report(target, f"{path} 从 {target} 引用了未导出的 {name!r}。")
        if suffix in (".html", ".htm"):
            for reference in dict.fromkeys(ASSET_REFERENCE.findall(content)):
                check_target(path, reference)
    return errors
