# Task 1 桌面代码智能体

本项目读取两份架构 Markdown，通过 DeepSeek 官方 API 构建 Space Fractions 示例项目。在 Tkinter 窗口输入密钥，选择两份架构文件及输出目录，点击「开始生成」。通过静态检查的项目会发布到新的子目录。完成后，按生成项目的 README 手动启动和测试。

发布窗口显示「静态检查通过，待运行验收」。在生成目录执行 `npm install`、`npm test` 和 `npm start` 后，再验证答题、得分、帮助和题目管理。运行验收中的错误应连同相关文件一起反馈到修订流程。

## 本地开发

在 Windows PowerShell 中进入项目根目录。本机使用 Python 3.13。

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

如果创建虚拟环境时 `ensurepip` 因临时目录权限失败，可在当前 PowerShell 进程中将 `TEMP` 和 `TMP` 指向可写目录后重试。

## 验证 DeepSeek API

在项目根目录的 PowerShell 中运行以下命令。脚本优先读取 `DEEPSEEK_API_KEY`，未设置时在终端隐藏输入。成功时会输出返回模型、耗时及 `OK`。这会向 DeepSeek 发送一次实际请求，并按账号的 API 用量计费。[DeepSeek 官方调用说明](https://api-docs.deepseek.com/en/)

```powershell
.\.venv\Scripts\python.exe debug_deepseek.py
```

## 导出架构输入

运行桌面程序时，通过界面选择老师提供的两份 Markdown。若要执行下列导出命令或运行使用这两份样例的测试，请先将 Architecture_Documentation.md 和 Architecture_View.md 放到项目根目录。

```powershell
.\.venv\Scripts\python.exe -m app.inputs Architecture_Documentation.md Architecture_View.md docs/architecture_inputs.debug.json
```

Agent 接入时可直接调用 `app.inputs.load_architecture_inputs(documentation, view)`，将返回的字典序列化为 JSON 后传给模型。来源行号从 1 开始，起止行均包含在范围内。

## 工程目录

| 路径 | 职责 |
| --- | --- |
| `main.py`、`app/gui.py` | 启动窗口和选择输入 |
| `app/inputs.py` | 解析架构文档和 PlantUML |
| `app/deepseek.py` | 调用 DeepSeek 官方 API |
| `app/agent.py` | 规划、分批生成和修订 |
| `app/output.py` | 校验路径、暂存和发布文件 |
| `tests/` | Agent 测试 |
| [调研记录](docs/research.md) | Claude Code 机制与本作业的实现对照 |
