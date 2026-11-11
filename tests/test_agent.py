import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.agent import PlanError, check_project, generate_project, plan_project, validate_plan
from app.deepseek import DeepSeekClient
from app.inputs import load_architecture_inputs
from app.output import publish_project


ROOT = Path(__file__).resolve().parents[1]


def valid_plan():
    return {
        "files": [
            {"path": path, "role": role, "purpose": "项目文件",
             "requirements": [], "dependencies": []}
            for path, role in [
                ("package.json", "file"), ("README.md", "file"),
                ("src/server.js", "entrypoint"), ("tests/server.test.js", "test"),
                ("public/index.html", "file"), ("public/client.js", "file"),
            ]
        ],
        "verification": {
            "answer_fractions": "提交正确答案后得分增加",
            "view_score": "打开结果页显示得分",
            "help": "点击帮助显示答题说明",
            "manage_questions": "新增题目后题库显示该题",
        },
        "assumptions": [
            {"topic": topic, "decision": "本地演示省略", "impact": "缺少生产环境能力"}
            for topic in ("PostgreSQL", "Redis", "Kubernetes")
        ],
    }


def response(content, finish="stop"):
    return SimpleNamespace(choices=[
        SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish),
    ])


class PlanningTests(unittest.TestCase):
    def setUp(self):
        sdk = self.enterContext(patch("app.deepseek.OpenAI"))
        self.create = sdk.return_value.chat.completions.create
        self.client = self.enterContext(DeepSeekClient("test-key"))

    def test_structured_input_and_feedback_reach_sdk(self):
        architecture = load_architecture_inputs(
            ROOT / "Architecture_Documentation.md", ROOT / "Architecture_View.md"
        )
        expected = valid_plan()
        invalid = valid_plan()
        del invalid["verification"]["help"]
        self.create.side_effect = [
            response(json.dumps(invalid)), response(json.dumps(expected)),
        ]
        self.assertEqual(plan_project(self.client, architecture), expected)
        first, second = [call.kwargs for call in self.create.call_args_list]
        self.assertEqual(first["response_format"], {"type": "json_object"})
        self.assertEqual(json.loads(first["messages"][1]["content"]), architecture)
        self.assertIn("verification.help", second["messages"][-1]["content"])

    def test_revisions_end_with_last_validation_error(self):
        self.create.side_effect = [
            response("partial", "length"), response("{"), response(" "),
        ]
        with self.assertRaisesRegex(PlanError, r"(?s)3 次尝试.*空白内容"):
            plan_project(self.client, {})
        self.assertEqual(self.create.call_count, 3)
        self.assertIn("finish_reason=length", self.create.call_args_list[1].kwargs["messages"][-1]["content"])

    def test_path_and_dependency_checks(self):
        plan = valid_plan()
        plan["files"][1]["path"] = "PACKAGE.JSON"
        plan["files"][2]["dependencies"] = ["unknown.js"]
        plan["files"][3]["path"] = "../escape.test.js"
        errors = "\n".join(validate_plan(plan))
        for phrase in ("路径重复", "计划外", "项目相对路径", "README.md"):
            self.assertIn(phrase, errors)

    def test_generate_retry_repair_and_publish(self):
        architecture = load_architecture_inputs(
            ROOT / "Architecture_Documentation.md", ROOT / "Architecture_View.md"
        )
        plan = valid_plan()
        plan["files"][3]["dependencies"] = ["src/server.js"]

        def batch(path, content):
            return response(json.dumps({"files": [{"path": path, "content": content}]}))

        package = json.dumps({
            "main": "src/server.js",
            "scripts": {"start": "node src/server.js", "test": "node --test"},
        })
        self.create.side_effect = [
            batch("package.json", package),
            response(" "),
            response("{"),
            batch("src/server.js", 'import "./missing.js";\napp.get("/", handler);'),
            response(json.dumps({"files": [
                {"path": "public/index.html", "content": '<html><script src="./client.js"></script></html>'},
                {"path": "public/client.js", "content": "// client"},
            ]})),
            batch("tests/server.test.js", 'import "../src/server.js";'),
            batch("README.md", "```sh\nnpm start\nnpm test\n```\n"),
            batch("src/server.js", 'app.get("/", handler);\n'),
        ]
        files = generate_project(self.client, architecture, plan)
        requests = [call.kwargs["messages"] for call in self.create.call_args_list]
        payloads = [json.loads(messages[1]["content"]) for messages in requests]
        self.assertEqual([item["requested_paths"] for item in payloads], [
            ["package.json"], ["src/server.js"], ["src/server.js"], ["src/server.js"],
            ["public/index.html", "public/client.js"],
            ["tests/server.test.js"], ["README.md"], ["src/server.js"],
        ])
        self.assertIn("为空", requests[2][-1]["content"])
        self.assertIn("无法解析", requests[3][-1]["content"])
        self.assertIn("src/server.js", payloads[5]["previous_files"])
        self.assertIn("missing.js", payloads[-1]["feedback"]["src/server.js"][0])

        with TemporaryDirectory(dir=ROOT) as directory:
            first = publish_project(directory, files)
            second = publish_project(directory, files)
            self.assertNotEqual(first, second)
            self.assertEqual((first / "src/server.js").read_text(encoding="utf-8"), files["src/server.js"])
            self.assertEqual(set(Path(directory).iterdir()), {first, second})
            for path in ("../escape.js", "C:/escape.js", "NUL.txt", "file:stream"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    publish_project(directory, {path: "text"})
            with self.assertRaisesRegex(ValueError, "路径重复"):
                publish_project(directory, {"src/a.js": "first", "SRC/A.js": "second"})
            self.assertEqual(set(Path(directory).iterdir()), {first, second})

    def test_acceptance_reports_undeclared_package_and_missing_export(self):
        files = {
            "package.json": '{"scripts":{"start":"node src/server.js","test":"node --test"}}',
            "README.md": "npm start\nnpm test\n",
            "src/server.js": "const express = require('express');\n"
                             "const { getRandomQuestion } = require('./questions');",
            "src/questions.js": "module.exports = { getQuestions };",
            "tests/server.test.js": "const { test } = require('node:test');",
        }
        plan = {"files": [{"path": path} for path in files]}
        feedback = check_project(plan, files)
        self.assertEqual(set(feedback), {"package.json", "src/questions.js"})
        self.assertIn("express", feedback["package.json"][0])
        self.assertIn("getRandomQuestion", feedback["src/questions.js"][0])

    def test_browser_page_is_required_and_must_be_served(self):
        plan = valid_plan()
        plan["files"] = [item for item in plan["files"] if not item["path"].startswith("public/")]
        self.assertIn("public/index.html", "\n".join(validate_plan(plan)))
        plan = valid_plan()
        files = {item["path"]: "// placeholder" for item in plan["files"]}
        files["public/index.html"] = '<html><script src="./client.js"></script></html>'
        files["src/server.js"] = "app.get('/api/help', handler);"
        feedback = check_project(plan, files)
        self.assertIn("缺少首页服务", "\n".join(feedback["src/server.js"]))
