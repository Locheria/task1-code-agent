from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.inputs import InputError, load_architecture_inputs


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTATION = ROOT / "Architecture_Documentation.md"
VIEW = ROOT / "Architecture_View.md"


class ArchitectureInputTests(unittest.TestCase):
    def test_current_inputs_and_source_locations(self):
        data = load_architecture_inputs(DOCUMENTATION, VIEW)
        self.assertEqual([item["id"] for item in data["sections"]], list("ABCDEFGHIJKL"))
        self.assertEqual([row["ids"] for row in data["requirements"]], [["FR-1"], ["NFR-1"], ["ASR-1"]])
        self.assertEqual(data["requirements"][0]["artifacts"], "openapi.yaml")
        self.assertEqual(len(data["diagrams"]), 13)
        diagrams = {item["name"]: item["start_line"] for item in data["diagrams"]}
        for name, line in {"UseCaseDiagram": 4, "ClassDiagram": 24, "SequenceDiagram1": 97}.items():
            self.assertEqual(diagrams[name], line)
        contracts = {item["kind"]: item for item in data["contracts"]}
        self.assertEqual(len(data["contracts"]), 4)
        self.assertEqual(set(contracts), {"openapi", "proto", "sql", "deployment"})
        self.assertEqual((contracts["openapi"]["section"], contracts["openapi"]["start_line"]), ("D", 83))
        self.assertEqual(contracts["openapi"]["references"][0]["start_line"], 242)
        self.assertEqual(data["input_notes"][0]["declared_count"], 11)
        self.assertEqual(data["input_notes"][0]["parsed_count"], 13)

    def test_utf8_bom_and_invalid_inputs(self):
        with TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "test-view.md"
            path.write_text(VIEW.read_text(encoding="utf-8"), encoding="utf-8-sig")
            self.assertEqual(len(load_architecture_inputs(DOCUMENTATION, path)["diagrams"]), 13)
            for content, message in [(b"", "文件为空"), (b"\xff", "编码无效")]:
                with self.subTest(message=message):
                    path.write_bytes(content)
                    with self.assertRaisesRegex(InputError, f"test-view.md.*{message}"):
                        load_architecture_inputs(DOCUMENTATION, path)
        with self.assertRaisesRegex(InputError, "Architecture_View.md.*请选择架构文档"):
            load_architecture_inputs(VIEW, DOCUMENTATION)
        with self.assertRaisesRegex(InputError, "Architecture_Documentation.md.*请选择架构视图"):
            load_architecture_inputs(DOCUMENTATION, DOCUMENTATION)

    def test_reports_unpaired_plantuml_markers(self):
        with TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "broken.md"
            for marker, missing in [("@startuml Test", "@enduml"), ("@enduml", "@startuml")]:
                with self.subTest(marker=marker):
                    path.write_text(f"## View\n```plantuml\n{marker}\n```\n", encoding="utf-8")
                    with self.assertRaisesRegex(InputError, f"broken.md 第 3 行.*缺少配对的 {missing}"):
                        load_architecture_inputs(DOCUMENTATION, path)
