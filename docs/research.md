# Claude Code 工作机制调研与 Task 1 实现对照

查阅日期为 2026 年 11 月 11 日。本次查阅覆盖三个仓库的公开 README、相关源码和文档。下文对 Task 1 的描述是实现设计，运行结果留待开发后验证。

## 三个仓库的查阅记录

### Claw Code

仓库 README 将 `rust/` 标为 Claw CLI 的主要实现目录。`rust/README.md` 的 crate 说明与 `runtime/src/lib.rs` 的导出对应。运行层负责组织提示词、管理会话和驱动循环，同时暴露 API 客户端和工具执行接口。作业可以借鉴这种职责划分，在 `app/agent.py` 维护任务状态，由 API 模块发请求，由输出模块写文件。[仓库 README](https://github.com/ultraworkers/claw-code#readme) · [Rust crate 说明](https://github.com/ultraworkers/claw-code/blob/main/rust/README.md) · [运行层入口源码](https://github.com/ultraworkers/claw-code/blob/main/rust/crates/runtime/src/lib.rs)

在 `conversation.rs` 中，工具结果包含调用标识、工具名、输出和错误状态。运行层把结果写入会话并记录事件，使下一轮可根据实际结果继续。作业可把校验结果组织成带文件路径、检查项和错误原因的记录，既供模型修订，也供界面显示进度。[结果写入会话的源码](https://github.com/ultraworkers/claw-code/blob/main/rust/crates/runtime/src/conversation.rs#L772-L792)

### How Claude Code Works

这是第三方机制解读。第 2 章第 2.1 和 2.2 节把交互拆成会话层和单次查询循环。文档用 `QueryEngine.submitMessage()` 表示接收和组织用户输入的入口，用 `query()` 表示调用模型、执行动作并决定继续或停止的内部循环。Task 1 可用一次点击建立一个任务，内部独立维护本轮文件清单、当前批次和检查反馈。模型根据反馈修订该批文件，编排器负责轮数上限和任务最终状态。[仓库 README](https://github.com/Windy3f3f3f3f/how-claude-code-works#readme) · [第 2 章 系统主循环](https://github.com/Windy3f3f3f3f/how-claude-code-works/blob/main/docs/02-agent-loop.md)

### Anthropic Claude Code

官方仓库 README 介绍了产品和使用入口。官方工作原理页给出三个阶段，并用修复失败测试说明阶段可以反复进行。测试输出先帮助定位问题，工具修改代码后再次运行测试，结果进入后续决策。本作业采用相同的反馈思路。运行时先校验输出文件，有具体错误就请求修订。生成项目的功能测试和 Windows EXE 验收由开发者实际执行并记录结果。[官方仓库](https://github.com/anthropics/claude-code) · [官方工作原理](https://code.claude.com/docs/en/how-claude-code-works#the-agentic-loop)

## 机制对照表

| 阶段 | 参考机制与依据 | Task 1 实现 | 阶段验收 |
| --- | --- | --- | --- |
| 收集上下文 | Claude Code 按任务搜索和读取相关文件。第三方解读把上下文组装放在模型决策之前。[官方工作原理](https://code.claude.com/docs/en/how-claude-code-works#the-agentic-loop) · [系统主循环](https://github.com/Windy3f3f3f3f/how-claude-code-works/blob/main/docs/02-agent-loop.md) | 读取 课程提供的架构文档 与 架构视图，提取需求编号、接口契约和 PlantUML 块，保留文件名和行号。当前资料中，文档开头写了 11 张图，视图文件实际有 13 对图边界。把差异写入 `input_notes`，并保留全部 13 个块。 | 结构化输入能定位 `FR-1`、OpenAPI 片段和 `UseCaseDiagram`。 |
| 采取行动 | Claw Code 的运行层连接模型请求、工具执行和会话状态。第三方解读将会话入口和循环内的动作分开。[Claw Code 运行层源码](https://github.com/ultraworkers/claw-code/blob/main/rust/crates/runtime/src/lib.rs) · [系统主循环](https://github.com/Windy3f3f3f3f/how-claude-code-works/blob/main/docs/02-agent-loop.md) | 用 DeepSeek 官方 API 的 `deepseek-v4-pro` 生成文件计划，逐项记录路径、职责、关联需求和依赖。先生成依赖声明和接口，再生成题库与评分、网页、测试和 README。每批带上相关架构依据和前序接口，程序校验返回结构后写入暂存目录。 | 文件计划可解析，批次路径属于计划，且覆盖源码、`package.json`、README 和测试。 |
| 验证结果 | 官方示例用测试结果驱动下一轮修改。Claw Code 源码把工具输出和错误状态追加到会话。[官方工具示例](https://code.claude.com/docs/en/how-claude-code-works#tools) · [Claw Code 工具结果源码](https://github.com/ultraworkers/claw-code/blob/main/rust/crates/runtime/src/conversation.rs#L772-L792) | 检查文件清单、输出路径、依赖声明、入口和本地引用。把有问题的文件、检查项和原因反馈给模型，要求仅修订相关文件，然后复查。达到修订上限时记录剩余问题。检查通过后发布生成目录。 | 用模拟响应复现一次校验失败和修订通过，再用真实输入生成项目，手动运行项目测试并验证答题、计分等功能。 |

## 一次可复现的修订验证

用模拟模型响应生成 `package.json`，其中启动脚本指向 `src/index.js`，文件计划及实际输出使用 `src/server.js`。检查器应报告入口不存在，把 `package.json`、`src/index.js` 和计划中的 `src/server.js` 作为证据反馈给模型。模拟下一轮返回修正后的 `package.json`，复查启动入口与全量清单，确认其他文件内容保持一致。记录初次失败、修订和复查的测试结果。
