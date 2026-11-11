"""End-to-end checks for the architecture JSON export command."""

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCUMENTATION = ROOT / "Architecture_Documentation.md"
VIEW = ROOT / "Architecture_View.md"


class ArchitectureExportTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory(dir=ROOT))
        self.output = Path(directory) / "architecture.json"

    def run_export(self, documentation=DOCUMENTATION, view=VIEW):
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "app.inputs",
             str(documentation), str(view), str(self.output)],
            cwd=ROOT,
            capture_output=True,
            encoding="utf-8",
            timeout=15,
        )

    def test_export_contains_traceable_source_text(self):
        result = self.run_export()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(self.output.read_text(encoding="utf-8"))

        sources = {
            path.name: path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
            for path in (DOCUMENTATION, VIEW)
        }
        for collection in ("sections", "contracts", "diagrams"):
            for item in data[collection]:
                with self.subTest(collection=collection, line=item["start_line"]):
                    lines = sources[item["file"]][item["start_line"] - 1:item["end_line"]]
                    self.assertEqual(item["text"], "".join(lines))

        diagrams = {item["name"] for item in data["diagrams"]}
        self.assertTrue({"UseCaseDiagram", "ClassDiagram", "SequenceDiagram1"} <= diagrams)
        requirement = next(item for item in data["requirements"] if "FR-1" in item["ids"])
        self.assertEqual(requirement["diagrams"], "UseCaseDiagram")
        self.assertEqual(requirement["artifacts"], "openapi.yaml")
        openapi, = [item for item in data["contracts"] if item["kind"] == "openapi"]
        appendix, = openapi["references"]
        self.assertEqual((openapi["section"], appendix["section"]), ("D", "L"))
        lines = sources[appendix["file"]][appendix["start_line"] - 1:appendix["end_line"]]
        self.assertEqual("".join(lines), openapi["text"])
        note = next(item for item in data["input_notes"] if item["code"] == "diagram_count_mismatch")
        self.assertEqual((note["declared_count"], note["parsed_count"]), (11, 13))
        self.assertEqual(note["parsed_count"], len(data["diagrams"]))

    def test_swapped_inputs_fail_without_creating_output(self):
        result = self.run_export(VIEW, DOCUMENTATION)
        self.assertEqual(result.returncode, 1)
        self.assertIn(VIEW.name, result.stderr)
        self.assertIn("请选择架构文档", result.stderr)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
