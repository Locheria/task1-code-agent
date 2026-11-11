from datetime import datetime
from pathlib import Path
from queue import Empty, SimpleQueue
from tempfile import TemporaryFile
from threading import Thread
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from app.agent import PlanError, generate_project, plan_project
from app.deepseek import DeepSeekClient, DeepSeekError
from app.inputs import InputError, load_architecture_inputs
from app.output import publish_project


def validate_start_inputs(key: str, documentation: str, view: str, output: str) -> None:
    if not key or any(character.isspace() for character in key):
        raise ValueError("请输入有效格式的 DeepSeek API Key，密钥中不能有空白字符。")
    for label, value in (("架构文档", documentation), ("架构视图", view)):
        if not value:
            raise ValueError(f"请选择{label}。")
        path = Path(value)
        if path.suffix.lower() != ".md" or not path.is_file():
            raise ValueError(f"{label}必须是已存在的 Markdown（.md）文件。")
        try:
            with path.open("rb") as stream:
                if not stream.read(1):
                    raise ValueError(f"{label}为空，请选择包含内容的文件。")
        except OSError:
            raise ValueError(f"无法读取{label}，请检查文件权限或重新选择。") from None
    if Path(documentation).samefile(view):
        raise ValueError("架构文档和架构视图应选择不同的文件。")
    if not output or not Path(output).is_dir():
        raise ValueError("请选择已存在的输出目录。")
    try:
        with TemporaryFile(dir=output) as probe:
            probe.write(b"ok")
            probe.flush()
    except OSError:
        raise ValueError("输出目录无法写入，请检查权限或换一个目录。") from None


def build_window(root: tk.Tk) -> None:
    root.title("Task 1 代码智能体")
    root.minsize(660, 390)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)

    form = ttk.Frame(root, padding=16)
    form.grid(sticky="nsew")
    form.columnconfigure(1, weight=1)
    form.rowconfigure(6, weight=1)

    ttk.Label(form, text="DeepSeek API Key").grid(row=0, column=0, sticky="w")
    key_entry = ttk.Entry(form, show="*")
    key_entry.grid(row=0, column=1, padx=8, pady=4, sticky="ew")

    def add_path_field(row: int, label: str, *, directory: bool = False) -> ttk.Entry:
        ttk.Label(form, text=label).grid(row=row, column=0, sticky="w")
        entry = ttk.Entry(form)
        entry.grid(row=row, column=1, padx=8, pady=4, sticky="ew")

        def browse() -> None:
            if directory:
                selected = filedialog.askdirectory(parent=root, title=label)
            else:
                selected = filedialog.askopenfilename(
                    parent=root,
                    title=label,
                    filetypes=[("Markdown", "*.md")],
                )
            if selected:
                entry.delete(0, tk.END)
                entry.insert(0, str(Path(selected)))

        ttk.Button(form, text="浏览", command=browse).grid(row=row, column=2)
        return entry

    documentation_entry = add_path_field(1, "架构文档")
    view_entry = add_path_field(2, "架构视图")
    output_entry = add_path_field(3, "输出目录", directory=True)

    ttk.Label(form, text="运行日志").grid(row=5, column=0, columnspan=3, pady=(12, 4), sticky="w")
    log = ScrolledText(form, wrap="word", height=8, state="disabled")
    log.grid(row=6, column=0, columnspan=3, sticky="nsew")
    status = tk.StringVar(master=root, value="就绪")
    results = SimpleQueue()

    def append_log(message: str) -> None:
        log.configure(state="normal")
        log.insert("end", f"[{datetime.now():%H:%M:%S}] {message}\n")
        log.see("end")
        log.configure(state="disabled")

    def run_generation(key: str, documentation: str, view: str, output: str) -> None:
        stage = "读取资料"

        def report(next_stage: str, message: str) -> None:
            nonlocal stage
            stage = next_stage
            results.put(("progress", (stage, message.replace(key, "********"))))

        try:
            report("读取资料", "正在解析架构文档和 PlantUML 视图。")
            architecture = load_architecture_inputs(documentation, view)
            report("规划", "正在请求 DeepSeek 生成项目计划并校验。")
            with DeepSeekClient(key) as client:
                plan = plan_project(client, architecture)
                files = generate_project(client, architecture, plan, on_progress=report)
            if any(key in path or key in content for path, content in files.items()):
                raise PlanError("生成文件包含当前 API Key。请检查架构资料是否含有密钥，并重新生成。")
            report("发布", "文件检查已通过，正在写入输出目录。")
            destination = publish_project(output, files)
        except (InputError, DeepSeekError, PlanError, ValueError) as error:
            results.put(("error", (stage, str(error).replace(key, "********"))))
        except OSError:
            results.put(("error", (stage, "无法访问文件或目录，请检查路径、权限和剩余磁盘空间。")))
        except Exception:
            results.put(("error", (stage, "发生意外错误，请检查运行环境后重试。")))
        else:
            lines = [
                f"输出路径：{destination}",
                f"文件数：{len(files)}",
                "静态检查通过，待运行验收：文件清单、依赖声明、入口、本地引用和 README 命令。",
                "请按生成项目的 README 启动服务并运行功能测试。",
                "",
                "架构取舍与影响",
            ]
            lines.extend(
                f"{item['topic']}：{item['decision']}。影响：{item['impact']}"
                for item in plan["assumptions"]
            )
            results.put(("done", (len(files), "\n".join(lines).replace(key, "********"))))

    def poll_result() -> None:
        try:
            state, payload = results.get_nowait()
        except Empty:
            root.after(100, poll_result)
            return
        if state == "progress":
            stage, message = payload
            status.set(stage)
            append_log(f"{stage} · {message}")
            root.after(100, poll_result)
            return
        start_button.state(["!disabled"])
        if state == "error":
            stage, message = payload
            status.set(f"{stage}失败")
            append_log(f"{stage}失败 · {message}")
            messagebox.showerror(f"{stage}失败", message, parent=root)
            return
        count, summary = payload
        status.set(f"完成 · 已发布 {count} 个文件")
        append_log(f"完成 · {summary.splitlines()[0]}")
        window = tk.Toplevel(root)
        window.title("生成结果")
        preview = tk.Text(window, wrap="word", width=84, height=28)
        scrollbar = ttk.Scrollbar(window, command=preview.yview)
        preview.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        preview.pack(side="left", fill="both", expand=True)
        preview.insert("1.0", summary)
        preview.configure(state="disabled")

    def start_generation() -> None:
        key = key_entry.get().strip()
        documentation = documentation_entry.get().strip()
        view = view_entry.get().strip()
        output = output_entry.get().strip()
        try:
            validate_start_inputs(key, documentation, view, output)
        except (OSError, ValueError) as error:
            message = str(error).replace(key, "********") if key else str(error)
            status.set("输入检查失败")
            append_log(f"输入检查失败 · {message}")
            messagebox.showerror("输入检查失败", message, parent=root)
            return
        start_button.state(["disabled"])
        status.set("读取资料")
        Thread(target=run_generation, args=(key, documentation, view, output), daemon=True).start()
        root.after(100, poll_result)

    start_button = ttk.Button(form, text="开始生成", command=start_generation)
    start_button.grid(row=4, column=1, sticky="e")
    ttk.Label(form, textvariable=status).grid(
        row=7, column=0, columnspan=3, pady=(8, 0), sticky="ew"
    )
